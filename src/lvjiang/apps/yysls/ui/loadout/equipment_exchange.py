"""装备数据对外交换：外部站点的公共部位词表与导出条目收集。

两个外部站点的装备交换格式只共用“哪些装备参与导出、它们属于哪个部位”，
其余（属性标识、编码、文件类型）各自在 ``leoq7_export`` / ``bingjian_export``
里定义，避免把第三方协议揉成一份公共格式。

站点本身登记在 ``SITES`` 里：品牌名、网址、文件名后缀、保存过滤器与一句操作提示
都是数据；界面文案用带 ``{site}`` / ``{url}`` 占位符的同一份翻译键渲染，新增站点
不需要往词表里加“导出为某某格式”这种一次性键。

部位统一使用 ``weapon`` / ``ring`` / ``pendant`` / ``head`` / ``chest`` /
``legs`` / ``hands``：这是稳定 key，用于协议与文件名，不对应任何界面文案。
"""
from __future__ import annotations

from dataclasses import dataclass

# ── 外部站点 ──────────────────────────────────────────

SITE_LEOQ7 = "leoq7"
SITE_BINGJIAN = "bingjian"


@dataclass(frozen=True)
class ExternalSite:
    """一个可导出的外部站点。``label`` 是品牌名，不翻译。"""

    key: str
    label: str
    url: str
    host: str
    file_suffix: str
    #: 保存对话框文件类型过滤器的翻译键
    file_filter_key: str
    #: 选项下方操作提示的翻译键（模板，含 ``{url}``）
    hint_key: str


SITES: dict[str, ExternalSite] = {
    SITE_LEOQ7: ExternalSite(
        key=SITE_LEOQ7,
        label="leoq7",
        url="https://yysls.leoq7.com/",
        host="yysls.leoq7.com",
        file_suffix="_leoq7.txt",
        file_filter_key="文本文件 (*.txt)",
        hint_key="文本文件，粘贴到 {url} 的导入框",
    ),
    SITE_BINGJIAN: ExternalSite(
        key=SITE_BINGJIAN,
        label="兵鉴",
        url="https://www.yysls-bingjian.com/",
        host="yysls-bingjian.com",
        file_suffix="_bingjian.json",
        file_filter_key="JSON 文件 (*.json)",
        hint_key="JSON 文件，在 {url} 的「导入装备库」里选择",
    ),
}


def site_spec(site: str) -> ExternalSite:
    """按站点 key 取登记项；未知站点属于调用方错误。"""
    try:
        return SITES[site]
    except KeyError:
        raise ValueError(f"未登记的外部站点: {site!r}") from None


# ── 部位 ──────────────────────────────────────────────

SLOT_WEAPON = "weapon"
SLOT_RING = "ring"
SLOT_PENDANT = "pendant"
SLOT_HEAD = "head"
SLOT_CHEST = "chest"
SLOT_LEGS = "legs"
SLOT_HANDS = "hands"

#: 已装备槽位 key → 部位（主副武器合并为 ``weapon``）
EQUIPPED_SLOT_PARTS: dict[str, str] = {
    "main_weapon": SLOT_WEAPON,
    "sub_weapon": SLOT_WEAPON,
    "head": SLOT_HEAD,
    "chest": SLOT_CHEST,
    "ring": SLOT_RING,
    "pendant": SLOT_PENDANT,
    "leg": SLOT_LEGS,
    "wrist": SLOT_HANDS,
}

#: 装备类型（``type`` 字段）→ 部位；武器类型见 ``WEAPON_NAME_ALIASES``
TYPE_PARTS: dict[str, str] = {
    "环": SLOT_RING,
    "佩": SLOT_PENDANT,
    "冠胄": SLOT_HEAD,
    "胸甲": SLOT_CHEST,
    "胫甲": SLOT_LEGS,
    "腕甲": SLOT_HANDS,
}

