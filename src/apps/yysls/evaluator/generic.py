"""通用流派判定器（规则驱动，三档条件线性判定制）

GenericSchoolJudge 加载 SchoolRule（YAML 外置规则）完成 judge
（完整定级）与 check_tuning_worthiness（调律潜力）。
规则中的词条引用均为标准词条名，直接写具体属攻词条
（如 最大无相攻击/最大鸣金攻击），无归一化与符号映射层。

定级流程（线性，无模式匹配）：
1. 首词条不在 first → 跳过（无调律价值）
2. 非首词条存在 池外且非本次增伤 的词条 → 垃圾
3. 本次武器规则要求增伤但词条缺失 → 垃圾
4. junk_conditions 命中 → 垃圾；usable_conditions 命中 → 能用；
   top_conditions 命中 → 顶级；全不命中 → 优秀
   （每档条件组间 OR、组内 AND）

武器部位按用户勾选的武器规则（如 纯唐/双切）逐一尝试，装备武器名
匹配主/副武器即产生一次判定（携带该侧增伤要求），取评级最高者。

全局 keep_pvp 开启时做部位级 PVP 词条等价处理：
- 胫甲（含腕甲）：对玩家单位增效 视作 对首领单位增伤；
- 冠胄（含头/胸甲）：单体类奇术增伤 临时并入词条库。
"""

from __future__ import annotations

from src.apps.yysls.equip_parser import EquipmentData

from .base import JudgeResult, Rating, SchoolJudge, part_label
from .rules import PART_ALIAS, PVP_NAMES, Condition, PartPattern, SchoolRule

# 评级排序（多武器规则/转律模拟取评级上限最高的组合）
_RANK = {Rating.JUNK: 0, Rating.USABLE: 1, Rating.EXCELLENT: 2, Rating.TOP: 3}


def _group_text(group: list[Condition]) -> str:
    """条件组 reason 文案"""
    return "；".join(f"{c.kind}[{'/'.join(c.symbols)}]" for c in group)


