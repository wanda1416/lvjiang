"""治疗流派 判定器（纯奶/火拳奶 子玩法合并，穷举匹配制）

规格来源：docs/10-game/04-tuning-mechanics.md
「治疗流派纯奶/火拳奶各部位词条要求」+「各流派需要的武学增伤说明」。

治疗流派即 牵丝·霖：主武器为 扇，副武器为 伞。
纯奶 主武器需要 扇武学增效；火拳奶 无主武学增伤要求；
两者均不需要伞武学增伤（副武器出现任何武学增伤即垃圾）。

UI 上合并为一个流派条目，纯奶/火拳奶 为可多选的子玩法，
判定时逐个所选子玩法尝试，取最优结果（与会心多角色尝试同构）。
"""

from src.apps.yysls.equip_parser import EquipmentData

from .base import JudgeResult, Rating, SchoolJudge, part_label
from .huiyi import _match_partial, _match_pattern  # 共用匹配算法


# ─── 词条归一化 ────────────────────────────────────────────
# 大无相/小无相 在非武器部位表示 大本属/小本属（牵丝·霖 即 牵丝攻击）；
# 「无相」「本属」是池内但不匹配任何槽位的符号（不判垃圾，最高能用）。

_SYMBOL_MAP = {
    "最大外功攻击": "大外",
    "最小外功攻击": "小外",
    "劲": "劲",
    "敏": "敏",
    "会心率": "会心",
}

# 治疗流派词条池；池外词条（如 势、会意率、他系属攻）即垃圾词条
_POOL_SYMBOLS = {"大外", "小外", "劲", "敏", "会心",
                 "大无相", "小无相", "无相", "本属"}

_RANK = {Rating.JUNK: 0, Rating.USABLE: 1, Rating.EXCELLENT: 2, Rating.TOP: 3}


def _normalize(name: str, category: str) -> str:
    """词条全称 → 模式符号；神力词条及池外词条保留全称"""
    if name in _SYMBOL_MAP:
        return _SYMBOL_MAP[name]
    if name == "最大无相攻击":
        return "大无相" if category == "weapon" else "无相"
    if name == "最小无相攻击":
        return "小无相" if category == "weapon" else "无相"
    if name == "最大牵丝攻击":
        return "本属" if category == "weapon" else "大无相"
    if name == "最小牵丝攻击":
        return "本属" if category == "weapon" else "小无相"
    return name


# ─── 各部位模式定义 ────────────────────────────────────────
# first:           首词条要求（无次选宽容档，不符即跳过）
# required:        非首词条必选槽位（每槽一个候选集合）
# required_damage: 明确需要的增伤词条（缺失直接判垃圾；None 表示无要求）
# optional_n:      可选槽数量
# optional_pool:   可选槽候选池（主武器池不含会心）
# top:             顶级判定条件（作用于非首词条符号集合）

_TOP_NO_WUXIANG = "no_wuxiang"          # 未出现 大无相/小无相 → 顶级
_TOP_NO_WX_MIN_HX = "no_wx_min_hx"      # 且 敏/会心 未同时出现 → 顶级

_JIN_SLOT = {"大外", "小外", "劲"}       # 大外/小外/劲 通用槽位
_MAIN_POOL = {"大外", "小外", "劲", "敏", "大无相", "小无相"}
_COMMON_POOL = _MAIN_POOL | {"会心"}

# 纯奶各部位模式
_PURE_PATTERNS: dict[str, dict] = {
    "主武器": {
        "first": {"大外", "小外"},
        "required": [{"扇武学增效"}, set(_JIN_SLOT), set(_JIN_SLOT)],
        "required_damage": {"扇武学增效"},
        "optional_n": 1,
        "optional_pool": _MAIN_POOL,
        "top": _TOP_NO_WUXIANG,
    },
    "副武器": {
        "first": {"大外", "小外"},
        "required": [set(_JIN_SLOT), set(_JIN_SLOT)],
        "required_damage": None,
        "optional_n": 2,
        "optional_pool": _COMMON_POOL,
        "top": _TOP_NO_WX_MIN_HX,
    },
    "环": {
        "first": {"大外", "小外"},
        "required": [{"全武学增效"}, set(_JIN_SLOT)],
        "required_damage": {"全武学增效"},
        "optional_n": 2,
        "optional_pool": _COMMON_POOL,
        "top": _TOP_NO_WX_MIN_HX,
    },
    "冠胄": {
        "first": {"会心"},
        "required": [set(_JIN_SLOT), set(_JIN_SLOT)],
        "required_damage": None,
        "optional_n": 2,
        "optional_pool": _COMMON_POOL,
        "top": _TOP_NO_WX_MIN_HX,
    },
    "胫甲": {
        "first": {"劲"},
        "required": [{"对玩家单位增效"}, set(_JIN_SLOT)],
        "required_damage": {"对玩家单位增效"},
        "optional_n": 2,
        "optional_pool": _COMMON_POOL,
        "top": _TOP_NO_WX_MIN_HX,
    },
}

