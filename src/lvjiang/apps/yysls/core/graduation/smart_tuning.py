"""智能调律的「最大可能毕业率」判定。

本模块只做纯计算：读取备战方案快照，把当前调律装备替换到
对应部位，将剩余普通词条补满，再把替换前后的整套装备统一按
满等级、满承音、满定音推演并比较。它不修改背包、备战方案或
原始装备数据。
"""
from __future__ import annotations

import copy
import time
from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum
from typing import Callable

from loguru import logger

from ...config import get_game_config
from ...config.tune_slots import SLOT_LABELS
from ..affix_cap import affix_cap_value
from ..combat.affix_rules import normal_affix_candidates
from ..combat.combat_attrs import CombatAttributes
from ..equip_validator import validate_combination_dict
from ..loadout import EQUIPMENT_SLOTS, LoadoutRepository
from ..loadout.models import COMBAT_TYPE_PVP
from ..loadout.transmute import transmute_pool_union, transmute_targets
from ..tuning_rules import (
    PART_ALIAS,
    SmartTuningConfig,
    TuningRule,
    dynamic_affix_map,
    get_tuning_rule_manager,
)
from .assumptions import Assumptions
from .context import PlanContextError, PlanScoringContext
from .scoring import LoadoutScorer
from .smart_search import (
    SearchBudget,
    SearchOutcome,
    SearchProblem,
    SearchStatus,
    SearchStrategy,
    get_strategy,
)

#: 单方案单次判断的搜索预算（秒）；暂停时间不计入
_MAX_PLAN_SECONDS = 5.0
#: 武器部位：候选武器按类型匹配方案里的主/副武器，不是固定替换 main_weapon
_WEAPON_SLOTS = ("main_weapon", "sub_weapon")


class SmartTuningStatus(str, Enum):
    """智能判定结果。"""

    IMPROVES = "improves"
    NO_IMPROVEMENT = "no_improvement"
    UNKNOWN = "unknown"
    #: 仅用于单方案：这件装备装不进该方案（武器类型不匹配），不参与聚合
    NOT_APPLICABLE = "not_applicable"


@dataclass(frozen=True)
class SmartPlanResult:
    plan_id: str
    plan_name: str
    status: SmartTuningStatus
    baseline_rate: float | None = None
    maximum_rate: float | None = None
    reason: str = ""
    evaluated_combinations: int = 0
    plan_maximum_rate: float | None = None
    without_slot_rate: float | None = None
    rule_name: str = ""
    playstyle: str = ""


@dataclass(frozen=True)
class SmartTuningResult:
    status: SmartTuningStatus
    reason: str
    plans: tuple[SmartPlanResult, ...] = ()


@dataclass(frozen=True)
class _PlanContext:
    plan_id: str
    plan_name: str
    school: str
    calculator: object
    base_attrs: CombatAttributes
    equipped: dict[str, dict]
    baseline_rate: float
    rule_key: str = ""
    rule_name: str = ""
    affix_pool: tuple[str, ...] = ()
    attribute: str = ""
    playstyle: str = ""
    plan_maximum_rate: float | None = None
    first_affixes: dict[str, tuple[str, ...]] | None = None
    #: 评分内核（含签名缓存）；未注入时按需构造
    scorer: LoadoutScorer | None = None

    def get_scorer(self, game_config) -> LoadoutScorer:
        if self.scorer is not None:
            return self.scorer
        return LoadoutScorer(
            self.calculator, self.base_attrs, self.school, game_config)


@dataclass(frozen=True)
class _SelectedTarget:
    rule_key: str
    rule_name: str
    playstyle: str
    affix_pool: tuple[str, ...]


def _rate(context: _PlanContext, equipped: dict[str, dict], game_config) -> float:
    return context.get_scorer(game_config).rate(equipped)


