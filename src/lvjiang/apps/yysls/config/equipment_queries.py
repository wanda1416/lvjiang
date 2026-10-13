"""装备类型、名称与套装查询；读取统一配置快照。"""

from __future__ import annotations

import copy
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .manager import GameConfigManager

def get_gongjue_bonus(self: GameConfigManager, gongjue_type: str, level: int) -> float | None:
    """弓玦百分数；实测区间优先，其他类型沿用相邻词条档的中点。"""
    for (low, high), value in self._gongjue_bonuses.get(gongjue_type, {}).items():
        if low <= level <= high:
            return value
    affix = {"会意": "会意率", "会心": "会心率", "精准": "精准率"}.get(
        gongjue_type)
    if not affix:
        return None
    caps = self.get_affix_caps(level, affix)
    if caps:
        return float(caps["cap"]) / 2
    levels = self.get_affix_cap_levels(affix)
    lower = max((item for item in levels if item < level), default=None)
    upper = min((item for item in levels if item > level), default=None)
    if lower is None or upper is None:
        return None
    low_caps = self.get_affix_caps(lower, affix)
    high_caps = self.get_affix_caps(upper, affix)
    if low_caps is None or high_caps is None:
        return None
    return (float(low_caps["cap"]) + float(high_caps["cap"])) / 4


def get_equipment_sets(self: GameConfigManager, side: str | None = None) -> dict:
    """返回套装注册表副本；左右套装身份始终分开。"""
    if side in ("left", "right"):
        return copy.deepcopy(self._equipment_sets[side])
    return copy.deepcopy(self._equipment_sets)


def resolve_equipment_set(self: GameConfigManager, text: str, side: str) -> str:
    """从 OCR 全文中识别指定侧的已注册套装，返回稳定 key。"""
    if not text or side not in ("left", "right"):
        return ""
    compact = "".join(str(text).split()).replace("|", "")
    matches = [
        (key, str(entry.get("name") or ""))
        for key, entry in self._equipment_sets[side].items()
        if f"{str(entry.get('name') or '')}套装" in compact
    ]
    if not matches:
        return ""
    # 长名优先，避免未来短名成为另一套装名称的子串。
    return max(matches, key=lambda item: len(item[1]))[0]


def equipment_set_name(self: GameConfigManager, key: str) -> str:
    """稳定 key 转展示名称；未知 key 返回空串。"""
    for side in ("left", "right"):
        entry = self._equipment_sets[side].get(key)
        if entry:
            return str(entry.get("name") or "")
    return ""


def recommended_equipment_set(self: GameConfigManager, school: str) -> str:
    """返回流派配置的推荐攻具套装；未登记时返回空串。"""
    school = str(school or "").strip()
    for key, entry in self._equipment_sets["left"].items():
        if str(entry.get("recommended_school") or "").strip() == school:
            return key
    return ""


def get_weapon_types(self: GameConfigManager) -> list[str]:
    """武器类型注册表（顶层 weapon_types，reload 后实时生效）"""
    return list(self._weapon_types)


def get_type_to_group(self: GameConfigManager) -> dict[str, str]:
    """装备 type → 分组 key 的全局映射。

    包含所有武器具体名称（剑/枪/扇…）→ "weapon"，
    以及非武器部位名（环/佩/冠胄…）→ 对应 group_key。
    武器名称从配置动态加载，新增武器自动生效。
    """
    if not hasattr(self, '_type_to_group_cache') or self._type_to_group_cache is None:
        mapping: dict[str, str] = {}
        # 所有武器具体名称 → "weapon"
        for wt in self._weapon_types:
            mapping[wt] = "weapon"
        # 武器显示名（对话框部位下拉用）
        mapping["武器"] = "weapon"
        # 非武器部位
        mapping.update({
            "环": "ring",
            "佩": "pendant",
            "冠胄": "head",
            "胸甲": "chest",
            "胫甲": "leg",
            "腕甲": "wrist",
        })
        self._type_to_group_cache = mapping
    return dict(self._type_to_group_cache)


def get_group_to_part(self: GameConfigManager) -> dict[str, str]:
    """分组 key → 部位显示名的反向映射。"""
    return {
        "weapon": "武器",
        "ring": "环",
        "pendant": "佩",
        "head": "冠胄",
        "chest": "胸甲",
        "leg": "胫甲",
        "wrist": "腕甲",
    }


def get_weapon_wuxue_affix(self: GameConfigManager, weapon: str) -> str:
    """武器对应的武学增效词条（来自 weapon_types 的 wuxue_affix 字段）

    未配置时返回空字符串。
    """
    return self._weapon_wuxue_affixes.get(weapon, "")


def get_all_weapon_wuxue_affixes(self: GameConfigManager) -> dict[str, str]:
    """全部武器 → 武学增效词条映射"""
    return dict(self._weapon_wuxue_affixes)


def infer_equipment_type_from_name(self: GameConfigManager, equipment_name: str) -> str | None:
    """按配置的标准名称反查具体武器或非武器部位。"""
    matches: list[tuple[int, str]] = []
    mappings = [
        *self._weapon_name_suffixes.items(),
        *self._part_name_suffixes.items(),
    ]
    for equip_type, suffixes in mappings:
        for suffix in suffixes:
            if equipment_name.endswith(suffix):
                matches.append((len(suffix), equip_type))
    if not matches:
        return None
    longest = max(length for length, _equip_type in matches)
    types = {equip_type for length, equip_type in matches if length == longest}
    return types.pop() if len(types) == 1 else None


def infer_equipment_type_from_label(self: GameConfigManager, type_label: str) -> str | None:
    """按配置的部位名称反查规范类型，最长名称优先。"""
    text = str(type_label or "").strip()
    matches: list[tuple[int, str]] = []
    mappings = [
        *self._weapon_type_aliases.items(),
        *self._part_type_aliases.items(),
    ]
    for equip_type, aliases in mappings:
        for alias in aliases:
            if alias and alias in text:
                matches.append((len(alias), equip_type))
    if not matches:
        return None
    longest = max(length for length, _equip_type in matches)
    types = {equip_type for length, equip_type in matches if length == longest}
    return types.pop() if len(types) == 1 else None


def get_equipment_name_series(self: GameConfigManager, group: str) -> dict[int, str]:
    """取得指定输出/防具分组的等阶名称配置。"""
    return dict(self._equipment_name_series.get(group, {}))


def infer_original_equipment_level(self: GameConfigManager, equipment_name: str | None) -> int:
    """仅按装备名称开头的等阶名称推断原始等级，无法唯一匹配时返回 0。

    承音不会改变等阶名称，因此这里刻意不参考当前等级、基础属性或部位；
    最长前缀优先，相同前缀若配置到了不同等级则视为歧义。
    """
    name = str(equipment_name or "").strip()
    if not name:
        return 0
    matches: list[tuple[int, int]] = []
    for series in self._equipment_name_series.values():
        for level, tier_name in series.items():
            tier = str(tier_name or "").strip()
            if tier and name.startswith(tier):
                matches.append((len(tier), int(level)))
    if not matches:
        return 0
    longest = max(length for length, _level in matches)
    levels = {level for length, level in matches if length == longest}
    return levels.pop() if len(levels) == 1 else 0