class GenericSchoolJudge(SchoolJudge):
    """通用流派判定器：由 SchoolRule + 用户配置驱动"""

    def __init__(self, rule: SchoolRule, config: dict | None = None):
        super().__init__(config)
        self.rule = rule
        # 元数据改为实例属性（由规则填充，UI 接口与旧类属性同名）
        self.school_key = rule.key
        self.school_name = rule.name
        self.implemented = True
        self.weapon_rule_options = rule.weapon_rule_options

    def judge(self, equip: EquipmentData) -> JudgeResult:
        return self._run(equip, partial=False)

    def check_tuning_worthiness(self, equip: EquipmentData) -> JudgeResult:
        """调律潜力判定：空词条槽视作万能牌 + 模拟转律，返回评级上限"""
        return self._run(equip, partial=True)

    # ─── 主流程 ────────────────────────────────────────────

    def _run(self, equip: EquipmentData, partial: bool) -> JudgeResult:
        result = JudgeResult(equipment=equip)

        attempts = self._build_attempts(equip)
        if not attempts:
            result.skipped = True
            result.not_applicable = True
            result.reasons.append(
                f"{part_label(equip)} 不在 {self.school_name} 判定范围内")
            return result

        # 品阶筛选（quality 未识别时跳过此步继续判定）
        pre_reasons: list[str] = []
        if equip.quality is None:
            pre_reasons.append("品阶未识别，跳过品阶筛选")
        elif not self._quality_ok(equip):
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
        for label, part_key, pattern, damage in attempts:
            res = self._judge_attempt(
                equip, part_key, pattern, damage, partial, n_free)
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
                       partial: bool, n_free: int) -> JudgeResult:
        """按单个部位/武器规则组合判定一次"""
        result = JudgeResult(equipment=equip)
        pool = self.rule.pool_set

        first_token = equip.affixes[0].name
        tokens = [a.name for a in equip.affixes[1:]]

        # 首词条判断（不符即跳过）
        if first_token not in pattern.first:
            result.skipped = True
            result.reasons.append(f"首词条 {first_token} 不符合要求")
            return result

        # 全局 keep_pvp：部位级 PVP 词条等价处理
        pvp_hit = any(t in PVP_NAMES for t in tokens)
        if self.keep_pvp:
            if part_key == "胫甲" and "对玩家单位增效" not in pool:
                tokens = ["对首领单位增伤" if t == "对玩家单位增效" else t
                          for t in tokens]
            elif part_key == "冠胄":
                pool = pool | {"单体类奇术增伤"}

        if partial:
            return self._eval_partial(
                result, pattern, damage, pool,
                first_token, tokens, n_free, pvp_hit)

        # ── 完整定级 ──

        # 流程 2：池外且非本次增伤的词条 → 垃圾
        junk = [t for t in tokens if t not in pool and t != damage]
        if junk:
            result.rating = Rating.JUNK
            result.reasons.append(f"垃圾词条: {'、'.join(junk)}")
            return result

        if self.keep_pvp and pvp_hit:
            result.is_pvp = True
            result.reasons.append("含 PVP 词条，保留")

        # 流程 3：本次武器规则要求增伤但词条缺失 → 垃圾
        if damage and damage not in tokens:
            result.rating = Rating.JUNK
            result.reasons.append(f"缺失必需增伤词条: {damage}")
            return result

        # 流程 4：三档条件 junk → usable → top，全不命中默认优秀
        for group in pattern.junk_conditions:
            if all(c.check(first_token, tokens) for c in group):
                result.rating = Rating.JUNK
                result.reasons.append(f"命中垃圾条件（{_group_text(group)}）")
                return result
        for group in pattern.usable_conditions:
            if all(c.check(first_token, tokens) for c in group):
                result.rating = Rating.USABLE
                result.reasons.append(f"命中能用条件（{_group_text(group)}）")
                return result
        for group in pattern.top_conditions:
            if all(c.check(first_token, tokens) for c in group):
                result.rating = Rating.TOP
                result.reasons.append("满足顶级判定条件")
                return result
        result.rating = Rating.EXCELLENT
        result.reasons.append("词条合格，未满足顶级判定条件")
        return result

    def _eval_partial(self, result: JudgeResult, pattern: PartPattern,
                      damage: str | None, pool: set[str],
                      first_token: str, tokens: list[str], n_free: int,
                      pvp_hit: bool) -> JudgeResult:
        """潜力判定：万能牌 + 逐词条转律模拟，取评级上限最高者

        模拟转律（非首词条可选一条无限次转律，不产生神力词条），
        废词条可被转律洗掉，不直接判垃圾；缺失增伤只能由空槽补
        （转律不产生神力词条）。junk/usable 条件按 still_hits 求值
        （空槽按最优填法仍无法解除命中才算命中），top 条件按
        potential 求值（组内全部原语可满足才算组命中）。
        """

        def evaluate(toks: list[str], n_trans: int) -> tuple[Rating, str]:
            junk = [t for t in toks if t not in pool and t != damage]
            if junk:
                return Rating.JUNK, f"垃圾词条: {'、'.join(junk)}"
            n_avail = n_free + n_trans
            if damage and damage not in toks:
                if n_free < 1:
                    return Rating.JUNK, f"缺失必需增伤词条: {damage}"
                n_avail -= 1  # 一张万能牌用于补增伤
            for group in pattern.junk_conditions:
                if all(c.still_hits(first_token, toks, n_avail)
                       for c in group):
                    return (Rating.JUNK,
                            f"命中垃圾条件（{_group_text(group)}）")
            for group in pattern.usable_conditions:
                if all(c.still_hits(first_token, toks, n_avail)
                       for c in group):
                    return (Rating.USABLE,
                            f"上限为能用（{_group_text(group)}）")
            for group in pattern.top_conditions:
                if all(c.potential(first_token, toks, n_avail)
                       for c in group):
                    return Rating.TOP, f"仍可达顶级（剩余 {n_free} 空槽）"
            return Rating.EXCELLENT, f"仍可达优秀（剩余 {n_free} 空槽）"

        # 不转律 + 逐一转掉某条非首词条，取评级上限最高者
        rating, reason = evaluate(tokens, 0)
        for i, t in enumerate(tokens):
            r2, why = evaluate(tokens[:i] + tokens[i + 1:], 1)
            if _RANK[r2] > _RANK[rating]:
                rating, reason = r2, f"模拟转律 {t}：{why}"

        if rating is not Rating.JUNK and self.keep_pvp and pvp_hit:
            result.is_pvp = True
            result.reasons.append("含 PVP 词条，保留")

        result.rating = rating
        result.reasons.append(reason)
        return result

    # ─── 判定组合展开 ──────────────────────────────────────

    def _build_attempts(
            self, equip: EquipmentData,
    ) -> list[tuple[str, str, PartPattern, str | None]]:
        """展开该装备在当前配置下的全部判定组合

        Returns:
            [(标签, 部位 key, 模式, 增伤词条名或 None), ...]
        """
        combos: list[tuple[str, str, str | None]] = []
        if equip.part == "武器":
            weapon = equip.weapon or ""
            for name in self._enabled_weapon_rules():
                wr = self.rule.weapon_rules[name]
                if weapon == wr.main.weapon:
                    combos.append((f"{name} 主武器", "主武器", wr.main.damage))
                if weapon == wr.sub.weapon:
                    combos.append((f"{name} 副武器", "副武器", wr.sub.damage))
        else:
            part = PART_ALIAS.get(equip.part, equip.part)
            if part in ("环", "冠胄", "胫甲"):
                combos.append(("", part, None))

        attempts = []
        for label, part_key, damage in combos:
            pattern = self.rule.patterns.get(part_key)
            if pattern is None:
                continue
            attempts.append((label, part_key, pattern, damage))
        return attempts

    def _enabled_weapon_rules(self) -> list[str]:
        """所选武器规则名字（未配置时默认全部，供潜力判定遍历）"""
        names = list(self.rule.weapon_rules)
        chosen = [n for n in (self.config.get("weapon_rules") or names)
                  if n in self.rule.weapon_rules]
        return chosen or names

    # ─── 内部辅助 ──────────────────────────────────────────

    @staticmethod
    def _quality_ok(equip: EquipmentData) -> bool:
        """品阶筛选：武器/首饰仅金色，防具紫色及金色"""
        if equip.category == "armor":
            return equip.quality in ("purple", "gold")
        return equip.quality == "gold"
