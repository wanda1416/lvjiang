"""调律判定与评级 — TuningJudge

装备潜力判定、期望评级提取、行为表评级提供者。
纯逻辑类，不依赖 UI 操作原语，通过 ctx 获取判定配置。
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from loguru import logger
from lvjiang.apps.shuijiao_tuning.core.build_profiles import ProfileJudge
from lvjiang.apps.shuijiao_tuning.core.equip_validator import duplicate_tuned_names
from lvjiang.apps.shuijiao_tuning.core.junk_recycle import (
    armor_combination_possible,
    left_combination_possible,
)
from lvjiang.apps.shuijiao_tuning.workflows.implementations.tuning.decisions import (
    BehaviorAction,
    BehaviorDecision,
)

from lvjiang.apps.yysls.core.equip_parser import EquipmentData
from lvjiang.apps.yysls.core.evaluator import (
    GenericTuningJudge,
    get_rule_names,
    get_tuning_rules,
    judge_equipment_potential,
    summarize_potential,
)
from lvjiang.apps.yysls.core.tuning_rules import (
    JUDGE_SCOPE_LABELS,
    RATING_LABELS,
    RATING_RANK,
    RatingProvider,
    ScanBehavior,
    TuneBehavior,
)
from lvjiang.i18n import tr

if TYPE_CHECKING:
    from lvjiang.apps.shuijiao_tuning.workflows.tuning_context import TuningRunContext

_RATING_UNSET = object()


class TuningJudge:
    """调律判定与评级：潜力判定、期望评级、行为表评级提供者"""

    def __init__(self, ctx: TuningRunContext):
        self._ctx = ctx
        self._all_potential_key = ""
        self._all_potential: dict = {}
        self._incoming_key = ""
        self._incoming_potential: dict = {}
        self._profile_judge: ProfileJudge | None = None
        self.transition_seen = False
        self.graduation_seen = False

    def profile_judge(self) -> ProfileJudge:
        from lvjiang.apps.shuijiao_tuning.core.build_profiles import load_profiles

        if self._profile_judge is None:
            policy = self._ctx.junk_recycle
            self._profile_judge = ProfileJudge(self._ctx.profile_catalog or load_profiles(),
                self._ctx.profile_overrides, policy.affixes, minimum=policy.minimum, count_enabled=policy.enabled,
                field_modes=self._ctx.profile_field_modes,
                strict_parts=self._ctx.strict_parts, strict_flexible=self._ctx.strict_flexible,
                extra_junk={"small": policy.small_affixes if policy.by_build else (),
                            "large": policy.large_affixes if policy.by_build else ()})
        return self._profile_judge

    def profile_decision(self, equip: EquipmentData, *, full: bool) -> BehaviorDecision:
        if duplicate_tuned_names([a.name for a in equip.affixes[1:]]):
            return BehaviorDecision(BehaviorAction.SKIP, "后四普通词条重复，可能识别异常；保留本件，不调律不回收")
        judge = self.profile_judge()
        selected = self._ctx.build_profiles or []
        assessments = [judge.assess(key, equip) for key in selected]
        matches = [judge.catalog[k]["label"] for k, a in zip(selected, assessments, strict=True) if a.viable]
        if matches:
            if full:
                self.graduation_seen = True
            return BehaviorDecision(BehaviorAction.SKIP if full else BehaviorAction.CONTINUE,
                                    "符合流派要求：" + "、".join(matches))
        strict = judge.is_strict(equip, selected)
        protected = "" if strict else self.top_protection(equip)
        if protected:
            return BehaviorDecision(BehaviorAction.SKIP if full else BehaviorAction.CONTINUE,
                                    "跨流派保护：" + protected)
        transition = judge.transition_keys(equip, selected)
        if transition:
            self.transition_seen = True
            return BehaviorDecision(BehaviorAction.SKIP if full else BehaviorAction.CONTINUE,
                                    "过渡保留：词条均在刚需/灵活并集内，等待同部位毕业替代品（" +
                                    "、".join(judge.catalog[k]["label"] for k in transition) + "）")
        common_junk_first = bool(equip.affixes and equip.affixes[0].name in self._ctx.junk_recycle.affixes)
        if not any(a.applicable for a in assessments) and not common_junk_first:
            return BehaviorDecision(BehaviorAction.SKIP, "装备不属于所选流派的武器，保留原件")
        action = (BehaviorAction.TUNE_FULL_RECYCLE if equip.level == 115 and equip.quality == "gold" and not full
                  else BehaviorAction.RECYCLE)
        missing = next((a.missing for a in assessments if a.applicable and a.missing),
                       "固定首条在通用垃圾名单" if common_junk_first else "组合不符合所选流派")
        return BehaviorDecision(action, missing + ("；无狗粮补满后回收" if action is BehaviorAction.TUNE_FULL_RECYCLE else "；回收"))

    def profile_potential(self, equip: EquipmentData, *, all_builds: bool = False) -> dict:
        judge = self.profile_judge()
        results = {}
        for key in list(judge.catalog) if all_builds else self._ctx.build_profiles or []:
            assessment = judge.assess(key, equip)
            transition = bool(judge.transition_keys(equip, [key])) if not assessment.viable else False
            results[key] = {"name": judge.catalog[key]["label"], "skipped": False,
                            "not_applicable": not assessment.applicable,
                            "rating": RATING_LABELS["top" if assessment.viable else "excellent" if transition else "junk"],
                            "reasons": ["满足五框要求，含空槽与至多一次合法转律" if assessment.viable else
                                        "目标并集内的过渡装，尚缺刚需" if transition else assessment.missing or "武器不属于该流派"]}
        return results

    def effective_junk_policy(self):
        return self._ctx.junk_recycle.for_rules(self._ctx.judge_configs, self._ctx.judge_rule_keys)

    @staticmethod
    def missing_natural_affix(equip: EquipmentData) -> str:
        if len(equip.affixes) < 5:
            return ""
        required = (f"{equip.weapon}武学增伤" if equip.weapon else
                    "全武学增效" if equip.part in ("环", "佩") else
                    "对首领单位增伤" if equip.part in ("腕甲", "胫甲") else "")
        names = [a.name for a in equip.affixes]
        if required == "对首领单位增伤" and "对玩家单位增效" in names:
            return ""
        return required if required and required not in names else ""

    def per_build_junk(self, equip: EquipmentData) -> list[list[str]]:
        policy = self._ctx.junk_recycle
        if not policy.by_build:
            return []
        rules = get_tuning_rules()
        options = []
        configs = self._ctx.judge_configs or {}
        for key in self._ctx.judge_rule_keys or list(configs):
            if key not in rules:
                continue
            cfg = configs.get(key, {})
            profile = policy.for_rules({key: cfg}, [key])
            for bad in GenericTuningJudge(rules[key], cfg).observed_junk_options(equip):
                options.append([a.name for a in equip.affixes[1:5]
                                if a.name in bad or a.name in profile.affixes])
        return options

    def initial_rejection_reason(self, equip_data: EquipmentData) -> str:
        if self._ctx.build_profiles:
            return ""  # 新流派模型在完整组合判定中统一检查固定首条。
        if not equip_data.affixes:
            return ""
        first = equip_data.affixes[0].name
        if self._ctx.junk_recycle.first_is_junk([first]):
            return f"固定首词条「{first}」在通用垃圾名单中"
        if equip_data.weapon:
            # 用户的武器培养策略；首条准入独立于后四条的流派垃圾计数。
            required = "最大外功攻击" if equip_data.weapon in ("剑", "枪", "扇", "伞") else "最小外功攻击"
            return "" if first == required else f"{equip_data.weapon}只培养「{required}」首条，当前为「{first}」"
        if equip_data.part in ("冠胄", "胸甲", "胫甲", "腕甲") and first in ("劲", "精准率", "会心率"):
            return ""  # 四防具由首条分类，不借用当前目标流派的首条垃圾表。
        if self.effective_junk_policy().first_is_junk([first]):
            return f"固定首词条「{first}」不适合本次所选流派"
        return ""

    def armor_routes(self, equip: EquipmentData, *, all_builds: bool = False) -> tuple[bool, str]:
        """四防具按首条定大小外；小外本系来自所选玩法，保护时检查所有属性。"""
        if equip.part not in ("冠胄", "胸甲", "胫甲", "腕甲") or not equip.affixes:
            return False, ""
        first = equip.affixes[0].name
        if first == "劲":
            return True, "劲首条大外组合" if armor_combination_possible(equip, "large") else ""
        if first not in ("会心率", "精准率"):
            return (equip.part in ("胫甲", "腕甲") and bool(
                self._ctx.judge_rule_keys or self._ctx.judge_configs)), ""
        rules = get_tuning_rules()
        configs = {} if all_builds else self._ctx.judge_configs or {}
        keys = list(rules) if all_builds else self._ctx.judge_rule_keys or list(configs)
        attrs: set[str] = set()
        for key in keys:
            rule = rules.get(key)
            if rule is None:
                continue
            chosen = configs.get(key, {}).get("playstyles")
            for name in list(rule.playstyles) if chosen is None else chosen:
                ps = rule.playstyles.get(name)
                if ps is None:
                    continue
                if ps.attr != "通用":
                    attrs.add(ps.attr)
        if not attrs and "huixin_small" in rules:
            attrs = {ps.attr for ps in rules["huixin_small"].playstyles.values() if ps.attr != "通用"}
        for attr in sorted(attrs):
            if armor_combination_possible(equip, "small", attr):
                return True, f"会心/精准首条小外（{attr}）组合"
        return True, ""

    def all_potential(self, equip_data: EquipmentData) -> dict:
        """所有已配置流派的潜力；不受本次选中玩法限制。"""
        key = repr(equip_data.to_dict(include_fp=False))
        if key != self._all_potential_key:
            self._all_potential = self.judge_by_scope(equip_data, "all", [])
            self._all_potential_key = key
        return self._all_potential

    def qishu_armor_protection(self, equip: EquipmentData) -> str:
        """已出现奇术的头/胸按对应大外规则保护，不借未来空槽虚构奇术。"""
        if equip.part not in ("头甲", "胸甲", "冠胄") or equip.level not in (105, 110, 115) or not equip.affixes:
            return ""
        from lvjiang.apps.yysls.config import get_game_config

        gc = get_game_config()
        if not any(gc.resolve_affix_category(a.name) == "奇术类增伤" and gc.is_affix_available(a.name, equip.level)
                   for a in equip.affixes[1:]):
            return ""
        route = {"会意率": ("huiyi_general", "虹虹/影影"), "会心率": ("huixin_yuyu", "玉玉"),
                 "精准率": ("huixin_yuyu", "玉玉")}.get(
            equip.affixes[0].name)
        if route is None:
            return ""
        key, label = route
        rule = get_tuning_rules().get(key)
        if rule is None:
            return ""
        judge = GenericTuningJudge(rule, {"switches": {"keep_danti": True}, "can_transmute": True})
        result = (judge.judge_with_legal_transmute(equip) if len(equip.affixes) == 5
                  else judge.check_tuning_worthiness(equip))
        if not result.skipped and not result.not_applicable and result.rating.value == RATING_LABELS["top"]:
            return f"{label}奇术大外毕业组合（含最多一次合法转律）"
        return ""

    def top_protection(self, equip_data: EquipmentData) -> str:
        policy = self._ctx.junk_recycle
        if self._ctx.build_profiles:
            if not policy.protect_other_builds:
                return ""
            judge = self.profile_judge()
            keys = list(judge.catalog)
            if len(equip_data.affixes) < 5:
                from lvjiang.apps.yysls.config import get_game_config

                if equip_data.part not in ("冠胄", "胸甲") or not any(
                        get_game_config().resolve_affix_category(a.name) == "奇术类增伤" for a in equip_data.affixes):
                    return ""
            return "、".join(judge.matches(keys, equip_data))
        if not policy.protect_other_builds or policy.first_is_junk([a.name for a in equip_data.affixes]):
            return ""
        qishu = self.qishu_armor_protection(equip_data)
        if qishu:
            return qishu
        armor_active, armor_match = self.armor_routes(equip_data, all_builds=True)
        if armor_active:
            return (armor_match if policy.protect_other_builds and len(equip_data.affixes) == 5
                    and not self.missing_natural_affix(equip_data) else "")
        if not left_combination_possible(equip_data):
            return ""
        if self.missing_natural_affix(equip_data):
            return ""
        if len(equip_data.affixes) < 5 and self.initial_rejection_reason(equip_data):
            return ""
        if len(equip_data.affixes) < 5:
            options = self.per_build_junk(equip_data)
            if options and min(map(len, options)) >= policy.minimum:
                return ""  # 两废已不可由一次转律救回；不靠未出现的跨流派词条虚拟保留。
        names = [r.get("name", key) for key, r in self.all_potential(equip_data).items()
                 if not r.get("skipped") and not r.get("not_applicable")
                 and r.get("rating") == RATING_LABELS["top"]]
        return "、".join(names)

    def gold_viable(self, equip_data: EquipmentData) -> bool:
        if self._ctx.build_profiles:
            judge = self.profile_judge()
            return bool(judge.matches(self._ctx.build_profiles, equip_data) or
                        (not judge.is_strict(equip_data, self._ctx.build_profiles) and self.top_protection(equip_data)))
        armor_active, armor_match = self.armor_routes(equip_data)
        if armor_active:
            return bool(armor_match or self.top_protection(equip_data))
        if not left_combination_possible(equip_data):
            return False
        incoming = self.judge_by_scope(equip_data, "incoming", [])
        return self.expect_key(incoming) in ("top", "excellent") or bool(
            self.top_protection(equip_data))

    @staticmethod
    def expect_key(results: dict) -> str | None:
        """从潜力判定结果提取装备期望评级 key（适用规则最高档）

        跳过/不适用的规则不参与；无任何适用规则时返回 None
        （狗粮规则的期望条件永不命中）。
        """
        label_to_key = {v: k for k, v in RATING_LABELS.items()}
        best: str | None = None
        for r in (results or {}).values():
            if r.get("skipped") or r.get("not_applicable"):
                continue
            key = label_to_key.get(r.get("rating", ""))
            if key and (best is None or RATING_RANK[key] > RATING_RANK[best]):
                best = key
        return best

    def final_judge(self, equip_data: EquipmentData) -> dict:
        """终局判定：含转律模拟的各规则评级上限，返回结构化结果供 report/hook 消费"""
        results = self.judge_by_scope(equip_data, "incoming", [])
        for r in results.values():
            tag = (tr("不适用") if r["not_applicable"]
                   else tr("跳过") if r["skipped"] else r["rating"])
            logger.info(
                f"  终局判定 | {r['name']}: {tag}（{'；'.join(r['reasons'])}）")
        return results

    def judge_by_scope(self, equip_data: EquipmentData, scope: str,
                       keys: list[str]) -> dict:
        """按判定语义跑潜力判定（行为点的预期评级识别入口）

        incoming=运行期传入配置；all=全部规则默认配置；custom=
        自选 key 集 + 默认配置（已删除的 key 运行期过滤；全部无效
        时回落全部规则，宁多保留不误收）。
        """
        if self._ctx.build_profiles:
            return self.profile_potential(equip_data, all_builds=scope != "incoming")
        if scope == "incoming":
            key = repr((equip_data.to_dict(include_fp=False), self._ctx.judge_configs,
                        self._ctx.judge_rule_keys))
            if key != self._incoming_key:
                self._incoming_potential = judge_equipment_potential(
                    equip_data, self._ctx.judge_configs, self._ctx.judge_rule_keys)
                self._incoming_key = key
            return self._incoming_potential
        use_keys = None
        if scope == "custom" and keys:
            known = set(get_rule_names())
            use_keys = [k for k in keys if k in known]
            unknown = [k for k in keys if k not in known]
            if unknown:
                logger.warning(f"判定声明的规则不存在: {unknown}，已忽略")
            if not use_keys:
                logger.warning("判定声明的规则全部无效，按全部规则判定")
                use_keys = None
        return judge_equipment_potential(equip_data, None, use_keys)

    def rating_provider(self, equip_data: EquipmentData,
                        incoming: dict | None = None, *,
                        incoming_first_affix_only: bool = False,
                        incoming_rating: str | None | object = _RATING_UNSET,
                        ) -> RatingProvider:
        """构造行为表的评级提供者（同一装备当前词条状态内缓存）

        各规则按自身判定语义懒算评级（缓存键 (scope, keys,
        仅首词条)）；incoming 为已有的传入规则判定结果，作种子
        避免重复跑。种子必须落在与其口径一致的缓存键上：门槛按
        "仅首词条"判定得到的结果只能复用给同样声明仅首词条的规则，
        否则全词条规则会拿到偏高的评级。
        first_affix_only 时只注入首词条（其余槽视作空槽由潜力
        判定自由填充），避免回收掉非首词条已成垃圾但可重置
        调律的装备。无任何适用规则（部位/品阶不在任何判定
        范围）= 无调律价值，按业务约定兜底为垃圾档。
        """
        cache: dict[tuple, str] = {}
        if incoming is not None:
            seed_fao = incoming_first_affix_only and len(equip_data.affixes) > 1
            cache[("incoming", (), seed_fao)] = (
                self.expect_key(incoming) or "junk")
        elif incoming_rating is not _RATING_UNSET:
            # 材料处理每轮已有刚刷新的传入规则最高评级，只需把该值
            # 作为 incoming 缓存种子；全部/自选规则仍按需懒算。
            cache[("incoming", (), False)] = (
                str(incoming_rating) if incoming_rating else "junk")
        label = equip_data.name or equip_data.type

        def rating_of(scope: str, keys: list[str],
                      first_affix_only: bool = False) -> str:
            # 单词条装备无需构造副本，归一到全词条缓存键
            fao = first_affix_only and len(equip_data.affixes) > 1
            ck = (scope, tuple(keys), fao)
            if ck not in cache:
                target = equip_data
                if fao:
                    target = replace(
                        equip_data, affixes=equip_data.affixes[:1],
                        extra_data={**equip_data.extra_data,
                                    "affix_count": 1})
                    logger.info(
                        f"  [行为评级] {label} 仅按首词条判定评级"
                        f"（忽略其他 {len(equip_data.affixes) - 1} 条）")
                results = self.judge_by_scope(target, scope, keys)
                rating = self.expect_key(results) or "junk"
                cache[ck] = rating
                winning_names = [
                    str(result.get("name") or key)
                    for key, result in results.items()
                    if not result.get("skipped")
                    and not result.get("not_applicable")
                    and result.get("rating") == RATING_LABELS.get(rating)
                ]
                scope_label = JUDGE_SCOPE_LABELS.get(scope, scope)
                winners = (
                    f"（命中规则：{'、'.join(winning_names)}）"
                    if winning_names else "（无适用规则，兜底）"
                )
                logger.info(
                    f"  [行为评级] {label} 按{scope_label}判定为 "
                    f"{RATING_LABELS.get(rating, rating)}{winners}")
            return cache[ck]

        return rating_of

    def decide_scan(
        self,
        config: ScanBehavior,
        equip_data: EquipmentData,
        incoming: dict,
        *,
        incoming_first_affix_only: bool = False,
    ) -> BehaviorDecision:
        """纯扫描行为决策；不执行回收、导航或 UI 通知。"""
        part, quality, cap_pct = self.recycle_inputs(equip_data)
        action, reason = config.decide(
            part,
            quality,
            cap_pct,
            self.rating_provider(
                equip_data, incoming,
                incoming_first_affix_only=incoming_first_affix_only),
            [affix.name for affix in equip_data.affixes],
        )
        return BehaviorDecision.from_raw(action, reason)

    def decide_end(
        self,
        config: TuneBehavior,
        equip_data: EquipmentData,
        incoming: dict | None,
        *,
        full: bool,
    ) -> BehaviorDecision:
        """纯单轮结束行为决策；不执行重置、回收或继续动作。"""
        if self._ctx.build_profiles:
            return self.profile_decision(equip_data, full=full)
        if self._ctx.junk_recycle.enabled:
            missing = self.missing_natural_affix(equip_data)
            if missing:
                return BehaviorDecision(BehaviorAction.RECYCLE,
                                        f"五条已确定，缺少不可转律补出的「{missing}」，回收")
            rejected = self.initial_rejection_reason(equip_data)
            if rejected:
                protected = self.top_protection(equip_data) if full else ""
                if protected:
                    return BehaviorDecision(BehaviorAction.SKIP, f"跨流派成品保护：{protected}，保留装备")
                rejected_action = (BehaviorAction.TUNE_FULL_RECYCLE if equip_data.level == 115
                                   and equip_data.quality == "gold" and not full
                                   else BehaviorAction.RECYCLE)
                suffix = ("；115 阶不加狗粮，补满普通词条后回收"
                          if rejected_action is BehaviorAction.TUNE_FULL_RECYCLE else "；回收")
                return BehaviorDecision(rejected_action, rejected + suffix)
            armor_active, armor_match = self.armor_routes(equip_data)
            if armor_active:
                if armor_match:
                    return BehaviorDecision(BehaviorAction.SKIP if full else BehaviorAction.CONTINUE,
                                            f"符合{armor_match}，" + ("保留" if full else "继续培养"))
                protected = self.top_protection(equip_data)
                if protected:
                    return BehaviorDecision(BehaviorAction.SKIP if full else BehaviorAction.CONTINUE,
                                            f"跨流派保护：{protected}")
                armor_action = (BehaviorAction.TUNE_FULL_RECYCLE if equip_data.level == 115
                                and equip_data.quality == "gold" and not full else BehaviorAction.RECYCLE)
                return BehaviorDecision(armor_action, "防具首条对应大小外必需组合不符，最多一次合法转律也不可达，判废")
            if not left_combination_possible(equip_data):
                combination_action = (BehaviorAction.TUNE_FULL_RECYCLE if equip_data.level == 115
                          and equip_data.quality == "gold" and not full else BehaviorAction.RECYCLE)
                return BehaviorDecision(combination_action, "左四件必需组合无法通过空槽与最多一次合法转律达成，判废")
            action, reason = self.effective_junk_policy().decision(
                [a.name for a in equip_data.affixes], full=full, first_valid=True)
            options = self.per_build_junk(equip_data)
            if options:
                best = min(options, key=len)
                action = ("recycle" if len(best) >= self._ctx.junk_recycle.minimum else
                          "skip" if full else "continue")
                reason = (f"首条适用玩法中最少废条 {len(best)}/{self._ctx.junk_recycle.minimum}"
                          f"（{'、'.join(best) or '无'}），{action}")
            if action == "recycle" or full:
                protected = self.top_protection(equip_data)
                if protected:
                    action = "skip" if full else "continue"
                    reason = (f"跨流派保护：{protected} 可达顶级（含合法转律），"
                              + ("保留装备" if full else "继续逐条培养"))
            if action == "recycle" and equip_data.level == 115 and equip_data.quality == "gold" and not full:
                return BehaviorDecision(BehaviorAction.TUNE_FULL_RECYCLE, reason + "；115金装无狗粮补满后回收")
            return BehaviorDecision.from_raw(action, reason)
        part, quality, cap_pct = self.recycle_inputs(equip_data)
        rating_of = self.rating_provider(equip_data, incoming)
        final_rating = (
            rating_of("incoming", [], False)
            if len(equip_data.affixes) >= 5 else None
        )
        action, reason = config.decide(
            part,
            quality,
            cap_pct,
            rating_of,
            full,
            [affix.name for affix in equip_data.affixes],
            final_rating,
        )
        return BehaviorDecision.from_raw(action, reason)

    @staticmethod
    def recycle_inputs(equip_data: EquipmentData,
                       ) -> tuple[str, str | None, float | None]:
        """行为规则匹配输入：(部位, 品阶, 首词条初始数值 cap_pct)

        首词条 cap_pct 不因调律改变，各行为点可用同一输入。
        """
        cap_pct = (equip_data.affixes[0].cap_pct
                   if equip_data.affixes else None)
        return equip_data.part, equip_data.quality, cap_pct

    def refresh_expectation(self, equip_data: EquipmentData,
                            ) -> tuple[dict, str | None]:
        """按传入规则刷新装备预期评级（每轮调律结束后取值）

        行为表评级另经评级提供者按各规则判定语义懒取。

        Returns: (传入规则判定结果, 最大预期评级 key)。
        """
        results = self.judge_by_scope(equip_data, "incoming", [])
        _, logs = summarize_potential(results)
        for line in logs:
            logger.info(f"  潜力判定 | {line}")
        return results, self.expect_key(results)