#: 武器类型 → 交换格式武器名（对齐两个站点的武器词表，舞绫鼓统一写作“鼓”）
WEAPON_NAME_ALIASES: dict[str, str] = {
    "剑": "剑",
    "长剑": "剑",
    "枪": "枪",
    "长枪": "枪",
    "伞": "伞",
    "扇": "扇",
    "团扇": "扇",
    "绳镖": "绳镖",
    "绳标": "绳镖",
    "双刀": "双刀",
    "陌刀": "陌刀",
    "横刀": "横刀",
    "唐刀": "横刀",
    "手甲": "手甲",
    "拳甲": "手甲",
    "舞绫鼓": "鼓",
    "鼓": "鼓",
}

#: 词条筛选：全部 / 只导出带定音的装备 / 只导出满调律装备
AFFIX_FILTER_ALL = "all"
AFFIX_FILTER_DINGYIN = "dingyin"
AFFIX_FILTER_FULL_TUNING = "full_tuning"


@dataclass(frozen=True)
class ExchangeEntry:
    """参与导出的一件装备。"""

    slot: str
    equip: dict

    @property
    def type_name(self) -> str:
        value = self.equip.get("type")
        return value.strip() if isinstance(value, str) else ""

    @property
    def weapon_name(self) -> str | None:
        """武器名；非武器或类型不认识时为 None。"""
        return WEAPON_NAME_ALIASES.get(self.type_name)


def part_of_type(type_name: str | None) -> str | None:
    """装备类型 → 部位（武器返回 ``weapon``，不认识返回 None）。"""
    if not isinstance(type_name, str):
        return None
    cleaned = type_name.strip()
    if cleaned in WEAPON_NAME_ALIASES:
        return SLOT_WEAPON
    return TYPE_PARTS.get(cleaned)


def _passes_filters(equip: dict, *, level_threshold: int, affix_filter: str) -> bool:
    if level_threshold > 0:
        level = equip.get("level", 0)
        if isinstance(level, str):
            try:
                level = int(level)
            except (TypeError, ValueError):
                level = 0
        if not isinstance(level, (int, float)) or level < level_threshold:
            return False
    if affix_filter == AFFIX_FILTER_DINGYIN:
        dingyin = equip.get("dingyin")
        return bool(isinstance(dingyin, dict) and dingyin.get("name"))
    if affix_filter == AFFIX_FILTER_FULL_TUNING:
        return all(
            isinstance(equip.get(f"affix_{index}"), dict)
            and equip[f"affix_{index}"].get("name")
            for index in range(1, 6)
        )
    return True


def iter_export_entries(
    session_data: dict,
    *,
    level_threshold: int = 0,
    affix_filter: str = AFFIX_FILTER_ALL,
) -> list[ExchangeEntry]:
    """收集参与导出的装备：已穿戴全部导出，背包按等级与词条筛选。

    已穿戴装备不受筛选影响；类型缺失（OCR 未识别）的背包装备跳过。
    """
    entries: list[ExchangeEntry] = []
    for slot_key, equip in (session_data.get("equipped") or {}).items():
        if not isinstance(equip, dict):
            continue
        slot = EQUIPPED_SLOT_PARTS.get(str(slot_key))
        if slot is None:
            continue
        entries.append(ExchangeEntry(slot=slot, equip=equip))

    for group in (session_data.get("bag_items") or {}).values():
        if not isinstance(group, dict):
            continue
        for equip in group.values():
            if not isinstance(equip, dict):
                continue
            slot = part_of_type(equip.get("type"))
            if slot is None:
                continue
            if not _passes_filters(
                equip, level_threshold=level_threshold, affix_filter=affix_filter
            ):
                continue
            entries.append(ExchangeEntry(slot=slot, equip=equip))
    return entries


def export_text(
    site: str,
    session_data: dict,
    role_name: str,
    *,
    level_threshold: int = 0,
    affix_filter: str = AFFIX_FILTER_ALL,
) -> str:
    """按站点生成导出内容；新增站点时只在这里接一条实现。"""
    spec = site_spec(site)
    if spec.key == SITE_BINGJIAN:
        from .bingjian_export import export_bingjian

        return export_bingjian(
            session_data, role_name,
            level_threshold=level_threshold, affix_filter=affix_filter,
        )
    from .leoq7_export import export_leoq7

    return export_leoq7(
        session_data, role_name,
        level_threshold=level_threshold, affix_filter=affix_filter,
    )
