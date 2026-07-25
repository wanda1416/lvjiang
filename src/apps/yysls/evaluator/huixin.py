"""会心流派 判定器（大外流/小外流，穷举匹配制）

规格来源：docs/10-game/04-tuning-mechanics.md
「会心大外流/小外流各部位词条要求」+「各流派需要的武学增伤说明」。

与会意判定器共用同一套穷举匹配框架，差异点：
- 每个部位模式为 首词条 + 4 个固定槽位（无可选候选池）；
- 武器的 主/副 角色与 主武学增伤 由「指定流派 + 玩法」配置决定，
  同一武器在多个所选流派/角色下分别尝试，取最优判定结果；
- 所选流派的最大属攻攻击（裂石/牵丝/破竹）在非武器部位一律
  视作 大无相（简化判定，无独立的大本属符号）；
- 04 文档未给出符号层面的 顶级/优秀 区分规则（需数值拉满），
  命中模式统一按 顶级 上限处理。
"""

from src.apps.yysls.equip_parser import EquipmentData

from .base import JudgeResult, Rating, SchoolJudge, part_label
from .huiyi import _match_partial, _match_pattern  # 共用匹配算法


# ─── 词条归一化 ────────────────────────────────────────────

_SYMBOL_MAP = {
    "最大外功攻击": "大外",
    "最小外功攻击": "小外",
    "劲": "劲",
    "势": "势",
    "敏": "敏",
    "会心率": "会心",
    "精准率": "精准",
}

# 会心流派词条池；池外词条（如 会意率、他系属攻）即垃圾词条。
# 「无相」「本属」是池内但不匹配任何槽位的符号（不判垃圾，最高能用）。
_POOL_SYMBOLS = {"大外", "小外", "劲", "势", "敏", "会心", "精准",
                 "大无相", "无相", "本属"}

_PVP_NAMES = {"单体类奇术增伤", "对玩家单位增效"}


def _normalize(name: str, category: str, own_attrs: set[str]) -> str:
    """词条全称 → 模式符号；神力词条及池外词条保留全称

    own_attrs 为所选流派的本属名集合（裂石/牵丝/破竹）：
    该属攻在非武器部位一律视作 大无相，在武器上是池内但
    无槽位的符号（本属）。
    """
    if name in _SYMBOL_MAP:
        return _SYMBOL_MAP[name]
    if name == "最大无相攻击":
        return "大无相" if category == "weapon" else "无相"
    if any(name == f"最大{a}攻击" for a in own_attrs):
        return "本属" if category == "weapon" else "大无相"
    return name


# ─── 流派/玩法 → 武器角色 ──────────────────────────────────
# 主武器需要对应的主武学增伤；副武器不允许出现自身武学增伤。
# 玩法仅影响裂石（纯唐/双切 主副互换）；牵丝的 走地/飞天 不改变武器角色。

# 会心二级配置：指定流派 key → 显示名（UI 子选项）
SUB_SCHOOLS: dict[str, str] = {
    "lieshi": "裂石",
    "pozhu": "破竹",
    "qiansi": "牵丝",
}

# 指定流派 key → 玩法 key → 显示名（破竹无玩法区分，不列条目）
SUB_SCHOOL_PLAYSTYLES: dict[str, dict[str, str]] = {
    "lieshi": {"chuntang": "纯唐", "shuangqie": "双切"},
    "qiansi": {"zoudi": "走地", "feitian": "飞天"},
}

_SCHOOL_ATTR = {"lieshi": "裂石", "pozhu": "破竹", "qiansi": "牵丝"}
_PLAYSTYLE_NAMES = {"chuntang": "纯唐", "shuangqie": "双切"}

