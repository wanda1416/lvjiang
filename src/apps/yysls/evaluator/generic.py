"""通用流派判定器（规则驱动，穷举匹配制）

GenericSchoolJudge 加载 SchoolRule（YAML 外置规则）完成 judge
（完整定级）与 check_tuning_worthiness（调律潜力）。
规则中的词条引用均为标准词条名；判定前仅做属攻归一化
（本流派属攻 → 无相攻击，见 _normalize），无符号映射层。

定级流程骨架（00-调律总说明.md 第七节）：
1. 出现废词条（词条库以外的词条，或流派不需要的神力词条）→ 垃圾
2. 部位明确需要的增伤词条缺失 → 垃圾
3. 模式命中 → 按顶级判定条件区分 顶级/优秀
4. 流派专属垃圾规则（junk_rules）触发 → 垃圾
5. 必选缺失但无废词条 → 能用

多武器角色（会心的 流派×玩法）对激活组合逐一尝试，
取评级最高的结果。
"""

from __future__ import annotations

import re

from src.apps.yysls.equip_parser import EquipmentData

from .base import JudgeResult, Rating, SchoolJudge, part_label
from .matching import _match_partial, _match_pattern
from .rules import (
    PART_ALIAS, PVP_NAMES, SCHOOL_ATTRS,
    Condition, PartPattern, SchoolRule,
)

# 评级排序（多角色/转律模拟取评级上限最高的组合）
_RANK = {Rating.JUNK: 0, Rating.USABLE: 1, Rating.EXCELLENT: 2, Rating.TOP: 3}

# 属攻词条全称（最大/最小 X 攻击）
_ATTR_RE = re.compile(r"^(最[大小])(.+)攻击$")


def _normalize(name: str, category: str, own_attrs: set[str]) -> str:
    """属攻归一化：本流派属攻 → 无相攻击；错位属攻加标记

    - 武器上：无相攻击保留全称（可命中规则）；任何流派属攻均为
      错位（加标记后必然落在词条库外 → 判垃圾）；
    - 非武器：大本属（own_attrs 内的属攻）→ 最大/最小无相攻击，
      字面 无相攻击 为错位；其他流派属攻保留全称
      （词条库列具体标准名时可命中）。
    """
    m = _ATTR_RE.match(name)
    if not m:
        return name
    attr = m.group(2)
    if category == "weapon":
        return f"{name}(错位)" if attr in SCHOOL_ATTRS else name
    if attr in own_attrs:
        return f"{m.group(1)}无相攻击"
    if attr == "无相":
        return f"{name}(错位)"
    return name


def _cond_count(cond: Condition, first_token: str,
                tokens: list[str]) -> int:
    """计数类条件的实际计数（reason 文案用）"""
    count = sum(1 for t in tokens if t in cond.symbols)
    if cond.include_first and first_token in cond.symbols:
        count += 1
    return count


