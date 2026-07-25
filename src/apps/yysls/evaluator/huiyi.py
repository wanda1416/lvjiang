"""会意流派-通用 判定器（穷举匹配制）

规格来源：docs/10-game/11-调律说明文档/01-会意流派调律说明.md

每个部位定义一个统一模式（必选槽位 + 可选候选池），按定级流程判定：
1. 出现垃圾词条 → 垃圾
2. 部位明确需要的增伤词条缺失 → 垃圾
3. 模式命中 → 按顶级判定条件区分 顶级/优秀
4. 非增伤必选缺失，或可选槽被 1 条会心/精准顶替 → 能用
5. 会心/精准 ≥ 2 条 → 垃圾
"""

from src.apps.yysls.equip_parser import EquipmentData

from .base import JudgeResult, Rating, SchoolJudge, part_label


# ─── 词条归一化 ────────────────────────────────────────────
# 全称 → 模式符号；大无相在非武器部位表示大本属（鸣金虹即最大鸣金攻击）。
# 错位属攻（武器上的鸣金、非武器上的无相）保留全称，不入池，直接判垃圾。

_SYMBOL_MAP = {
    "最大外功攻击": "大外",
    "劲": "劲",
    "势": "势",
    "会意率": "会意",
    "会心率": "会心",
    "精准率": "精准",
}

# 全局词条池（第四节）中的非神力符号；池外词条即垃圾词条
_POOL_SYMBOLS = {"大外", "劲", "势", "会意", "会心", "精准", "大无相"}

# 可选候选池：(劲/势/会意/大无相) * N，允许重复
_OPTIONAL_POOL = {"劲", "势", "会意", "大无相"}

# PVP 词条（keep_pvp 开启时视作有效）
_PVP_NAMES = {"单体类奇术增伤", "对玩家单位增效"}

# 评级排序（转律模拟取评级上限最高的变体）
_RANK = {Rating.JUNK: 0, Rating.USABLE: 1, Rating.EXCELLENT: 2, Rating.TOP: 3}


def _normalize(name: str, category: str) -> str:
    """词条全称 → 模式符号；神力词条、错位属攻及池外词条保留全称"""
    if name in _SYMBOL_MAP:
        return _SYMBOL_MAP[name]
    if name == "最大无相攻击" and category == "weapon":
        return "大无相"
    if name == "最大鸣金攻击" and category != "weapon":
        return "大无相"
    return name


# ─── 各部位模式定义（第六节） ──────────────────────────────
# first:          首词条筛选（第二节，武器次选势）
# pattern_first:  模式要求的首词条（武器模式只认大外，势首最高能用）
# required:       非首词条必选槽位（每槽一个候选集合）
# required_damage: 明确需要的增伤词条（缺失直接判垃圾；None 表示无要求）
# optional_n:     可选槽数量
# top:            顶级判定条件（命中模式后，作用于非首词条符号集合）

_TOP_NO_HUIYI_DWX = "no_huiyi_dwx"    # 未出现 会意/大无相 → 顶级
_TOP_JIN_AND_SHI = "jin_and_shi"      # 同时出现 劲 + 势 → 顶级
_TOP_NO_DWX = "no_dwx"                # 未出现 大无相 → 顶级

# 武器模式按武器类型定义（部位「武器」不区分主/副，会意流派仅 剑/枪 适用）
HUIYI_WEAPON_PATTERNS: dict[str, dict] = {
    "剑": {
        "first": {"大外", "势"},
        "pattern_first": {"大外"},
        "required": [{"剑武学增伤"}, {"大外"}, {"劲", "势"}],
        "required_damage": {"剑武学增伤"},
        "optional_n": 1,
        "top": _TOP_NO_HUIYI_DWX,
    },
    "枪": {
        "first": {"大外", "势"},
        "pattern_first": {"大外"},
        "required": [{"大外"}, {"劲", "势"}],
        "required_damage": None,
        "optional_n": 2,
        "top": _TOP_JIN_AND_SHI,
    },
}