# 火拳奶各部位模式
_FIRE_PATTERNS: dict[str, dict] = {
    "主武器": {
        "first": {"大外", "小外"},
        "required": [set(_JIN_SLOT), set(_JIN_SLOT)],
        "required_damage": None,
        "optional_n": 2,
        "optional_pool": _MAIN_POOL,
        "top": _TOP_NO_WUXIANG,
    },
    "副武器": _PURE_PATTERNS["副武器"],
    "环": {
        "first": {"大外", "小外"},
        "required": [set(_JIN_SLOT), set(_JIN_SLOT)],
        "required_damage": None,
        "optional_n": 2,
        "optional_pool": _COMMON_POOL,
        "top": _TOP_NO_WX_MIN_HX,
    },
    "冠胄": {
        "first": {"会心"},
        "required": [{"单体类奇术增伤"}, set(_JIN_SLOT)],
        "required_damage": {"单体类奇术增伤"},
        "optional_n": 2,
        "optional_pool": _COMMON_POOL,
        "top": _TOP_NO_WX_MIN_HX,
    },
    "胫甲": {
        "first": {"劲"},
        "required": [{"对首领单位增伤"}, set(_JIN_SLOT)],
        "required_damage": {"对首领单位增伤"},
        "optional_n": 2,
        "optional_pool": _COMMON_POOL,
        "top": _TOP_NO_WX_MIN_HX,
    },
}

_SUB_PATTERNS = {"pure": _PURE_PATTERNS, "fire": _FIRE_PATTERNS}
_SUB_NAMES = {"pure": "纯奶", "fire": "火拳奶"}

# 武器类型 → 模式部位 key（主武器=扇，副武器=伞；其余武器不适用）
_WEAPON_KEYS = {"扇": "主武器", "伞": "副武器"}

# 非武器部位 → 模式部位 key（环/佩、冠胄/胸甲、胫甲/腕甲 两两同模式）
_PART_KEYS = {
    "环": "环", "佩": "环",
    "冠胄": "冠胄", "胸甲": "冠胄",
    "胫甲": "胫甲", "腕甲": "胫甲",
}


def _check_top(rule: str, tokens: list[str]) -> bool:
    """顶级判定条件（tokens 为非首词条符号列表）

    均为「不出现某组合」型条件，不受剩余空槽影响，
    潜力判定与完整定级共用同一函数。
    """
    s = set(tokens)
    if rule == _TOP_NO_WUXIANG:
        return not (s & {"大无相", "小无相"})
    if rule == _TOP_NO_WX_MIN_HX:
        return (not (s & {"大无相", "小无相"})
                and not ({"敏", "会心"} <= s))
    return False


# ─── 判定器实现 ────────────────────────────────────────────