class GenericSchoolJudge(SchoolJudge):
    """通用流派判定器：由 SchoolRule + 用户配置驱动"""

    def __init__(self, rule: SchoolRule, config: dict | None = None):
        super().__init__(config)
        self.rule = rule
        # 元数据改为实例属性（由规则填充，UI 接口与旧类属性同名）
        self.school_key = rule.key
        self.school_name = rule.name
        self.implemented = True
        self.has_keep_pvp = rule.has_keep_pvp
        self.needs_sub_school = rule.needs_sub_school
        self.sub_school_options = rule.sub_school_options
        self.sub_school_playstyles = rule.sub_school_playstyles
        self.sub_school_label = rule.sub_school_label
        if not rule.has_keep_pvp:
            self.keep_pvp = False

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

        # 逐个武器角色组合尝试，取最优结果
        best_label = ""
        best: JudgeResult | None = None
        for label, pattern, damage, own_attrs in attempts:
            res = self._judge_attempt(
                equip, pattern, damage, own_attrs, partial, n_free)
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

    def _judge_attempt(self, equip: EquipmentData,
                       pattern: PartPattern, damage: set[str] | None,
                       own_attrs: set[str], partial: bool,
                       n_free: int) -> JudgeResult:
        """按单个部位角色组合判定一次"""
        result = JudgeResult(equipment=equip)
        pool = self.rule.pool_set
        optional_pool = self.rule.optional_pool_set

        category = equip.category
        first_token = _normalize(equip.affixes[0].name, category, own_attrs)
        tokens = [_normalize(a.name, category, own_attrs)
                  for a in equip.affixes[1:]]

        # 首词条判断（不符即跳过）
        if first_token not in pattern.first:
            result.skipped = True
            result.reasons.append(
                f"首词条 {equip.affixes[0].name} 不符合要求")
            return result

        # 解析 DMG 占位符 → 必选槽 / 必需增伤
        dmg_set = set(damage or ())
        required: list[set[str]] = []
        for slot in pattern.required:
            cands: set[str] = set()
            for c in slot:
                if c == "DMG":
                    cands |= dmg_set
                else:
                    cands.add(c)
            required.append(cands)
        req_damage: set[str] | None = None
        if pattern.required_damage == "DMG":
            req_damage = dmg_set
        elif pattern.required_damage:
            req_damage = {pattern.required_damage}

        # keep_pvp：PVP 增伤可顶替必需增伤槽位
        substitute = pattern.damage_pvp_substitute
        if self.keep_pvp and substitute and req_damage:
            required = [slot | {substitute} if slot & req_damage else slot
                        for slot in required]

        # 允许的神力词条（必选槽候选并集 + keep_pvp 扩展）
        allowed_divine = {c for slot in required for c in slot}
        if req_damage:
            allowed_divine |= req_damage
        if self.keep_pvp:
            allowed_divine |= set(pattern.allowed_divine_pvp)
            if substitute:
                allowed_divine.add(substitute)

        if partial:
            return self._eval_partial(
                result, pattern, required, req_damage,
                allowed_divine, first_token, tokens, n_free,
                pool, optional_pool)

        # ── 完整定级 ──

        # 定级流程 1：废词条（库外词条或禁止的神力词条）→ 垃圾
        junk = [t for t in tokens
                if t not in pool and t not in allowed_divine]
        if junk:
            result.rating = Rating.JUNK
            result.reasons.append(f"垃圾词条: {'、'.join(junk)}")
            return result

        if self.keep_pvp and any(t in PVP_NAMES for t in tokens):
            result.is_pvp = True
            result.reasons.append("含 PVP 词条，保留")

        # 定级流程 2：明确需要的增伤词条缺失 → 垃圾
        if req_damage:
            accepted = set(req_damage)
            if self.keep_pvp and substitute:
                accepted.add(substitute)
            if not any(t in accepted for t in tokens):
                result.rating = Rating.JUNK
                result.reasons.append(
                    f"缺失必需增伤词条: {'/'.join(sorted(req_damage))}")
                return result

        # 定级流程 3：模式命中 → 按顶级判定条件区分 顶级/优秀
        if _match_pattern(tokens, required, pattern.optional_n,
                          optional_pool):
            if all(c.check(first_token, tokens) for c in pattern.top):
                result.rating = Rating.TOP
                result.reasons.append("命中模式，满足顶级判定条件")
            else:
                result.rating = Rating.EXCELLENT
                result.reasons.append("命中模式，未满足顶级判定条件")
            return result

        # 定级流程 4：流派专属垃圾规则 → 垃圾
        for jr in self.rule.junk_rules:
            if jr.check(first_token, tokens):
                count = _cond_count(jr, first_token, tokens)
                result.rating = Rating.JUNK
                result.reasons.append(
                    f"{'/'.join(jr.symbols)} 出现 {count} 条")
                return result

        # 定级流程 5：必选缺失但无废词条 → 能用
        result.rating = Rating.USABLE
        result.reasons.append("模式未命中但无垃圾词条")
        return result

    def _eval_partial(self, result: JudgeResult,
                      pattern: PartPattern, required: list[set[str]],
                      req_damage: set[str] | None, allowed_divine: set[str],
                      first_token: str, tokens: list[str], n_free: int,
                      pool: set[str], optional_pool: set[str]) -> JudgeResult:
        """潜力判定：万能牌 + 逐词条转律模拟，取评级上限最高者

        模拟转律（非首词条可选一条无限次转律，不产生神力词条），
        废词条可被转律洗掉，不直接判垃圾。
        """

        def evaluate(toks: list[str], n_trans: int) -> tuple[Rating, str]:
            junk = [t for t in toks
                    if t not in pool and t not in allowed_divine]
            if junk:
                return Rating.JUNK, f"垃圾词条: {'、'.join(junk)}"
            for jr in self.rule.junk_rules:
                if jr.check(first_token, toks):
                    count = _cond_count(jr, first_token, toks)
                    return (Rating.JUNK,
                            f"{'/'.join(jr.symbols)} 出现 {count} 条")
            if not _match_partial(toks, required, pattern.optional_n,
                                  n_free, n_trans, pool, optional_pool):
                return Rating.USABLE, "已有词条无法全部落入模式槽位，上限为能用"
            n_avail = n_free + n_trans
            if all(c.potential(first_token, toks, n_avail)
                   for c in pattern.top):
                return Rating.TOP, f"仍可达顶级（剩余 {n_free} 空槽）"
            return Rating.EXCELLENT, f"仍可命中模式达优秀（剩余 {n_free} 空槽）"

        # 不转律 + 逐一转掉某条非首词条，取评级上限最高者
        rating, reason = evaluate(tokens, 0)
        for i, t in enumerate(tokens):
            r2, why = evaluate(tokens[:i] + tokens[i + 1:], 1)
            if _RANK[r2] > _RANK[rating]:
                rating, reason = r2, f"模拟转律 {t}：{why}"

        if (rating is not Rating.JUNK and self.keep_pvp
                and any(t in PVP_NAMES for t in tokens)):
            result.is_pvp = True
            result.reasons.append("含 PVP 词条，保留")

        result.rating = rating
        result.reasons.append(reason)
        return result

    # ─── 判定角色展开 ──────────────────────────────────────

    def _build_attempts(
            self, equip: EquipmentData,
    ) -> list[tuple[str, PartPattern, set[str] | None, set[str]]]:
        """展开该装备在当前配置下的全部判定组合

        Returns:
            [(角色标签, 模式, 主武学增伤候选, 本属名集合), ...]
        """
        # 角色：武器按 weapons 表展开主/副；非武器按归并部位
        if equip.part == "武器":
            roles = self._weapon_roles(equip.weapon or "")
        else:
            part = PART_ALIAS.get(equip.part, equip.part)
            if part not in ("环", "冠胄", "胫甲"):
                return []
            roles = [("", part, None, self._own_attrs_all())]

        attempts = []
        for role_label, part_key, damage, own_attrs in roles:
            pattern = self.rule.patterns.get(part_key)
            if pattern is None:
                continue
            attempts.append((role_label, pattern, damage, own_attrs))
        return attempts

    def _weapon_roles(
            self, weapon: str,
    ) -> list[tuple[str, str, set[str] | None, set[str]]]:
        """该武器在当前配置下的全部角色

        weapons 表 key：default（恒启用）或 子流派[.玩法]（按勾选启用）。
        """
        roles: list[tuple[str, str, set[str] | None, set[str]]] = []
        for entry_key, entry in self.rule.weapons.items():
            if entry_key == "default":
                prefix, own_attrs = "", self._own_attrs_all()
            else:
                school, _, ps = entry_key.partition(".")
                if not self._role_enabled(school, ps):
                    continue
                sub = self.rule.sub_schools[school]
                prefix = sub.name
                if ps:
                    prefix += f"-{sub.playstyles.get(ps, ps)}"
                prefix += " "
                own_attrs = {sub.name}
            if weapon in entry.main:
                roles.append((f"{prefix}主武器" if prefix else "",
                              "主武器", {entry.main[weapon]}, own_attrs))
            if weapon in entry.sub:
                roles.append((f"{prefix}副武器" if prefix else "",
                              "副武器", None, own_attrs))
        return roles

    def _role_enabled(self, school: str, ps: str) -> bool:
        """武器角色条目 (子流派, 玩法) 是否被当前配置启用"""
        if school not in self._enabled_subs():
            return False
        if not ps:
            return True
        playstyles = self.rule.sub_schools[school].playstyles
        ps_cfg = (self.config.get("playstyles") or {}).get(school)
        enabled_ps = [p for p in (ps_cfg or list(playstyles))
                      if p in playstyles]
        return ps in enabled_ps

    def _enabled_subs(self) -> list[str]:
        """所选子流派（未配置时默认全部，供潜力判定遍历）"""
        keys = list(self.rule.sub_schools)
        subs = self.config.get("sub_schools") or keys
        chosen = [s for s in subs if s in self.rule.sub_schools]
        return chosen or keys

    def _own_attrs_all(self) -> set[str]:
        """大本属名集合（own_attr 固定属名 或 跟随勾选的子流派名）"""
        if self.rule.own_attr == "from_sub_schools":
            return {self.rule.sub_schools[s].name
                    for s in self._enabled_subs()}
        return {self.rule.own_attr} if self.rule.own_attr else set()

    # ─── 内部辅助 ──────────────────────────────────────────

    @staticmethod
    def _quality_ok(equip: EquipmentData) -> bool:
        """品阶筛选：武器/首饰仅金色，防具紫色及金色"""
        if equip.category == "armor":
            return equip.quality in ("purple", "gold")
        return equip.quality == "gold"
