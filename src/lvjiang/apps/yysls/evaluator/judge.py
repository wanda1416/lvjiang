"""通用调律规则判定器（规则驱动，四档条件线性判定制）

GenericTuningJudge 加载 TuningRule（YAML 外置规则）完成 judge
（完整定级）与 check_tuning_worthiness（调律潜力）。
规则中的词条引用为规则可引用词表（标准词条全集 + 四个动态
词条 最大/最小本属攻击、最大/最小外属攻击）。

定级流程（线性，无模式匹配）：
1. 首词条不在 first → 跳过（无调律价值）
2. 非首词条存在 池外且非本次增伤 的词条 → 垃圾
3. 本次武器规则要求增伤但词条缺失 → 垃圾
4. 四档条件按 垃圾 → 一般 → 优秀 → 顶级 顺序求值（每档条件
   组间 OR、组内 AND，规则级通用判定条件组逐档并入所有部位、
   通用在前），命中即定档；全不命中取默认判定（部位级
   default_rating 优先，缺省跟随规则级）。条件组先按开关状态过滤：
   when 全部匹配才参与判定（未配置的开关视作 False）。

潜力判定（check_tuning_worthiness）按可用词条库（affix_pool，
声明序即全局价值序）填充空槽、模拟一次转律后复用同一套完整
定级，返回评级上限（详见 _eval_partial）。

武器部位按用户勾选的玩法设定（如 纯唐/双切）逐一尝试，装备武器名
匹配主/副武器即产生一次判定（携带该侧增伤要求与玩法属性），取评级
最高者。非武器部位按勾选玩法的不同属性去重展开（各属性各跑一次取
最优）。

非武器部位判定时按玩法属性做「属攻→动态词条」归类（双重
身份，非破坏性改写）：装备上的具体属攻同时以字面名与归类名
（最大/最小本属攻击 =玩法属性、最大/最小外属攻击 =其余属性）
参与匹配：规则写真实属攻（单流派字面精确）或动态词条（跨属性
泛化）均可命中；武器部位保持字面匹配不做归类（无相词条为
字面语义，仅武器掉落）；attr=通用（混搭流）不做任何归类。

品阶门槛与开关注册表从 tuning_base.yaml 读取：
- 品阶门槛按标准部位（武器/环/佩/防具四件）配置，规则级
  quality_thresholds 可按部位覆盖全局默认；
- 开关状态由调用方经 config["switches"] 注入，仅影响带 when 前提
  的条件组是否参与判定，引擎无任何开关专属特判。
"""

from __future__ import annotations

from lvjiang.apps.yysls.equip_parser import EquipmentData

from .base import JudgeResult, Rating, TuningJudge, part_label
from .tuning_rules import (
    DYNAMIC_AFFIXES,
    GENERIC_ATTR,
    PART_ALIAS,
    RATING_LABELS,
    ConditionGroup,
    PartPattern,
    TuningRule,
    dynamic_affix_map,
    get_tuning_base,
)

# 评级排序（多玩法/转律模拟取评级上限最高的组合）
_RANK = {Rating.JUNK: 0, Rating.NORMAL: 1, Rating.EXCELLENT: 2,
         Rating.TOP: 3}

# 档位 key → 评级枚举（default_rating / 四档循环共用）
_RATING_BY_KEY = {"junk": Rating.JUNK, "normal": Rating.NORMAL,
                  "excellent": Rating.EXCELLENT, "top": Rating.TOP}

# 武器部位 key（不做属攻→动态词条归类）
_WEAPON_PARTS = ("主武器", "副武器")


def _group_text(group: ConditionGroup) -> str:
    """条件组 reason 文案"""
    return "；".join(f"{c.kind}[{'/'.join(c.symbols)}]"
                    for c in group.conditions)