class HealJudge(SchoolJudge):
    """治疗流派 判定器（纯奶/火拳奶 子玩法合并）"""

    school_key = "heal"
    school_name = "治疗流派"
    implemented = True
    has_keep_pvp = False
    needs_sub_school = True
    sub_school_options = {"pure": "纯奶", "fire": "火拳奶（输出）"}
    sub_school_label = "玩法（可多选）："

    def judge(self, equip: EquipmentData) -> JudgeResult:
        return self._run(equip, partial=False)

    def check_tuning_worthiness(self, equip: EquipmentData) -> JudgeResult:
        """调律潜力判定：空词条槽视作万能牌 + 模拟转律，返回评级上限"""
        return self._run(equip, partial=True)

    # ─── 主流程 ────────────────────────────────────────────

    def _run(self, equip: EquipmentData, partial: bool) -> JudgeResult:
        result = JudgeResult(equipment=equip)

        # 按 部位+武器 两维取模式部位 key
        if equip.part == "武器":
            part_key = _WEAPON_KEYS.get(equip.weapon or "")
        else:
            part_key = _PART_KEYS.get(equip.part)
        if part_key is None:
            result.skipped = True
            result.not_applicable = True
            result.reasons.append(f"{part_label(equip)} 不在治疗流派范围内")
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

        # 逐个所选子玩法尝试，取最优结果
        best_label = ""
        best: JudgeResult | None = None
        for sub in self._enabled_subs():
            res = self._judge_attempt(
                equip, _SUB_PATTERNS[sub][part_key], partial, n_free)
            score = -1 if res.skipped else _RANK[res.rating]
            if best is None or score > (-1 if best.skipped else _RANK[best.rating]):
                best, best_label = res, _SUB_NAMES[sub]
        assert best is not None
        best.reasons = pre_reasons + [f"[{best_label}] {r}" for r in best.reasons]
        return best

    def _judge_attempt(self, equip: EquipmentData, spec: dict,
                       partial: bool, n_free: int) -> JudgeResult:
        """按单个子玩法模式判定一次"""
        result = JudgeResult(equipment=equip)

        category = equip.category
        first_token = _normalize(equip.affixes[0].name, category)
        tokens = [_normalize(a.name, category) for a in equip.affixes[1:]]

        # 首词条判断（治疗模式无次选宽容档）
        if first_token not in spec["first"]:
            result.skipped = True
            result.reasons.append(
                f"首词条 {equip.affixes[0].name} 不符合要求")
            return result

        # 允许的神力词条 = 该部位必需增伤（其余神力即垃圾）
        allowed_divine = set(spec["required_damage"] or ())
        required = spec["required"]

        if partial:
            # 潜力判定：模拟转律（非首词条可选一条无限次转律，
            # 不产生神力词条），废词条可被转律洗掉，不直接判垃圾
            def evaluate(toks: list[str], n_trans: int) -> tuple[Rating, str]:
                junk = [t for t in toks
                        if t not in _POOL_SYMBOLS and t not in allowed_divine]
                if junk:
                    return Rating.JUNK, f"垃圾词条: {'、'.join(junk)}"
                if not _match_partial(toks, required, spec["optional_n"],
                                      n_free, n_trans, _POOL_SYMBOLS,
                                      spec["optional_pool"]):
                    return Rating.USABLE, "已有词条无法全部落入模式槽位，上限为能用"
                if _check_top(spec["top"], toks):
                    return Rating.TOP, f"仍可达顶级（剩余 {n_free} 空槽）"
                return Rating.EXCELLENT, f"仍可命中模式达优秀（剩余 {n_free} 空槽）"

            # 不转律 + 逐一转掉某条非首词条，取评级上限最高者
            rating, reason = evaluate(tokens, 0)
            for i, t in enumerate(tokens):
                r2, why = evaluate(tokens[:i] + tokens[i + 1:], 1)
                if _RANK[r2] > _RANK[rating]:
                    rating, reason = r2, f"模拟转律 {t}：{why}"

            result.rating = rating
            result.reasons.append(reason)
            return result

        # 完整定级：垃圾词条无法移除 → 垃圾
        junk = [t for t in tokens
                if t not in _POOL_SYMBOLS and t not in allowed_divine]
        if junk:
            result.rating = Rating.JUNK
            result.reasons.append(f"垃圾词条: {'、'.join(junk)}")
            return result

        # 明确需要的增伤词条缺失 → 垃圾
        if spec["required_damage"] is not None:
            if not any(t in allowed_divine for t in tokens):
                result.rating = Rating.JUNK
                result.reasons.append(
                    f"缺失必需增伤词条: {'/'.join(sorted(allowed_divine))}")
                return result

        # 模式匹配 → 按顶级判定条件区分 顶级/优秀
        if _match_pattern(tokens, required, spec["optional_n"],
                          spec["optional_pool"]):
            if _check_top(spec["top"], tokens):
                result.rating = Rating.TOP
                result.reasons.append("命中模式，满足顶级判定条件")
            else:
                result.rating = Rating.EXCELLENT
                result.reasons.append("命中模式，未满足顶级判定条件")
        else:
            result.rating = Rating.USABLE
            result.reasons.append("模式未命中但无垃圾词条")
        return result

    # ─── 内部辅助 ──────────────────────────────────────────

    def _enabled_subs(self) -> list[str]:
        """所选子玩法（未配置时默认全部，供潜力判定遍历）"""
        subs = self.config.get("sub_schools") or list(_SUB_PATTERNS)
        chosen = [s for s in subs if s in _SUB_PATTERNS]
        return chosen or list(_SUB_PATTERNS)

    @staticmethod
    def _quality_ok(equip: EquipmentData) -> bool:
        """品阶筛选：武器/首饰仅金色，防具紫色及金色"""
        if equip.category == "armor":
            return equip.quality in ("purple", "gold")
        return equip.quality == "gold"