# (流派, 玩法) → {主武器类型: 主武学增伤候选名}
# 词条名一律使用 attributes.yaml 中的标准字段名。
_MAIN_WEAPONS: dict[tuple[str, str | None], dict[str, set[str]]] = {
    ("lieshi", "chuntang"): {"横刀": {"唐横刀武学增伤"}},
    ("lieshi", "shuangqie"): {"陌刀": {"陌刀武学增伤"}},
    ("pozhu", None): {
        "双刀": {"双刀武学增伤"},
        "伞": {"伞武学增伤"},
        "手甲": {"手甲武学增伤"},
    },
    ("qiansi", None): {
        "伞": {"伞武学增伤"},
        "舞绫鼓": {"舞绫鼓武学增伤"},
    },
}

# (流派, 玩法) → 副武器类型集合
_SUB_WEAPONS: dict[tuple[str, str | None], set[str]] = {
    ("lieshi", "chuntang"): {"陌刀"},
    ("lieshi", "shuangqie"): {"横刀"},
    ("pozhu", None): {"绳镖"},
    ("qiansi", None): {"扇"},
}


# ─── 各部位模式定义 ────────────────────────────────────────
# "DMG" 为主武学增伤占位槽，按所选流派/玩法解析。

_DAWAI_PATTERNS: dict[str, dict] = {
    "主武器": {"first": {"大外"},
               "required": ["DMG", {"大外"}, {"劲"}, {"敏", "势"}]},
    "副武器": {"first": {"大外"},
               "required": [{"大外"}, {"劲"}, {"敏"}, {"会心", "大无相"}]},
    "环": {"first": {"大外"},
           "required": [{"大外"}, {"劲"}, {"全武学增效"}, {"敏", "会心"}]},
    "冠胄": {"first": {"会心", "精准"},
             "required": [{"会心", "精准"}, {"大外"}, {"劲"}, {"敏", "大无相"}]},
    "胫甲": {"first": {"劲"},
             "required": [{"对首领单位增伤"}, {"大外"}, {"劲"},
                          {"敏", "势", "会心", "精准"}]},
}

_XIAOWAI_PATTERNS: dict[str, dict] = {
    "主武器": {"first": {"小外"},
               "required": ["DMG", {"小外"}, {"敏"}, {"大无相"}]},
    "副武器": {"first": {"小外"},
               "required": [{"小外"}, {"敏"}, {"大无相"}, {"会心"}]},
    "环": {"first": {"小外"},
           "required": [{"小外"}, {"敏"}, {"全武学增效"}, {"大无相"}]},
    "冠胄": {"first": {"会心", "精准"},
             "required": [{"会心", "精准"}, {"小外"}, {"敏"}, {"大无相"}]},
    "胫甲": {"first": {"会心", "精准"},
             "required": [{"小外"}, {"敏"}, {"对首领单位增伤"}, {"大无相"}]},
}

_PART_ALIAS = {"佩": "环", "胸甲": "冠胄", "腕甲": "胫甲"}

_RANK = {Rating.JUNK: 0, Rating.USABLE: 1, Rating.EXCELLENT: 2, Rating.TOP: 3}


# ─── 判定器实现 ────────────────────────────────────────────

