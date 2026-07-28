"""通用调律规则判定器（规则驱动，三档条件线性判定制）

GenericTuningJudge 加载 TuningRule（YAML 外置规则）完成 judge
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

武器部位按用户勾选的玩法设定（如 纯唐/双切）逐一尝试，装备武器名
匹配主/副武器即产生一次判定（携带该侧增伤要求与玩法属性），取评级
最高者。非武器部位按勾选玩法的不同属性去重展开（各属性各跑一次取
最优）。

非武器部位判定时按玩法属性做「属攻→无相」等价：把装备上该属性的
最大/最小属攻视作无相词条再匹配（规则非武器部位统一写无相词条即
可泛化到各属性流派）；武器部位保持字面属攻引用不做等价。

品阶门槛与 keep_pvp 词条等价从 tuning_base.yaml 读取：
- 品阶门槛按标准部位（武器/环/佩/防具四件）配置，规则级
  quality_thresholds 可按部位覆盖全局默认；
- 胫甲（含腕甲）：对玩家单位增效 视作 对首领单位增伤；
- 冠胄（含头/胸甲）：单体类奇术增伤 临时并入词条库。
"""

from __future__ import annotations

from src.apps.yysls.equip_parser import EquipmentData

from .base import JudgeResult, Rating, TuningJudge, part_label
from .tuning_rules import (GENERIC_ATTR, PART_ALIAS, Condition, PartPattern,
                           TuningRule, attr_equivalence, get_tuning_base)

# 评级排序（多玩法/转律模拟取评级上限最高的组合）
_RANK = {Rating.JUNK: 0, Rating.USABLE: 1, Rating.EXCELLENT: 2, Rating.TOP: 3}

# 武器部位 key（不做属攻→无相等价）
_WEAPON_PARTS = ("主武器", "副武器")


def _group_text(group: list[Condition]) -> str:
    """条件组 reason 文案"""
    return "；".join(f"{c.kind}[{'/'.join(c.symbols)}]" for c in group)


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
        pool = self.rule.pool_set
        tuning_base = get_tuning_base()

        first_token = equip.affixes[0].name
        tokens = [a.name for a in equip.affixes[1:]]

        # 首词条判断（不符即跳过）
        if first_token not in pattern.first:
            result.skipped = True
            result.reasons.append(f"首词条 {first_token} 不符合要求")
            return result

        # 非武器部位：按玩法属性做「属攻→无相」等价
        if part_key not in _WEAPON_PARTS:
            equiv = attr_equivalence(attr)
            if equiv:
                first_token = equiv.get(first_token, first_token)
                tokens = [equiv.get(t, t) for t in tokens]

        # keep_pvp：部位级 PVP 词条等价处理（读 tuning_base）
        pvp_hit = any(t in tuning_base.pvp_names for t in tokens)
        if self.keep_pvp:
            part_rule = tuning_base.pvp_parts.get(part_key)
            if part_rule:
                for src, dst in part_rule.substitutions.items():
                    if src not in pool:
                        tokens = [dst if t == src else t for t in tokens]
                if part_rule.add_to_pool:
                    pool = pool | set(part_rule.add_to_pool)

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
        """潜力判定：万能牌 + 以 transmute_priority 为序的转律模拟

        模拟转律：非首词条可选一条无限次转律（不产生神力词条），
        转出选择按价值最低者：先转池外垃圾词条，其次转
        transmute_priority 排位最靠后（价值最低）的普通词条；转入
        则假定得到最优词条（空槽同为万能牌）。增伤词条不参与转
        出；缺失增伤只能由空槽补（转律不产生神力词条）。junk/usable
        条件按 still_hits 求值，top 条件按 potential 求值。
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

        priority = self.rule.transmute_priority

        def sac_score(t: str) -> int:
            """转出优先级（越大越该转出）；增伤词条不参与转出"""
            if t == damage:
                return -1
            if t not in pool:
                return 10000  # 池外垃圾优先转出
            if t in priority:
                return priority.index(t)  # 价值降序：靠后 = 越该转
            return 1000  # 池内但未列入优先级：低价值

        # 不转律基线 + 转掉「最该转出」的一条，取评级上限最高者
        rating, reason = evaluate(tokens, 0)
        if tokens:
            idx = max(range(len(tokens)), key=lambda i: sac_score(tokens[i]))
            if sac_score(tokens[idx]) >= 0:  # 存在可转出词条（非增伤）
                r2, why = evaluate(tokens[:idx] + tokens[idx + 1:], 1)
                if _RANK[r2] > _RANK[rating]:
                    rating, reason = r2, f"模拟转律 {tokens[idx]}：{why}"

        if rating is not Rating.JUNK and self.keep_pvp and pvp_hit:
            result.is_pvp = True
            result.reasons.append("含 PVP 词条，保留")

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