# 非武器部位模式（环/佩、冠胄/胸甲、胫甲/腕甲 两两同模式）
HUIYI_PART_PATTERNS: dict[str, dict] = {
    "环": {
        "first": {"大外"},
        "pattern_first": {"大外"},
        "required": [{"大外"}, {"全武学增效"}],
        "required_damage": {"全武学增效"},
        "optional_n": 2,
        "top": _TOP_JIN_AND_SHI,
    },
    "冠胄": {
        "first": {"会意"},
        "pattern_first": {"会意"},
        "required": [{"大外"}, {"劲", "势"}],
        "required_damage": None,
        "optional_n": 2,
        "top": _TOP_NO_DWX,
    },
    "胫甲": {
        "first": {"劲"},
        "pattern_first": {"劲"},
        "required": [{"对首领单位增伤"}, {"大外"}],
        "required_damage": {"对首领单位增伤"},
        "optional_n": 2,
        "top": _TOP_JIN_AND_SHI,
    },
}
_PART_ALIAS = {"佩": "环", "胸甲": "冠胄", "腕甲": "胫甲"}


def _get_spec(equip: EquipmentData) -> tuple[str, dict] | None:
    """按 部位+武器 两维查找模式：返回 (规范化 key, 模式)，无定义为 None

    部位为武器时按具体武器类型（剑/枪）取模式；其余部位按
    归并后的部位 key（佩→环、胸甲→冠胄、腕甲→胫甲）取模式。
    """
    if equip.part == "武器":
        weapon = equip.weapon or ""
        spec = HUIYI_WEAPON_PATTERNS.get(weapon)
        return (weapon, spec) if spec else None
    key = _PART_ALIAS.get(equip.part, equip.part)
    spec = HUIYI_PART_PATTERNS.get(key)
    return (key, spec) if spec else None


def _check_top(rule: str, tokens: list[str]) -> bool:
    """顶级判定条件（tokens 为非首词条符号列表）"""
    s = set(tokens)
    if rule == _TOP_NO_HUIYI_DWX:
        return not (s & {"会意", "大无相"})
    if rule == _TOP_JIN_AND_SHI:
        return {"劲", "势"} <= s
    if rule == _TOP_NO_DWX:
        return "大无相" not in s
    return False


def _match_pattern(tokens: list[str], required: list[set[str]],
                   optional_n: int,
                   optional_pool: set[str] | None = None) -> bool:
    """穷举匹配：tokens 必须恰好填满 必选槽 + 可选槽（无序，允许池内重复）

    optional_pool 缺省为会意流派候选池；其他流派可按部位传入自己的池。
    """
    pool = _OPTIONAL_POOL if optional_pool is None else optional_pool
    if len(tokens) != len(required) + optional_n:
        return False

    def backtrack(slot_idx: int, remaining: list[str]) -> bool:
        if slot_idx == len(required):
            return all(t in pool for t in remaining)
        for i, t in enumerate(remaining):
            if t in required[slot_idx]:
                if backtrack(slot_idx + 1, remaining[:i] + remaining[i + 1:]):
                    return True
        return False

    return backtrack(0, tokens)


def _match_partial(tokens: list[str], required: list[set[str]],
                   optional_n: int, n_free: int, n_trans: int = 0,
                   pool_symbols: set[str] | None = None,
                   optional_pool: set[str] | None = None) -> bool:
    """部分匹配：tokens + n_free 张万能牌 + n_trans 张转律牌 能否填满槽位

    空词条槽视作万能牌（可变成任意需要的词条）；转律牌来自转律
    模拟，因转律不产生神力词条，只能补足含普通词条候选的槽位
    （纯神力槽位须由万能牌补足，pool_symbols 用于区分普通符号）。
    已有 token 必须全部落入某个必选槽或可选池槽位，剩余槽位由
    两类牌补足。optional_pool 缺省为会意流派候选池。
    """
    pool = _OPTIONAL_POOL if optional_pool is None else optional_pool
    if len(tokens) + n_free + n_trans != len(required) + optional_n:
        return False

    def backtrack(idx: int, slots: list[set[str]], opt_left: int) -> bool:
        if idx == len(tokens):
            # 剩余槽位由万能牌/转律牌补足；纯神力槽位只能用万能牌
            if n_trans and pool_symbols is not None:
                divine_only = sum(1 for s in slots if not (s & pool_symbols))
                return divine_only <= n_free
            return True
        t = tokens[idx]
        if opt_left > 0 and t in pool:
            if backtrack(idx + 1, slots, opt_left - 1):
                return True
        for j, slot in enumerate(slots):
            if t in slot:
                if backtrack(idx + 1, slots[:j] + slots[j + 1:], opt_left):
                    return True
        return False

    return backtrack(0, required, optional_n)