class SmartTuningEvaluator:
    """一次调律运行共享的备战方案快照与计算缓存。"""

    def __init__(
        self,
        config: SmartTuningConfig,
        *,
        username: str,
        incoming_rule_configs: dict | None,
        users_dir=None,
        stop_check: Callable[[], bool] | None = None,
        strategy: SearchStrategy | None = None,
        state=None,
        rules: dict[str, TuningRule] | None = None,
        game_config=None,
    ) -> None:
        """``state``（``LoadoutState``）与 ``rules`` 可由调用方注入；不注入时
        才自己读备战方案仓储与规则管理器。评估器本身不再是仓储的读者。"""
        self.config = config
        self._stop_check = stop_check or (lambda: False)
        self._strategy = strategy or get_strategy()
        self._game_config = game_config or get_game_config()
        self._injected_rules = rules
        self._contexts: tuple[_PlanContext, ...] = ()
        self._plan_infos: list[dict] = []
        self._disabled_reason = ""
        # 其余七件的三满投影快照，按 (方案, 槽) 缓存
        self._other_equipped_cache: dict[tuple[str, str], dict[str, dict]] = {}
        if config.evaluation.enabled:
            self._contexts = self._load_contexts(
                username, incoming_rule_configs or {}, users_dir, state)

    @property
    def rules(self) -> dict[str, TuningRule]:
        """调律规则表：优先用调用方注入的，否则按需向规则管理器取一次。"""
        injected = getattr(self, "_injected_rules", None)
        if injected is not None:
            return injected
        return get_tuning_rule_manager().get_rules()

    @property
    def active(self) -> bool:
        return bool(self._contexts) and not self._disabled_reason

    @property
    def disabled_reason(self) -> str:
        return self._disabled_reason

    @property
    def plan_infos(self) -> tuple[dict, ...]:
        """初始化阶段的方案清单，包含被排除方案及明确原因。"""
        return tuple(copy.deepcopy(getattr(self, "_plan_infos", ())))

    def _remember_plan(
        self, target: _SelectedTarget, *, plan_id: str = "",
        plan_name: str = "", status: str, reason: str = "",
        plan_maximum_rate: float | None = None,
        baseline_rate: float | None = None,
    ) -> None:
        self._plan_infos.append({
            "plan_id": plan_id,
            "plan_name": plan_name or f"{target.rule_name}/{target.playstyle}",
            "rule_name": target.rule_name,
            "playstyle": target.playstyle,
            "status": status,
            "reason": reason,
            "plan_maximum_rate": plan_maximum_rate,
            "baseline_rate": baseline_rate,
        })

    def _selected_targets(self, incoming: dict) -> tuple[_SelectedTarget, ...]:
        rules = self.rules
        selected: list[_SelectedTarget] = []
        source: Iterable[tuple[str, TuningRule, list[str]]]
        if self.config.plan_scope == "all":
            source = ((key, rule, list(rule.playstyles))
                      for key, rule in rules.items())
        else:
            rows: list[tuple[str, TuningRule, list[str]]] = []
            for key, cfg in incoming.items():
                rule = rules.get(key)
                if rule is None or not isinstance(cfg, dict):
                    continue
                configured = cfg.get("playstyles")
                # 与规则判定器保持一致：缺失或空列表都表示全部玩法。
                names = (configured if isinstance(configured, list) else None)
                rows.append((key, rule, names or list(rule.playstyles)))
            source = rows
        for key, rule, names in source:
            selected.extend(
                _SelectedTarget(
                    key, rule.name, name, tuple(rule.affix_pool))
                for name in names if name in rule.playstyles)
        return tuple(dict.fromkeys(selected))

    def _selected_playstyles(self, incoming: dict) -> set[str]:
        """返回去重玩法集合，保留给诊断与兼容调用。"""
        return {target.playstyle for target in self._selected_targets(incoming)}

    def _load_contexts(
        self, username: str, incoming: dict, users_dir, state=None,
    ) -> tuple[_PlanContext, ...]:
        if not username:
            self._disabled_reason = "未获取到调律用户"
            return ()
        targets = self._selected_targets(incoming)
        if not targets:
            self._disabled_reason = "当前调律玩法没有可匹配的备战方案"
            return ()
        if state is None:
            try:
                state = LoadoutRepository(username, users_dir).load()
            except Exception as exc:  # noqa: BLE001 - 失败放行
                self._disabled_reason = f"读取备战方案失败: {exc}"
                return ()

        schools = self._game_config.get_schools()
        rules = self.rules
        contexts: dict[tuple[str, str], _PlanContext] = {}
        for target in targets:
            tuning_rule = rules.get(target.rule_key)
            matched = [plan for plan in state.plans.values()
                       if plan.playstyle == target.playstyle]
            # 目前没有 PVP 调律方案。把 PVP 方案一起加载，会让大量够不到 PVE
            # 标准的装备因为 PVP 方案基线低而被判成有提升。
            plans = [plan for plan in matched
                     if plan.combat_type != COMBAT_TYPE_PVP]
            label = f"规则「{target.rule_name}」-玩法「{target.playstyle}」"
            for plan in matched:
                if plan.combat_type != COMBAT_TYPE_PVP:
                    continue
                # 不能静默丢掉：只有 PVP 方案的用户否则只会看到「没有匹配的
                # 备战方案」，完全不知道发生了什么。
                self._remember_plan(
                    target, plan_id=plan.id, plan_name=plan.name,
                    status="skipped",
                    reason="PVP 方案不参与智能调律")
            if not plans:
                logger.warning(f"智能调律忽略{label}：没有匹配的备战方案")
                self._remember_plan(
                    target, status="missing",
                    reason="没有匹配的备战方案，智能调律不启用")
                continue
            valid = False
            missing_graduation = False
            missing_equipment = False
            for plan in plans:
                context_key = (plan.id, target.rule_key)
                if context_key in contexts:
                    valid = True
                    continue
                available = self._game_config.get_playstyles_for_arts(
                    [plan.main_martial_art, plan.sub_martial_art])
                if plan.playstyle not in available:
                    reason = "玩法与武学不匹配，智能调律不启用"
                    logger.warning(
                        f"智能调律忽略备战方案「{plan.name}」：{reason}")
                    self._remember_plan(
                        target, plan_id=plan.id, plan_name=plan.name,
                        status="invalid", reason=reason)
                    continue
                try:
                    scoring = PlanScoringContext.from_plan(
                        plan, game_config=self._game_config, schools=schools)
                except PlanContextError as exc:
                    missing_graduation = True
                    reason = f"缺少毕业率方案，智能调律不启用（{exc.reason}）"
                    logger.warning(
                        f"智能调律忽略备战方案「{plan.name}」："
                        f"{reason}")
                    self._remember_plan(
                        target, plan_id=plan.id, plan_name=plan.name,
                        status="invalid", reason=reason)
                    continue
                school = scoring.school
                equipped = state.resolved_equipment(plan.id)
                if set(equipped) != set(EQUIPMENT_SLOTS):
                    missing_equipment = True
                    reason = (
                        f"备战方案不完整（{len(equipped)}/8），智能调律不启用")
                    logger.warning(
                        f"智能调律忽略备战方案「{plan.name}」：{reason}，"
                        "请先扫描一次已穿戴装备")
                    self._remember_plan(
                        target, plan_id=plan.id, plan_name=plan.name,
                        status="incomplete", reason=reason)
                    continue
                try:
                    # 弓玦固定为方案已选套装；不假设用户会为一件装备换弓玦
                    scorer = scoring.scorer(game_config=self._game_config)
                    provisional = _PlanContext(
                        plan.id, plan.name, school, scoring.calculator,
                        scoring.base_attrs,
                        copy.deepcopy(equipped), 0.0,
                        target.rule_key, target.rule_name,
                        target.affix_pool,
                        scoring.attribute,
                        plan.playstyle,
                        scorer=scorer)
                    baseline = _rate(provisional, provisional.equipped,
                                     self._game_config)
                    try:
                        # 方案基准与候选经过同一个三满入口。同等级承音会
                        # 创造平行装备分支，只属于最优组合的显式搜索选项，
                        # 不能改变备战方案或智能调律的静态理论值。
                        maximum_equipped = self._apply_maximum_assumptions(
                            provisional, provisional.equipped)
                        plan_maximum = _rate(
                            provisional, maximum_equipped, self._game_config)
                    except Exception:  # noqa: BLE001 - 展示指标不影响核心判定
                        logger.warning(
                            f"智能调律无法计算方案上限（{plan.name}）")
                        plan_maximum = None
                    first_affixes = {
                        slot: tuple(pattern.first)
                        for slot, label_name in SLOT_LABELS.items()
                        if tuning_rule is not None
                        and (pattern := tuning_rule.patterns.get(
                            PART_ALIAS.get(label_name, label_name))) is not None
                    }
                    contexts[context_key] = _PlanContext(
                        plan.id, plan.name, school, scoring.calculator,
                        scoring.base_attrs,
                        provisional.equipped, baseline,
                        target.rule_key, target.rule_name,
                        target.affix_pool,
                        provisional.attribute,
                        provisional.playstyle,
                        plan_maximum,
                        first_affixes,
                        scorer=scorer)
                    self._remember_plan(
                        target, plan_id=plan.id, plan_name=plan.name,
                        status="ready", plan_maximum_rate=plan_maximum,
                        baseline_rate=baseline)
                    valid = True
                except Exception as exc:  # noqa: BLE001 - 单方案隔离
                    reason = f"初始化失败：{exc}"
                    logger.warning(
                        f"智能调律忽略备战方案「{plan.name}」：{reason}")
                    self._remember_plan(
                        target, plan_id=plan.id, plan_name=plan.name,
                        status="invalid", reason=reason)
            if not valid and missing_graduation:
                logger.warning(f"智能调律忽略{label}：没有可用的毕业率方案")
            if not valid and missing_equipment:
                logger.warning(f"智能调律忽略{label}：备战方案穿戴装备均不完整")
        if not contexts:
            self._disabled_reason = "没有可靠的备战方案可用于毕业率判定"
        return tuple(contexts.values())

    def evaluate(self, slot: str, equipment: dict) -> SmartTuningResult:
        """判定当前装备的合法承音上限是否能提升任一方案。"""
        started = time.perf_counter()
        if not self.config.evaluation.enabled:
            return SmartTuningResult(SmartTuningStatus.UNKNOWN, "智能判定未启用")
        if not self.active:
            return SmartTuningResult(
                SmartTuningStatus.UNKNOWN,
                self._disabled_reason or "智能调律未就绪")
        if slot not in EQUIPMENT_SLOTS:
            return SmartTuningResult(
                SmartTuningStatus.UNKNOWN, f"无法识别装备部位: {slot}")
        results: list[SmartPlanResult] = []
        for context in self._contexts:
            if self._stop_check():
                return SmartTuningResult(
                    SmartTuningStatus.UNKNOWN, "用户中断智能调律计算",
                    tuple(results))
            candidate_slots = self._candidate_slots(context, slot, equipment)
            if not candidate_slots:
                results.append(SmartPlanResult(
                    context.plan_id, context.plan_name,
                    SmartTuningStatus.NOT_APPLICABLE, context.baseline_rate,
                    reason="当前装备不适用该方案：武器类型不符合",
                    plan_maximum_rate=context.plan_maximum_rate,
                    rule_name=context.rule_name,
                    playstyle=context.playstyle))
                continue
            slot_results: list[SmartPlanResult] = []
            for candidate_slot in candidate_slots:
                reason = self._not_applicable_reason(
                    context, candidate_slot, equipment)
                if reason:
                    slot_results.append(SmartPlanResult(
                        context.plan_id, context.plan_name,
                        SmartTuningStatus.NOT_APPLICABLE,
                        context.baseline_rate,
                        reason=reason,
                        plan_maximum_rate=context.plan_maximum_rate,
                        rule_name=context.rule_name,
                        playstyle=context.playstyle))
                    continue
                result = self._evaluate_plan(
                    context, candidate_slot, equipment)
                slot_results.append(result)
            results.append(self._best_slot_result(slot_results))
        # 装不进去的方案（武器类型不匹配）既不是通过也不是失败，不参与聚合
        applicable = [r for r in results
                      if r.status is not SmartTuningStatus.NOT_APPLICABLE]
        if not applicable:
            logger.debug(
                "智能调律判断点总耗时 "
                f"{(time.perf_counter() - started) * 1000:.1f}ms")
            return SmartTuningResult(
                SmartTuningStatus.UNKNOWN,
                "没有备战方案能装备这件武器，已放行",
                tuple(results))
        improving = [r for r in applicable
                     if r.status is SmartTuningStatus.IMPROVES]
        reliable = [r for r in applicable
                    if r.status is not SmartTuningStatus.UNKNOWN]
        if improving:
            outcome = SmartTuningResult(
                SmartTuningStatus.IMPROVES,
                "可提升：" + "、".join(item.plan_name for item in improving),
                tuple(results))
        elif reliable and len(reliable) == len(applicable):
            outcome = SmartTuningResult(
                SmartTuningStatus.NO_IMPROVEMENT,
                "所有匹配的备战方案均已确定无法提升",
                tuple(results))
        else:
            outcome = SmartTuningResult(
                SmartTuningStatus.UNKNOWN,
                "存在无法完成判定的备战方案，为避免误处理已放行",
                tuple(results))
        logger.debug(
            "智能调律判断点总耗时 "
            f"{(time.perf_counter() - started) * 1000:.1f}ms")
        return outcome

    @staticmethod
    def _best_slot_result(results: list[SmartPlanResult]) -> SmartPlanResult:
        """同类型武器占据两个槽位时，按可靠性和候选上限合并为一条方案结果。"""
        if not results:
            raise ValueError("方案至少应产生一个槽位结果")
        rank = {
            SmartTuningStatus.IMPROVES: 3,
            SmartTuningStatus.NO_IMPROVEMENT: 2,
            SmartTuningStatus.UNKNOWN: 1,
            SmartTuningStatus.NOT_APPLICABLE: 0,
        }
        return max(
            results,
            key=lambda item: (
                rank[item.status],
                item.maximum_rate if item.maximum_rate is not None else -1.0,
            ),
        )

    @staticmethod
    def _not_applicable_reason(
        context: _PlanContext, slot: str, equipment: dict,
    ) -> str:
        """返回当前装备不适用该方案的明确原因；空串表示可参与。"""
        if slot in _WEAPON_SLOTS:
            return ""
        patterns = getattr(context, "first_affixes", None)
        if patterns is None:  # 兼容测试/外部策略手工构造的旧上下文
            return ""
        allowed = patterns.get(slot)
        if not allowed:
            return "当前装备不适用该方案：当前部位没有对应调律规则"
        first = equipment.get("affix_1")
        first_name = str(first.get("name") or "") if isinstance(first, dict) else ""
        aliases = dynamic_affix_map(context.attribute)
        identities = {first_name}
        if first_name in aliases:
            identities.add(aliases[first_name])
        if not first_name or not identities.intersection(allowed):
            return "当前装备不适用该方案：首词条不符合"
        return ""

    @staticmethod
    def _candidate_slots(
        context: _PlanContext, slot: str, equipment: dict,
    ) -> tuple[str, ...]:
        """这件装备在该方案里可以替换哪些部位。

        自动调律把全部武器都放在 main_weapon 槽遍历，但方案里主/副武器类型
        可能不同，也可能相同。按类型匹配全部可用槽位；均不匹配时该方案
        不适用。非武器部位一一对应。
        """
        if slot not in _WEAPON_SLOTS:
            return (slot,)
        equip_type = str(equipment.get("type") or "")
        if not equip_type:
            return ()
        return tuple(
            candidate_slot for candidate_slot in _WEAPON_SLOTS
            if str((context.equipped.get(candidate_slot) or {}).get("type") or "")
            == equip_type
        )

    def _evaluate_plan(
        self, context: _PlanContext, slot: str, equipment: dict,
    ) -> SmartPlanResult:
        """单个方案的评估编排：校验 → 枚举转律分支 → 逐分支搜索 → 汇总。

        任何异常都失败放行（UNKNOWN），不阻断调律流程。
        """
        started = time.perf_counter()
        without_slot_rate = self._without_slot_rate(context, slot)

        def make_result(
            status: SmartTuningStatus, reason: str,
            maximum_rate: float | None = None,
            evaluated: int = 0,
        ) -> SmartPlanResult:
            return SmartPlanResult(
                plan_id=context.plan_id,
                plan_name=context.plan_name,
                status=status,
                baseline_rate=context.baseline_rate,
                maximum_rate=maximum_rate,
                reason=reason,
                evaluated_combinations=evaluated,
                plan_maximum_rate=context.plan_maximum_rate,
                without_slot_rate=without_slot_rate,
                rule_name=context.rule_name,
                playstyle=context.playstyle,
            )

        try:
            if context.plan_maximum_rate is None:
                return make_result(
                    SmartTuningStatus.UNKNOWN,
                    "备战方案极限毕业率计算失败，无法可靠比较")
            candidate = copy.deepcopy(equipment)
            problem_reason = self._candidate_problem(candidate)
            if problem_reason:
                return make_result(SmartTuningStatus.UNKNOWN, problem_reason)

            branches = self._transmute_branches(context, candidate)
            budget = SearchBudget(_MAX_PLAN_SECONDS, self._stop_check)
            searched: list[tuple[str, SearchOutcome, tuple[str, ...]]] = []
            for branch_label, branch in branches:
                row = self._search_branch(
                    context, slot, branch_label, branch, budget)
                if row is not None:
                    searched.append(row)

            if not searched:
                return make_result(
                    SmartTuningStatus.UNKNOWN, "没有可用的合法补全分支")
            status, reason, maximum_rate, evaluated = self._summarize(searched)
            return make_result(status, reason, maximum_rate, evaluated)
        except Exception as exc:  # noqa: BLE001 - 任何异常必须失败放行
            logger.exception(f"智能调律计算失败（{context.plan_name}）")
            return make_result(
                SmartTuningStatus.UNKNOWN, f"计算异常: {exc}")
        finally:
            logger.debug(
                f"智能调律方案计算 {context.plan_name}: "
                f"{(time.perf_counter() - started) * 1000:.1f}ms")

    def _without_slot_rate(
        self, context: _PlanContext, slot: str,
    ) -> float | None:
        """其余七件按三满投影后的毕业率（展示性指标，失败为 None）。"""
        try:
            without_slot = {
                key: value for key, value in context.equipped.items()
                if key != slot
            }
            without_slot = self._apply_maximum_assumptions(
                context, without_slot)
            return _rate(context, without_slot, self._game_config)
        except Exception:  # noqa: BLE001 - 展示性指标不应阻断核心判定
            logger.debug(
                f"智能调律无法计算七件基线（{context.plan_name}）",
                exc_info=True)
            return None

    @staticmethod
    def _candidate_problem(candidate: dict) -> str:
        """候选装备无法可靠评估时返回原因，可评估返回空串。

        检查顺序即原因优先级：基础字段 → 词条数据 → 组合合法性 → 是否已满。
        """
        if (not candidate.get("type") or not candidate.get("level")
                or not candidate.get("quality")):
            return "当前装备缺少类型、等级或品阶"
        for index in range(1, 6):
            affix = candidate.get(f"affix_{index}")
            if affix is None:
                continue
            value = affix.get("value") if isinstance(affix, dict) else None
            if (not isinstance(affix, dict) or not affix.get("name")
                    or isinstance(value, bool)
                    or not isinstance(value, (int, float)) or value <= 0):
                return f"当前装备第 {index} 条词条数据不完整"
        flaws = validate_combination_dict(candidate)
        if flaws:
            return "当前装备组合不合法：" + "；".join(str(item) for item in flaws)
        affix_count = sum(
            isinstance(candidate.get(f"affix_{i}"), dict) for i in range(1, 6))
        if affix_count >= 5:
            # 五词条已经进入调律处理的终局判定，不再假设未来转律；
            # 生产流程会在调用本评估器前结束，这里仍作防御性拦截。
            return "词条已满，应由调律处理执行终局判定"
        return ""

    def _transmute_branches(
        self, context: _PlanContext, candidate: dict,
    ) -> list[tuple[str, dict]]:
        """第 0 分支始终保留原词条；额外分支把一次未来转律表达为“把一个
        已出现的第 2~4 词条转成某个转律库词条，其余空槽再交给同一套补全
        算法”。转入词条必须是游戏里真能转出来的：各流派转律词条库的并集
        ∩ 规则池 ∩ 部位可出现；空槽由调律补全，不受转律库限制。当前能进入
        自动调律的装备不可能已有转律词条，无需再次转律状态机。"""
        pool = set(context.affix_pool)
        aliases = dynamic_affix_map(context.attribute)
        affix_count = sum(
            isinstance(candidate.get(f"affix_{i}"), dict) for i in range(1, 6))
        branches: list[tuple[str, dict]] = [("", candidate)]
        if not 2 <= affix_count <= 4:
            return branches
        removable: list[tuple[int, str]] = []
        outside_pool: list[tuple[int, str]] = []
        for index in range(2, min(affix_count, 4) + 1):
            affix = candidate.get(f"affix_{index}")
            if not isinstance(affix, dict):
                continue
            name = str(affix.get("name") or "")
            removable.append((index, name))
            if name not in pool and aliases.get(name) not in pool:
                outside_pool.append((index, name))
        # 有池外词条时只处理第一条；否则逐一尝试转出全部已出现的
        # 非首词条。一次转律最多改一条。
        selected = outside_pool[:1] or removable
        # 智能调律口径：各流派转律库并集 ∩ 本规则词条库（含动态本属
        # 别名展开）；部位合法性、去重与整件校验由公共过滤链完成。
        rule_pool = [
            target
            for target in transmute_pool_union(self._game_config)
            if target in pool or aliases.get(target) in pool
        ]
        for index, name in selected:
            for target in transmute_targets(
                    candidate, index, rule_pool, self._game_config):
                variant = copy.deepcopy(candidate)
                variant.pop(f"affix_{index}", None)
                # 被转出的槽在 2~4，是当前第一个空槽，转入词条落回原位。
                filled = self._with_affixes(variant, (target,))
                if filled is None:
                    continue
                branches.append((
                    f"转律假设：第 {index} 条「{name}」转为「{target}」",
                    filled))
        return branches

    def _search_branch(
        self, context: _PlanContext, slot: str,
        branch_label: str, branch: dict, budget: SearchBudget,
    ) -> tuple[str, SearchOutcome, tuple[str, ...]] | None:
        """对一个分支做补全搜索；返回 None 表示该分支不可行、不参与汇总。

        原分支（``branch_label`` 为空）的不可行会作为结论保留，转律分支的
        不可行只是这个假设不成立，不污染其他分支。
        """
        # _evaluate_plan 已在 plan_maximum_rate 为 None 时提前返回
        assert context.plan_maximum_rate is not None
        pool = set(context.affix_pool)
        aliases = dynamic_affix_map(context.attribute)
        branch_present = [branch.get(f"affix_{i}") for i in range(1, 6)]
        missing = 5 - sum(isinstance(item, dict) for item in branch_present)
        forced_names: tuple[str, ...] = ()

        # 智能调律必须服从触发它的规则词条库。部位合法池只负责
        # 物理可出现性，规则池决定该玩法实际允许拿什么来推演。
        legal = (normal_affix_candidates(branch, self._game_config)
                 if missing > 0 else [])
        candidates = list(dict.fromkeys(
            name for name in legal
            if name in pool or aliases.get(name) in pool))

        required = self._required_affix(context, slot, branch)
        present_names = {
            str(item.get("name") or "")
            for item in branch_present if isinstance(item, dict)
        }
        if required and required not in present_names:
            # 增伤词条不参与“是否允许转出”的特殊保护；移除后仍按
            # 最终玩法约束补回，与其他空槽一起进入理论极限计算。
            if required not in legal:
                return (
                    branch_label,
                    SearchOutcome(
                        SearchStatus.UNKNOWN,
                        f"玩法「{context.playstyle}」要求词条"
                        f"「{required}」，但当前部位不允许"),
                    (),
                )
            completed = self._with_affixes(branch, (required,))
            if completed is None:
                # 这个转律分支本身不合法，不污染其他可行分支。
                if branch_label:
                    return None
                return (
                    branch_label,
                    SearchOutcome(
                        SearchStatus.NO_IMPROVEMENT,
                        f"当前词条组合无法再加入玩法必需词条"
                        f"「{required}」"),
                    (),
                )
            branch = completed
            if required in candidates:
                candidates.remove(required)
            missing -= 1
            forced_names = (required,)
        if missing > 0 and not candidates:
            if branch_label:
                # 移除后连五条都补不满，说明该转律假设不可行；它不是
                # “计算不确定”，不应阻止其他完整分支给出结论。
                return None
            return (
                branch_label,
                SearchOutcome(
                    SearchStatus.UNKNOWN,
                    f"规则「{context.rule_name or context.rule_key}」"
                    "在当前部位没有可用词条"),
                forced_names,
            )
        if branch_label and len(candidates) < missing:
            # 候选名不足时必然无法形成完整装备，提前剔除该分支，
            # 避免策略层把“不可行”误报成需要放行的 UNKNOWN。
            return None

        problem = SearchProblem(
            equipment=branch,
            candidates=candidates,
            missing=missing,
            # 必须用相同的三满假设比较。拿候选极限与方案当前实值
            # 相比，会把用户以后仍可完成的培养错误算成候选收益。
            baseline=context.plan_maximum_rate,
            operator=self.config.evaluation.operator,
            complete=self._with_affixes,
            rate=lambda equip: self._candidate_rate(context, slot, equip),
            budget=budget,
            precision=self.config.evaluation.precision,
        )
        outcome = self._strategy.search(problem)
        return (branch_label, outcome, (*forced_names, *outcome.winning_affixes))

    @staticmethod
    def _summarize(
        searched: list[tuple[str, SearchOutcome, tuple[str, ...]]],
    ) -> tuple[SmartTuningStatus, str, float | None, int]:
        """多分支汇总：有提升取提升里最高；否则只要有分支未算完就 UNKNOWN。"""
        improving = [row for row in searched
                     if row[1].status is SearchStatus.IMPROVES]
        unknown = [row for row in searched
                   if row[1].status is SearchStatus.UNKNOWN]
        known = [row for row in searched
                 if row[1].maximum_rate is not None]
        best_pool = improving or known or searched
        best_label, best, winning = max(
            best_pool,
            key=lambda row: (row[1].maximum_rate
                             if row[1].maximum_rate is not None
                             else -1.0),
        )
        if improving:
            status = SmartTuningStatus.IMPROVES
        elif unknown:
            # 只要还有一个合法分支未能完成计算，就不能给出破坏性结论。
            status = SmartTuningStatus.UNKNOWN
        else:
            status = SmartTuningStatus.NO_IMPROVEMENT
        reason = best.reason
        if best_label:
            reason += f"；{best_label}"
        if winning:
            reason += "：" + "、".join(winning)
        return status, reason, best.maximum_rate, sum(
            row[1].evaluated for row in searched)

    def _required_affix(
        self, context: _PlanContext, slot: str, candidate: dict,
    ) -> str:
        """返回玩法对当前槽位明确规定的神力/武学增效词条。"""
        cfg = self._game_config.get_playstyle(context.playstyle) or {}
        equip_type = str(candidate.get("type") or "")
        # 备战方案的 main/sub 只表示装备槽；武学选择按无序组合识别，实际
        # 主副武器可能与玩法登记顺序相反，必须按武器类型匹配增伤要求。
        if slot in _WEAPON_SLOTS and equip_type == cfg.get("main_weapon"):
            return str(cfg.get("main_damage") or "")
        if slot in _WEAPON_SLOTS and equip_type == cfg.get("sub_weapon"):
            return str(cfg.get("sub_damage") or "")
        if slot in {"ring", "pendant"}:
            return ("全武学增效"
                    if cfg.get("all_skill_requirement") == "需要" else "")
        if slot in {"head", "chest"}:
            required = {
                "单体": "单体类奇术增伤",
                "群体": "群体类奇术增伤",
            }.get(str(cfg.get("qishu_requirement") or ""), "")
            if not required:
                return ""
            try:
                level = int(candidate.get("level") or 0)
            except (TypeError, ValueError):
                level = 0
            level_range = self._game_config.get_affix_level_range(required)
            source_level = level_range.through_level
            if level and source_level and level > source_level:
                return (self._game_config.resolve_affix_upgrade(
                    required, source_level, level) or required)
            return required
        if slot in {"leg", "wrist"}:
            return {
                "首领": "对首领单位增伤",
                "玩家": "对玩家单位增效",
            }.get(str(cfg.get("unit_requirement") or ""), "")
        return ""

    def _with_affixes(self, source: dict, names: tuple[str, ...]) -> dict | None:
        result = copy.deepcopy(source)
        empty_slots = [i for i in range(1, 6)
                       if not isinstance(result.get(f"affix_{i}"), dict)]
        if len(names) > len(empty_slots):
            return None
        level = int(result.get("level") or 0)
        for index, name in zip(empty_slots, names, strict=False):
            caps = self._game_config.get_affix_caps(level, name)
            chengyin_cap = affix_cap_value(
                level, name, chengyin=True, game_config=self._game_config)
            if not caps or chengyin_cap is None:
                return None
            result[f"affix_{index}"] = {
                "name": name,
                "value": chengyin_cap,
                "unit": caps.get("unit") or None,
            }
        return None if validate_combination_dict(result) else result

    def _candidate_rate(
        self, context: _PlanContext, slot: str, candidate: dict,
    ) -> float:
        return context.get_scorer(self._game_config).rate_attrs(
            self._candidate_attrs(context, slot, candidate))

    def _candidate_attrs(
        self, context: _PlanContext, slot: str, candidate: dict,
    ) -> CombatAttributes:
        """候选替换到该槽后的三满极限属性，与方案上限保持同一口径。

        其余七件的三满投影按 (方案, 槽) 缓存一次；候选与它们合成整套后交
        评分内核归一化聚合——同名专属武学增伤只取最高等规则由内核统一处理。
        """
        other_key = (context.plan_id, slot)
        other_equipped = self._other_equipped_cache.get(other_key)
        if other_equipped is None:
            other_equipped = self._apply_maximum_assumptions(
                context, {
                    key: value for key, value in context.equipped.items()
                    if key != slot
                })
            self._other_equipped_cache[other_key] = other_equipped
        combined = dict(other_equipped)
        combined[slot] = self._apply_maximum_assumptions(
            context, {slot: candidate})[slot]
        return context.get_scorer(self._game_config).attrs(combined)

    def _apply_maximum_assumptions(
        self, context: _PlanContext, equipped: dict[str, dict],
    ) -> dict[str, dict]:
        """按目标玩法应用满等级、满承音、满定音三项统一假设。

        智能调律比较的是三项全部拉满后的理论极限。满承音只提升装备本身
        已经是承音（或因满等级升级而成为承音）的普通词条；当前赛季原生
        装备保持原生状态。候选新词条仍按不投入彩色狗粮时可达到的上限补全。
        """
        return Assumptions(
            full_level=self._game_config.current_equip_level(),
            full_chengyin=True,
            full_dingyin=True,
            playstyle=context.playstyle,
        ).project(equipped, self._game_config)