class GenericTuningJudge(TuningJudge):
    """通用调律规则判定器：由 TuningRule + 用户配置驱动"""

    def __init__(self, rule: TuningRule, config: dict | None = None):
        super().__init__(config)
        self.rule = rule
        # 元数据改为实例属性（由规则填充，UI 接口与旧类属性同名）
        self.rule_key = rule.key
        self.rule_name = rule.name
        self.implemented = True
        self.playstyle_options = rule.playstyle_options

    def judge(self, equip: EquipmentData) -> JudgeResult:
        return self._run(equip, partial=False)

    def check_tuning_worthiness(self, equip: EquipmentData) -> JudgeResult:
        """调律潜力判定：价值序填充空槽 + 模拟一次转律，返回评级上限"""
        return self._run(equip, partial=True)

    # ─── 主流程 ────────────────────────────────────────────

    def _run(self, equip: EquipmentData, partial: bool) -> JudgeResult:
        result = JudgeResult(equipment=equip)

        attempts = self._build_attempts(equip)
        if not attempts:
            result.skipped = True
            result.not_applicable = True
            result.reasons.append(
                f"{part_label(equip)} 不在 {self.rule_name} 判定范围内")
            return result

        # 品阶筛选（quality 未识别时跳过此步继续判定）
        pre_reasons: list[str] = []
        if equip.quality is None:
            pre_reasons.append("品阶未识别，跳过品阶筛选")
        elif not get_tuning_base().quality_ok(
                equip.part, equip.quality, self.rule.quality_thresholds):
            result.skipped = True
            result.reasons.append(f"品阶 {equip.quality} 无调律价值")
            return result

        if not equip.affixes:
            result.skipped = True
            result.reasons.append("无词条数据")
            return result

        n_free = (5 - len(equip.affixes)) if partial else 0

        # 逐个武器规则组合尝试，取最优结果
        best_label = ""
        best: JudgeResult | None = None
        for label, part_key, pattern, damage, attr in attempts:
            res = self._judge_attempt(
                equip, part_key, pattern, damage, attr, partial, n_free)
            score = -1 if res.skipped else _RANK[res.rating]
            if best is None or score > (-1 if best.skipped else _RANK[best.rating]):
                best, best_label = res, label
        assert best is not None
        if best_label:
            best.reasons = pre_reasons + [f"[{best_label}] {r}"
                                          for r in best.reasons]
        else:
            best.reasons = pre_reasons + best.reasons
        return best

    def _judge_attempt(self, equip: EquipmentData, part_key: str,
                       pattern: PartPattern, damage: str | None,
                       attr: str, partial: bool, n_free: int) -> JudgeResult:
        """按单个部位/玩法组合判定一次"""
        result = JudgeResult(equipment=equip)

        first_token = equip.affixes[0].name
        tokens = [a.name for a in equip.affixes[1:]]

        # 非武器部位：属攻→动态词条归类映射（双重身份，装备
        # 词条同时以字面名与归类名参与匹配，不改写原名）
        equiv: dict[str, str] = {}
        if part_key not in _WEAPON_PARTS:
            equiv = dynamic_affix_map(attr)

        # 首词条判断（字面名与归类名任一命中即符，不符即跳过）
        first_ids = {first_token} | (
            {equiv[first_token]} if first_token in equiv else set())
        if not first_ids & set(pattern.first):
            result.skipped = True
            result.reasons.append(f"首词条 {first_token} 不符合要求")
            return result

        if partial:
            return self._eval_partial(
                result, pattern, damage, equiv,
                first_token, tokens, n_free)

        rating, reason = self._grade(
            pattern, damage, first_token, tokens, equiv)
        result.rating = rating
        result.reasons.append(reason)
        return result

    def _grade(self, pattern: PartPattern, damage: str | None,
               first_token: str, tokens: list[str],
               alias: dict[str, str]) -> tuple[Rating, str]:
        """完整定级核心（流程 2/3/4），潜力判定的填充/转律变体复用

        alias 为字面名→动态归类名映射（双重身份，武器部位为空）。

        Returns:
            (评级, 判定理由)
        """
        pool = self.rule.pool_set

        # 流程 2：池外且非本次增伤的词条 → 垃圾（任一身份在池即在池）
        junk = [t for t in tokens
                if t not in pool and alias.get(t) not in pool
                and t != damage]
        if junk:
            return Rating.JUNK, f"垃圾词条: {'、'.join(junk)}"

        # 流程 3：本次武器规则要求增伤但词条缺失 → 垃圾
        if damage and damage not in tokens:
            return Rating.JUNK, f"缺失必需增伤词条: {damage}"

        # 流程 4：四档条件 junk → normal → excellent → top，
        # 条件组先按开关状态过滤，全不命中取默认判定（部位级优先）
        for tier_key, groups in self._tiers(pattern):
            for group in groups:
                if not group.active(self.switches):
                    continue
                if all(c.check(first_token, tokens, alias)
                       for c in group.conditions):
                    return (_RATING_BY_KEY[tier_key],
                            f"命中{RATING_LABELS[tier_key]}条件"
                            f"（{_group_text(group)}）")
        default = pattern.default_rating or self.rule.default_rating
        return (_RATING_BY_KEY[default],
                f"四档条件均未命中，默认判定为{RATING_LABELS[default]}")

    def _tiers(self, pattern: PartPattern
               ) -> tuple[tuple[str, list[ConditionGroup]], ...]:
        """四档条件按判定顺序展开（档位 key, 条件组列表）

        逐档并入规则级通用判定条件组（对所有部位生效，通用在前，
        组间仍为 OR）。
        """
        common = self.rule.common
        return (
            ("junk", common.junk_conditions + pattern.junk_conditions),
            ("normal", common.normal_conditions + pattern.normal_conditions),
            ("excellent",
             common.excellent_conditions + pattern.excellent_conditions),
            ("top", common.top_conditions + pattern.top_conditions))

    def _eval_partial(self, result: JudgeResult, pattern: PartPattern,
                      damage: str | None, equiv: dict[str, str],
                      first_token: str, tokens: list[str],
                      n_free: int) -> JudgeResult:
        """潜力判定：按可用词条库价值序填充空槽 + 模拟一次转律

        填充（best-case 上限）：遍历 affix_pool（声明序即价值序），
        过滤装备部位（动态词条不在部位表中，特判仅非武器部位
        可用），按身份集去重（字面名/动态归类名任一身份已在非首
        词条或候选中即跳过，允许与首词条重复）得到填充候选；本次
        武器规则要求增伤且缺失时第一个空槽先补增伤（n_free=0 时由
        _grade 判垃圾），其余空槽按候选价值序填满（候选耗尽则留空）。

        模拟转律：非首词条（存在+填充）中按价值挑最差一条转出
        （池外词条最优先，池内按 affix_pool 排位靠后者优先，身份集
        取最优排位；增伤词条豁免），转入取转律词条库
        （transmute_priority，同样经部位过滤）中身份未出现的最高
        优先级词条，替换后再定级，与不转律基线取评级较高者。填充
        与转律后均复用 _grade（与完整定级同一套条件求值）。
        """
        from ..game_config import get_game_config
        gc = get_game_config()
        part = result.equipment.part

        def part_ok(name: str) -> bool:
            # 动态词条不在游戏配置部位表中（缺省会误判全部位），
            # 仅非武器部位可作填充/转入候选
            if name in DYNAMIC_AFFIXES:
                return part != "武器"
            return part in gc.get_affix_parts(name)

        def ids(name: str) -> set[str]:
            """词条身份集（字面名 + 动态归类名）"""
            g = equiv.get(name)
            return {name, g} if g else {name}

        present: set[str] = set()
        for t in tokens:
            present |= ids(t)

        # 填充候选：价值序 + 部位过滤 + 身份集去重
        candidates: list[str] = []
        cand_ids: set[str] = set()
        for name in self.rule.affix_pool:
            if not part_ok(name):
                continue
            if ids(name) & (present | cand_ids):
                continue
            candidates.append(name)
            cand_ids |= ids(name)

        # 顺序填充空槽（缺增伤先占一槽；候选耗尽则留空）
        filled = list(tokens)
        fill_log: list[str] = []
        slots = n_free
        if damage and slots >= 1 and damage not in filled:
            filled.append(damage)
            fill_log.append(damage)
            slots -= 1
        for cand in candidates:
            if slots <= 0:
                break
            filled.append(cand)
            fill_log.append(cand)
            slots -= 1

        def label(base: str) -> str:
            return (f"填充 {'、'.join(fill_log)} 后{base}"
                    if fill_log else base)

        # 不转律基线
        rating, why = self._grade(pattern, damage, first_token, filled, equiv)
        reason = label(why)

        # 转律分支：转掉最差一条换转律库最高优先级，取评级较高者
        if filled and self.config.get("can_transmute", True):
            pool = self.rule.pool_set
            order = self.rule.affix_pool

            def sac_score(t: str) -> int:
                """转出优先级（越大越该转出）；增伤词条豁免"""
                if t == damage:
                    return -1
                if not ids(t) & pool:
                    return 10 ** 6  # 池外垃圾最优先转出
                # 身份集取池内最优排位（最保守的价值估计）
                pos = [order.index(n) for n in ids(t) if n in order]
                return min(pos) if pos else 10 ** 3

            idx = max(range(len(filled)),
                      key=lambda i: sac_score(filled[i]))
            dropped = filled[idx]
            if sac_score(dropped) >= 0:  # 存在可转出词条（非增伤）
                rest = filled[:idx] + filled[idx + 1:]
                excluded = ids(dropped)
                for t in rest:
                    excluded |= ids(t)
                gain = next(
                    (n for n in self.rule.transmute_priority
                     if part_ok(n) and not ids(n) & excluded), None)
                if gain:
                    r2, why2 = self._grade(
                        pattern, damage, first_token, rest + [gain], equiv)
                    if _RANK[r2] > _RANK[rating]:
                        rating = r2
                        reason = label(
                            f"{dropped} 转律为 {gain}，{why2}")

        result.rating = rating
        result.reasons.append(reason)
        return result

    # ─── 判定组合展开 ──────────────────────────────────────

    def _build_attempts(
            self, equip: EquipmentData,
    ) -> list[tuple[str, str, PartPattern, str | None, str]]:
        """展开该装备在当前配置下的全部判定组合

        Returns:
            [(标签, 部位 key, 模式, 增伤词条名或 None, 玩法属性), ...]
        """
        combos: list[tuple[str, str, str | None, str]] = []
        if equip.part == "武器":
            weapon = equip.weapon or ""
            for name in self._enabled_playstyles():
                ps = self.rule.playstyles[name]
                if weapon == ps.main.weapon:
                    combos.append(
                        (f"{name} 主武器", "主武器", ps.main.damage, ps.attr))
                if weapon == ps.sub.weapon:
                    combos.append(
                        (f"{name} 副武器", "副武器", ps.sub.damage, ps.attr))
        else:
            part = PART_ALIAS.get(equip.part, equip.part)
            if part in ("环", "冠胄", "胫甲"):
                for attr in self._enabled_attrs():
                    combos.append(("", part, None, attr))

        attempts = []
        for label, part_key, damage, attr in combos:
            pattern = self.rule.patterns.get(part_key)
            if pattern is None:
                continue
            attempts.append((label, part_key, pattern, damage, attr))
        return attempts

    def _enabled_playstyles(self) -> list[str]:
        """所选玩法名字（未配置时默认全部，供潜力判定遍历）"""
        names = list(self.rule.playstyles)
        chosen = [n for n in (self.config.get("playstyles") or names)
                  if n in self.rule.playstyles]
        return chosen or names

    def _enabled_attrs(self) -> list[str]:
        """所选玩法涉及的去重属性（非武器部位据此展开属攻等价）

        无玩法时默认通用（不做等价）。
        """
        attrs: list[str] = []
        for name in self._enabled_playstyles():
            ps = self.rule.playstyles.get(name)
            if ps and ps.attr not in attrs:
                attrs.append(ps.attr)
        return attrs or [GENERIC_ATTR]