class _HuixinBase(SchoolJudge):
    """会心流派判定器公共实现（子类只需给出部位模式表）"""

    implemented = True
    has_keep_pvp = True
    needs_sub_school = True
    sub_school_options = SUB_SCHOOLS
    sub_school_playstyles = SUB_SCHOOL_PLAYSTYLES
    patterns: dict[str, dict] = {}

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
                f"{part_label(equip)} 不在所选会心流派范围内")
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

        # 逐个角色尝试，取最优结果（同一武器可在多个流派下有不同角色）
        best_label = ""
        best: JudgeResult | None = None
        for label, part_key, damage, own_attrs in attempts:
            res = self._judge_attempt(
                equip, part_key, damage, own_attrs, partial, n_free)
            score = -1 if res.skipped else _RANK[res.rating]
            if best is None or score > (-1 if best.skipped else _RANK[best.rating]):
                best, best_label = res, label
        assert best is not None
        best.reasons = pre_reasons + [f"[{best_label}] {r}" for r in best.reasons]
        return best

    def _judge_attempt(self, equip: EquipmentData, part_key: str,
                       damage: set[str] | None, own_attrs: set[str],
                       partial: bool, n_free: int) -> JudgeResult:
        """按单个 部位/角色 组合判定一次"""
        spec = self.patterns[part_key]
        result = JudgeResult(equipment=equip)

        category = equip.category
        first_token = _normalize(equip.affixes[0].name, category, own_attrs)
        tokens = [_normalize(a.name, category, own_attrs)
                  for a in equip.affixes[1:]]

        # 首词条判断（会心模式首词条即模式要求，无次选宽容档）
        if first_token not in spec["first"]:
            result.skipped = True
            result.reasons.append(
                f"首词条 {equip.affixes[0].name} 不符合要求")
            return result

        # 垃圾词条（池外词条或禁止的神力词条）
        allowed_divine = self._allowed_divine(part_key, damage)

        required = [set(damage) if slot == "DMG" else slot
                    for slot in spec["required"]]
        if self.keep_pvp and part_key == "胫甲":
            # 对玩家增效可顶替对首领增伤槽位（视作有效）
            required = [
                slot | {"对玩家单位增效"} if "对首领单位增伤" in slot else slot
                for slot in required
            ]

        if partial:
            # 潜力判定：模拟转律（非首词条可选一条无限次转律，
            # 不产生神力词条），废词条可被转律洗掉，不直接判垃圾
            def evaluate(toks: list[str], n_trans: int) -> tuple[Rating, str]:
                junk = [t for t in toks
                        if t not in _POOL_SYMBOLS and t not in allowed_divine]
                if junk:
                    return Rating.JUNK, f"垃圾词条: {'、'.join(junk)}"
                if _match_partial(toks, required, 0, n_free, n_trans,
                                  _POOL_SYMBOLS):
                    return Rating.TOP, f"仍可命中模式（剩余 {n_free} 空槽）"
                return Rating.USABLE, "已有词条无法全部落入模式槽位，上限为能用"

            # 不转律 + 逐一转掉某条非首词条，取评级上限最高者
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

        # 完整定级：垃圾词条无法移除 → 垃圾
        junk = [t for t in tokens
                if t not in _POOL_SYMBOLS and t not in allowed_divine]
        if junk:
            result.rating = Rating.JUNK
            result.reasons.append(f"垃圾词条: {'、'.join(junk)}")
            return result

        if self.keep_pvp and any(t in _PVP_NAMES for t in tokens):
            result.is_pvp = True
            result.reasons.append("含 PVP 词条，保留")

        # 明确需要的增伤词条缺失 → 垃圾（仅完整定级；潜力判定
        # 中缺失的增伤可由空槽调出）
        req_dmg = self._required_damage(part_key, damage)
        if req_dmg:
            accepted = set(req_dmg)
            if self.keep_pvp and part_key == "胫甲":
                accepted |= {"对玩家单位增效"}
            if not any(t in accepted for t in tokens):
                result.rating = Rating.JUNK
                result.reasons.append(
                    f"缺失必需增伤词条: {'/'.join(sorted(req_dmg))}")
                return result

        if _match_pattern(tokens, required, 0):
            # 04 文档的 顶级/优秀 需按数值拉满区分，符号层面按顶级上限处理
            result.rating = Rating.TOP
            result.reasons.append("命中模式")
        else:
            result.rating = Rating.USABLE
            result.reasons.append("模式未命中但无垃圾词条")
        return result

    # ─── 角色展开 ──────────────────────────────────────────

    def _build_attempts(
            self, equip: EquipmentData
    ) -> list[tuple[str, str, set[str] | None, set[str]]]:
        """展开该装备在所选流派下的全部判定角色

        非武器部位所有所选流派共用同一模式（属攻统一视作大无相），
        只需判定一次；部位为武器时不区分主/副，按 (流派, 玩法) 由
        具体武器类型展开主/副角色分别尝试。

        Returns:
            [(角色标签, 模式部位 key, 主武学增伤候选, 本属名集合), ...]
        """
        if equip.part != "武器":
            part = _PART_ALIAS.get(equip.part, equip.part)
            if part not in ("环", "冠胄", "胫甲"):
                return []
            own_attrs = {_SCHOOL_ATTR[s] for s in self._enabled_schools()}
            return [(part, part, None, own_attrs)]

        weapon = equip.weapon or ""
        attempts: list[tuple[str, str, set[str] | None, set[str]]] = []
        for school, ps in self._enabled_role_keys():
            label = _SCHOOL_ATTR[school]
            if ps:
                label += f"-{_PLAYSTYLE_NAMES.get(ps, ps)}"
            mains = _MAIN_WEAPONS.get((school, ps), {})
            if weapon in mains:
                attempts.append((f"{label} 主武器", "主武器",
                                 mains[weapon], {_SCHOOL_ATTR[school]}))
            if weapon in _SUB_WEAPONS.get((school, ps), set()):
                attempts.append((f"{label} 副武器", "副武器",
                                 None, {_SCHOOL_ATTR[school]}))
        return attempts

    def _enabled_schools(self) -> list[str]:
        """所选指定流派（未配置时默认全部，供潜力判定遍历）"""
        subs = self.config.get("sub_schools") or list(_SCHOOL_ATTR)
        return [s for s in subs if s in _SCHOOL_ATTR]

    def _enabled_role_keys(self) -> list[tuple[str, str | None]]:
        """所选 (流派, 玩法) 组合；裂石未选玩法时默认纯唐+双切"""
        ps_cfg = self.config.get("playstyles") or {}
        keys: list[tuple[str, str | None]] = []
        for s in self._enabled_schools():
            if s == "lieshi":
                for p in (ps_cfg.get("lieshi") or ["chuntang", "shuangqie"]):
                    if ("lieshi", p) in _MAIN_WEAPONS:
                        keys.append(("lieshi", p))
            else:
                keys.append((s, None))
        return keys

    # ─── 内部辅助 ──────────────────────────────────────────

    @staticmethod
    def _quality_ok(equip: EquipmentData) -> bool:
        """品阶筛选：武器/首饰仅金色，防具紫色及金色"""
        if equip.category == "armor":
            return equip.quality in ("purple", "gold")
        return equip.quality == "gold"

    @staticmethod
    def _required_damage(part_key: str,
                         damage: set[str] | None) -> set[str] | None:
        """该角色明确需要的增伤词条（缺失直接判垃圾；None 表示无要求）"""
        if part_key == "主武器":
            return set(damage or ())
        if part_key == "环":
            return {"全武学增效"}
        if part_key == "胫甲":
            return {"对首领单位增伤"}
        return None

    def _allowed_divine(self, part_key: str,
                        damage: set[str] | None) -> set[str]:
        """该角色允许出现的神力词条（其余神力词条视为垃圾词条）"""
        if part_key == "主武器":
            return set(damage or ())
        if part_key == "环":
            return {"全武学增效"}
        if part_key == "冠胄":
            return {"单体类奇术增伤"} if self.keep_pvp else set()
        if part_key == "胫甲":
            allowed = {"对首领单位增伤"}
            if self.keep_pvp:
                allowed |= {"对玩家单位增效"}
            return allowed
        return set()  # 副武器不允许任何武学增伤


class HuixinBigJudge(_HuixinBase):
    """会心流派-大外流 判定器"""

    school_key = "huixin_big"
    school_name = "会心流派-大外流"
    patterns = _DAWAI_PATTERNS


class HuixinSmallJudge(_HuixinBase):
    """会心流派-小外流 判定器"""

    school_key = "huixin_small"
    school_name = "会心流派-小外流"
    patterns = _XIAOWAI_PATTERNS
