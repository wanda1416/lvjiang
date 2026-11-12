"""可选的普通词条计数策略。2026-10-31 新增。"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from lvjiang.apps.yysls.core.equip_parser import EquipmentData

DEFAULT_JUNK_AFFIXES = ("气血最大值", "外功防御", "体", "御")

# 本地用户明确指定的培养路线，优先于通用规则中的大/小外备选方案。
SMALL_BUILD_SCHOOLS = frozenset(("破竹·樽", "裂石·钧", "破竹·风", "破竹·鸢", "牵丝·翊"))
LARGE_BUILD_SCHOOLS = frozenset(("鸣金·虹", "鸣金·影"))


def left_combination_possible(equip: EquipmentData) -> bool:
    """用户培养约束：保留固定首条，空槽乐观填充，转律复用公共合法目标。"""
    if not equip.affixes or equip.part not in ("武器", "环", "佩"):
        return True
    names = [a.name for a in equip.affixes]
    natural = f"{equip.weapon}武学增伤" if equip.weapon else "全武学增效"
    groups: list[tuple[str, ...]]
    if names[0] == "最小外功攻击":
        groups = [("最小外功攻击",), ("最小外功攻击",), ("敏",), (natural,)]
    elif names[0] == "最大外功攻击" and equip.part in ("环", "佩"):
        groups = [("最大外功攻击",), ("最大外功攻击",), ("劲", "最大鸣金攻击"), ("势",), (natural,)]
    else:
        return True
    free = max(0, 5 - len(names))

    def fits(tokens):
        remaining = list(tokens)
        missing = 0
        for group in groups:
            match = next((name for name in group if name in remaining), None)
            if match is None:
                missing += 1
            else:
                remaining.remove(match)
        return missing <= free

    return _reachable_with_transfer(equip, fits, natural)


def _reachable_with_transfer(equip: EquipmentData, fits: Callable[[list[str]], bool], natural: str) -> bool:
    names = [a.name for a in equip.affixes]
    if fits(names):
        return True
    from lvjiang.apps.yysls.config import get_game_config
    from lvjiang.apps.yysls.core.loadout.transmute import transmute_candidates

    data = equip.to_dict(include_fp=False)
    for slot, targets in transmute_candidates(data, get_game_config()).items():
        for target in targets:
            # 部位专属增伤只允许自然产出；转律库不能虚构该槽。
            if target == natural:
                continue
            changed = names.copy()
            changed[slot - 1] = target
            if fits(changed):
                return True
    return False


def armor_combination_possible(equip: EquipmentData, mode: str, attr: str = "") -> bool:
    """四防具首条分类、必需普通槽及至多一次合法转律；空槽可自然产出。"""
    if not equip.affixes or equip.part not in ("冠胄", "胸甲", "胫甲", "腕甲"):
        return False
    first = equip.affixes[0].name
    if first not in (("劲",) if mode == "large" else ("精准率", "会心率")):
        return False
    lower = equip.part in ("胫甲", "腕甲")
    boss = "对首领单位增伤" if lower else ""
    free = max(0, 5 - len(equip.affixes))
    from lvjiang.apps.yysls.config import get_game_config
    from lvjiang.apps.yysls.core.combat.affix_rules import normal_affix_candidates

    gc = get_game_config()
    physical = set(normal_affix_candidates(equip.to_dict(include_fp=False), gc))
    if mode == "large":
        pool = ({"劲", "最大外功攻击", "势", "会意率", "最大鸣金攻击", boss} if lower else
                physical - set(DEFAULT_JUNK_AFFIXES) - {"最小外功攻击", "敏"})

        def fits(tokens: list[str]) -> bool:
            required = ["劲", "最大外功攻击", *([boss] if lower else [])]
            return (all(t in pool for t in tokens[1:])
                    and sum(t not in tokens[1:] for t in required) <= free)
    else:
        flex = {t for t in physical if t == f"最大{attr}攻击" or (
            t.startswith("最小") and t.endswith("攻击") and t != "最小外功攻击")}
        flex |= physical & {"会心率", "精准率"}
        if not lower:
            flex |= {t for t in physical - set(DEFAULT_JUNK_AFFIXES) - {"最大外功攻击", "劲", "势"}
                     if not (t.startswith("最大") and t.endswith("攻击"))}

        def fits(tokens: list[str]) -> bool:
            remaining = list(tokens[1:])
            missing = 0
            for required in (*([boss] if lower else []), "最小外功攻击", "敏"):
                if required in remaining:
                    remaining.remove(required)
                else:
                    missing += 1
            flex_count = 1 if lower else 2
            return (len(remaining) <= flex_count and all(t in flex for t in remaining)
                    and missing + flex_count - len(remaining) <= free)
    return _reachable_with_transfer(equip, fits, boss)


@dataclass(frozen=True)
class JunkRecyclePolicy:
    enabled: bool = False
    minimum: int = 2
    affixes: tuple[str, ...] = DEFAULT_JUNK_AFFIXES
    protect_other_builds: bool = True
    confirm_recycle: bool = False
    by_build: bool = True
    small_affixes: tuple[str, ...] = ("最大外功攻击", "劲", "势")
    large_affixes: tuple[str, ...] = ("最小外功攻击", "敏")

    @classmethod
    def from_config(cls, raw: dict | None) -> JunkRecyclePolicy:
        if raw is None:
            return cls()
        if not isinstance(raw, dict):
            raise ValueError("垃圾词条回收配置必须是对象")
        enabled = raw.get("enabled", False)
        minimum = raw.get("minimum", 2)
        names = raw.get("affixes", list(DEFAULT_JUNK_AFFIXES))
        protect = raw.get("protect_other_builds", True)
        confirm = raw.get("confirm_recycle", False)
        by_build = raw.get("by_build", True)
        if not isinstance(enabled, bool):
            raise ValueError("垃圾词条回收开关必须是布尔值")
        if any(not isinstance(v, bool) for v in (protect, confirm, by_build)):
            raise ValueError("跨流派保护和回收确认必须是布尔值")
        if type(minimum) is not int or not 1 <= minimum <= 5:
            raise ValueError("垃圾词条回收门槛必须为 1–5")
        if (not isinstance(names, list) or not names
                or any(not isinstance(n, str) or not n.strip() for n in names)):
            raise ValueError("请选择至少一个垃圾词条")
        lists = []
        for key in ("small_affixes", "large_affixes"):
            values = raw.get(key, list(getattr(cls(), key)))
            if not isinstance(values, list) or any(not isinstance(n, str) or not n.strip() for n in values):
                raise ValueError("流派垃圾词条必须是名称列表")
            lists.append(tuple(v for v in dict.fromkeys(values) if v != "对玩家单位增效"))
        return cls(enabled, minimum, tuple(v for v in dict.fromkeys(names) if v != "对玩家单位增效"), protect, confirm, by_build, *lists)

    def to_config(self) -> dict:
        return {"enabled": self.enabled, "minimum": self.minimum,
                "affixes": list(self.affixes), "protect_other_builds": self.protect_other_builds,
                "confirm_recycle": self.confirm_recycle, "by_build": self.by_build,
                "small_affixes": list(self.small_affixes), "large_affixes": list(self.large_affixes)}

    def for_rules(self, configs: dict | None, keys: list[str] | None) -> JunkRecyclePolicy:
        """已指定流派优先按其路线，其余沿用规则首位属性；混选取交集。"""
        from dataclasses import replace

        from lvjiang.apps.yysls.config import get_game_config
        from lvjiang.apps.yysls.core.evaluator import get_tuning_rules

        if not self.by_build:
            return self
        rules = get_tuning_rules()
        selections = keys if keys is not None else list(configs or {})
        extras: list[set[str]] = []
        for key in selections:
            rule = rules.get(key)
            first = rule.transmute_priority[0] if rule and rule.transmute_priority else ""
            chosen = (configs or {}).get(key, {}).get("playstyles")
            names = list(rule.playstyles) if rule and chosen is None else (chosen or [""])
            for name in names:
                school = (get_game_config().get_playstyle(name) or {}).get("school", "")
                mode = ("small" if school in SMALL_BUILD_SCHOOLS else
                        "large" if school in LARGE_BUILD_SCHOOLS else
                        "small" if first == "最小外功攻击" else
                        "large" if first == "最大外功攻击" else "")
                extras.append(set(self.small_affixes if mode == "small" else
                                  self.large_affixes if mode == "large" else ()))
        shared = set.intersection(*extras) if extras else set()
        added = [n for n in (*self.small_affixes, *self.large_affixes) if n in shared]
        return replace(self, affixes=tuple(dict.fromkeys((*self.affixes, *added))))

    def first_is_junk(self, names: list[str]) -> bool:
        return bool(names and names[0] in self.affixes and names[0] != "对玩家单位增效")

    def matched(self, names: list[str]) -> list[str]:
        return [name for name in names[:5] if name in self.affixes and name != "对玩家单位增效"]

    def decision(self, names: list[str], *, full: bool, first_valid: bool = False) -> tuple[str, str]:
        if not first_valid and self.first_is_junk(names):
            return "recycle", f"固定首词条「{names[0]}」在垃圾名单内且不可转律，回收"
        matches = self.matched(names[1:5] if first_valid else names)
        reason = f"垃圾词条 {len(matches)}/{self.minimum}"
        if matches:
            reason += "（" + "、".join(matches) + "）"
        if len(matches) >= self.minimum:
            return "recycle", reason + "，立即回收"
        return ("skip", reason + "，词条已满，保留") if full else (
            "continue", reason + "，继续逐条调律")