def _potential_top(rule: str, tokens: list[str], n_free: int) -> bool:
    """顶级潜力判定：剩余空槽能否使顶级条件仍有机会成立"""
    s = set(tokens)
    if rule == _TOP_NO_HUIYI_DWX:
        return not (s & {"会意", "大无相"})
    if rule == _TOP_JIN_AND_SHI:
        return len({"劲", "势"} - s) <= n_free
    if rule == _TOP_NO_DWX:
        return "大无相" not in s
    return False


# ─── 判定器实现 ────────────────────────────────────────────

class HuiyiGeneralJudge(SchoolJudge):
    """会意流派-通用 判定器"""

    school_key = "huiyi_general"
    school_name = "会意流派-通用"
    implemented = True
    has_keep_pvp = True

    def judge(self, equip: EquipmentData) -> JudgeResult:
        result = JudgeResult(equipment=equip)

        found = _get_spec(equip)
        if found is None:
            result.skipped = True
            result.not_applicable = True
            result.reasons.append(f"{part_label(equip)} 无会意流派模式定义")
            return result
        spec_key, spec = found

        # 一、品阶筛选（quality 未识别时跳过此步继续判定）
        if equip.quality is None:
            result.reasons.append("品阶未识别，跳过品阶筛选")
        elif not self._quality_ok(equip):
            result.skipped = True
            result.reasons.append(f"品阶 {equip.quality} 无调律价值")
            return result

        if not equip.affixes:
            result.skipped = True
            result.reasons.append("无词条数据")
            return result

        category = equip.category
        first_token = _normalize(equip.affixes[0].name, category)
        tokens = [_normalize(a.name, category) for a in equip.affixes[1:]]

        # 二、首词条判断
        if first_token not in spec["first"]:
            result.skipped = True
            result.reasons.append(f"首词条 {equip.affixes[0].name} 不符合要求")
            return result

        # 允许的神力词条（含 keep_pvp 扩展）
        allowed_divine = self._allowed_divine(spec_key)

        # 定级流程 1：垃圾词条（池外词条或禁止的神力词条）
        junk = [t for t in tokens
                if t not in _POOL_SYMBOLS and t not in allowed_divine]
        if junk:
            result.rating = Rating.JUNK
            result.reasons.append(f"垃圾词条: {'、'.join(junk)}")
            return result

        if self.keep_pvp and any(t in _PVP_NAMES for t in tokens):
            result.is_pvp = True
            result.reasons.append("含 PVP 词条，保留")

        # 定级流程 2：明确需要的增伤词条缺失 → 垃圾
        required_damage = spec["required_damage"]
        if required_damage is not None:
            accepted = set(required_damage)
            if self.keep_pvp and spec_key == "胫甲":
                accepted |= {"对玩家单位增效"}
            if not any(t in accepted for t in tokens):
                result.rating = Rating.JUNK
                result.reasons.append(
                    f"缺失必需增伤词条: {'/'.join(required_damage)}")
                return result

        # 定级流程 3：模式匹配 → 顶级/优秀
        required = spec["required"]
        if self.keep_pvp and spec_key == "胫甲":
            # 对玩家增效可顶替对首领增伤槽位（视作有效）
            required = [
                slot | {"对玩家单位增效"} if "对首领单位增伤" in slot else slot
                for slot in required
            ]
        if (first_token in spec["pattern_first"]
                and _match_pattern(tokens, required, spec["optional_n"])):
            if _check_top(spec["top"], tokens):
                result.rating = Rating.TOP
                result.reasons.append("命中模式，满足顶级判定条件")
            else:
                result.rating = Rating.EXCELLENT
                result.reasons.append("命中模式，未满足顶级判定条件")
            return result

        # 定级流程 5：会心/精准 ≥ 2 条 → 垃圾
        rate_count = sum(1 for t in tokens if t in ("会心", "精准"))
        if rate_count >= 2:
            result.rating = Rating.JUNK
            result.reasons.append(f"会心/精准 出现 {rate_count} 条")
            return result

        # 定级流程 4：必选缺失或被 1 条会心/精准顶替，无垃圾词条 → 能用
        result.rating = Rating.USABLE
        result.reasons.append("模式未命中但无垃圾词条")
        return result

    # ─── 调律潜力判定 ──────────────────────────────────

    def check_tuning_worthiness(self, equip: EquipmentData) -> JudgeResult:
        """调律潜力判定：空词条槽视作万能牌，返回评级上限

        与 judge 的筛选逻辑一致，区别在于：
        - 模式匹配允许未出齐的词条由万能牌补位；
        - 模拟转律（非首词条可选一条无限次转律，不产生神力
          词条）：逐一尝试转掉某条词条换一张转律牌，取评级上限
          最高的变体（废词条可被转律洗掉，不直接判垃圾）。
        """
        result = JudgeResult(equipment=equip)

        found = _get_spec(equip)
        if found is None:
            result.skipped = True
            result.not_applicable = True
            result.reasons.append(f"{part_label(equip)} 无会意流派模式定义")
            return result
        spec_key, spec = found

        if equip.quality is None:
            result.reasons.append("品阶未识别，跳过品阶筛选")
        elif not self._quality_ok(equip):
            result.skipped = True
            result.reasons.append(f"品阶 {equip.quality} 无调律价值")
            return result

        if not equip.affixes:
            result.skipped = True
            result.reasons.append("无词条数据")
            return result

        category = equip.category
        first_token = _normalize(equip.affixes[0].name, category)
        tokens = [_normalize(a.name, category) for a in equip.affixes[1:]]
        n_free = 5 - len(equip.affixes)  # 空词条槽数（万能牌）

        # 首词条不满足模式要求 → 永远到不了 优秀/顶级
        if first_token not in spec["pattern_first"]:
            if first_token in spec["first"]:
                result.rating = Rating.USABLE
                result.reasons.append(
                    f"首词条 {equip.affixes[0].name} 评级上限为能用")
            else:
                result.skipped = True
                result.reasons.append(
                    f"首词条 {equip.affixes[0].name} 不符合要求")
            return result

        allowed_divine = self._allowed_divine(spec_key)

        required = spec["required"]
        if self.keep_pvp and spec_key == "胫甲":
            required = [
                slot | {"对玩家单位增效"} if "对首领单位增伤" in slot else slot
                for slot in required
            ]

        def evaluate(toks: list[str], n_trans: int) -> tuple[Rating, str]:
            """单个变体（不转律 / 转掉某条词条）的评级上限"""
            junk = [t for t in toks
                    if t not in _POOL_SYMBOLS and t not in allowed_divine]
            if junk:
                return Rating.JUNK, f"垃圾词条: {'、'.join(junk)}"
            rate_count = sum(1 for t in toks if t in ("会心", "精准"))
            if rate_count >= 2:
                return Rating.JUNK, f"会心/精准 出现 {rate_count} 条"
            if not _match_partial(toks, required, spec["optional_n"],
                                  n_free, n_trans, _POOL_SYMBOLS):
                return Rating.USABLE, "已有词条无法全部落入模式槽位，上限为能用"
            if _potential_top(spec["top"], toks, n_free + n_trans):
                return Rating.TOP, f"仍可达顶级（剩余 {n_free} 空槽）"
            return Rating.EXCELLENT, f"仍可命中模式达优秀（剩余 {n_free} 空槽）"

        # 转律模拟：不转律 + 逐一转掉某条非首词条，取评级上限最高者
        rating, reason = evaluate(tokens, 0)
        for i, t in enumerate(tokens):
            r2, why = evaluate(tokens[:i] + tokens[i + 1:], 1)
            if _RANK[r2] > _RANK[rating]:
                rating, reason = r2, f"模拟转律 {t}：{why}"

        if (rating is not Rating.JUNK and self.keep_pvp
                and any(t in _PVP_NAMES for t in tokens)):
            result.is_pvp = True
            result.reasons.append("含 PVP 词条，保留")

        result.rating = rating
        result.reasons.append(reason)
        return result

    # ─── 内部辅助 ──────────────────────────────────────────

    @staticmethod
    def _quality_ok(equip: EquipmentData) -> bool:
        """品阶筛选：武器/首饰仅金色，防具紫色及金色"""
        if equip.category == "armor":
            return equip.quality in ("purple", "gold")
        return equip.quality == "gold"

    def _allowed_divine(self, spec_key: str) -> set[str]:
        """该模式 key 允许出现的神力词条（其余神力词条视为垃圾词条）"""
        allowed: set[str] = set()
        if spec_key == "剑":
            allowed = {"剑武学增伤"}
        elif spec_key == "环":
            allowed = {"全武学增效"}
        elif spec_key == "冠胄":
            if self.keep_pvp:
                allowed = {"单体类奇术增伤"}
        elif spec_key == "胫甲":
            allowed = {"对首领单位增伤"}
            if self.keep_pvp:
                allowed |= {"对玩家单位增效"}
        return allowed
