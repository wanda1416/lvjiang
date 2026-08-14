"""leoq7 (yysls.leoq7.com) 装备数据导出 — V2 外部传输格式。

目标网站：https://yysls.leoq7.com/

Payload JSON::

    {
      "kind": "yysls-equipment-export",
      "schemaVersion": 2,
      "roleName": "<角色名>",
      "items": [
        {
          "equipmentKey": "EQUIPMENT_WEAPON_SWORD",
          "equipmentName": "踏雪含光",
          "type": "weapon",
          "firstTuning":  [{"key": "MAX_EXTERNAL_ATTACK", "value": 114.1}],
          "secondaryTuning": [{"key": "JIN", "value": 72.2}],
          "pitch": [{"key": "EXTERNAL_PENETRATION", "value": 16.8}],
          "isChengyin": true
        }
      ]
    }

编码：JSON → XOR 加密 → Base64 → 前缀 ``YYSLS_EQUIPMENT_EXPORT_V2\\n``

XOR 公式（与网站 ``decodeExternalEquipmentExportV2`` 对齐）::

    encrypted[i] = json_byte[i] ^ KEY[i % len(KEY)] ^ ((17*i + 29) % 251)

KEY = ``b"yysls-equipment-transfer"``
"""
from __future__ import annotations

import base64
import json

# ── 常量 ──────────────────────────────────────────────

_HEADER = "YYSLS_EQUIPMENT_EXPORT_V2"
_TRANSFER_KEY = b"yysls-equipment-transfer"

# ── 装备类型 → equipmentKey ──────────────────────────
# 网站的 EXTERNAL_EQUIPMENT_KEY_SLOT_MAP / EXTERNAL_WEAPON_KEY_MAP

_EQUIPMENT_KEY_MAP: dict[str, str] = {
    # 武器（10 种）
    "剑": "EQUIPMENT_WEAPON_SWORD",
    "枪": "EQUIPMENT_WEAPON_SPEAR",
    "伞": "EQUIPMENT_WEAPON_UMBRELLA",
    "扇": "EQUIPMENT_WEAPON_FAN",
    "绳镖": "EQUIPMENT_WEAPON_ROPE_DART",
    "双刀": "EQUIPMENT_WEAPON_DUAL_BLADE",
    "陌刀": "EQUIPMENT_WEAPON_MODAO",
    "横刀": "EQUIPMENT_WEAPON_HENGDAO",
    "手甲": "EQUIPMENT_WEAPON_FIST",
    "舞绫鼓": "EQUIPMENT_WEAPON_DRUM",
    # 防具（6 种）
    "环": "EQUIPMENT_RING",
    "佩": "EQUIPMENT_PENDANT",
    "冠胄": "EQUIPMENT_HEAD",
    "胸甲": "EQUIPMENT_CHEST",
    "胫甲": "EQUIPMENT_LEG",
    "腕甲": "EQUIPMENT_HAND",
}

# ── 词条中文名 → external stat key ───────────────────
# 网站的 EXTERNAL_STAT_KEY_MAP 的 key 集合

_AFFIX_KEY_MAP: dict[str, str] = {
    # 攻击
    "最小外功攻击": "MIN_EXTERNAL_ATTACK",
    "最大外功攻击": "MAX_EXTERNAL_ATTACK",
    "最小无相攻击": "MIN_WUXIANG_ATTACK",
    "最大无相攻击": "MAX_WUXIANG_ATTACK",
    "最小鸣金攻击": "MIN_MINGJIN_ATTACK",
    "最大鸣金攻击": "MAX_MINGJIN_ATTACK",
    "最小裂石攻击": "MIN_LIESHI_ATTACK",
    "最大裂石攻击": "MAX_LIESHI_ATTACK",
    "最小牵丝攻击": "MIN_QIANSI_ATTACK",
    "最大牵丝攻击": "MAX_QIANSI_ATTACK",
    "最小破竹攻击": "MIN_POZHU_ATTACK",
    "最大破竹攻击": "MAX_POZHU_ATTACK",
    # 属性
    "劲": "JIN",
    "敏": "MIN",
    "势": "SHI",
    # 率
    "精准率": "ACCURACY_RATE",
    "会心率": "CRITICAL_RATE",
    "会意率": "INSIGHT_RATE",
    # 穿透
    "外功穿透": "EXTERNAL_PENETRATION",
    "无相穿透": "WUXIANG_PENETRATION",
    # 增效
    "全武学增效": "WUXUE_DAMAGE",
    "对首领单位增伤": "BOSS_DAMAGE",
    "对玩家单位增效": "PLAYER_DAMAGE",
    # 武器增伤（含别名 → 网站 EXTERNAL_STAT_KEY_MAP 值）
    "陌刀武学增伤": "BIGBLADE_DAMAGE",
    "陌刀武学增效": "BIGBLADE_DAMAGE",
    "横刀武学增伤": "HENGDAO_DAMAGE",
    "横刀武学增效": "HENGDAO_DAMAGE",
    "剑武学增伤": "SWORD_DAMAGE",
    "剑武学增效": "SWORD_DAMAGE",
    "枪武学增伤": "SPEAR_DAMAGE",
    "枪武学增效": "SPEAR_DAMAGE",
    "伞武学增伤": "UMBRELLA_DAMAGE",
    "伞武学增效": "UMBRELLA_DAMAGE",
    "扇武学增伤": "FAN_DAMAGE",
    "扇武学增效": "FAN_DAMAGE",
    "绳镖武学增伤": "ROPE_DAMAGE",
    "绳镖武学增效": "ROPE_DAMAGE",
    "绳标武学增效": "ROPE_DAMAGE",
    "双刀武学增伤": "2BLADE_DAMAGE",
    "双刀武学增效": "2BLADE_DAMAGE",
    "手甲武学增伤": "FIST_DAMAGE",
    "手甲武学增效": "FIST_DAMAGE",
    "拳甲武学增效": "FIST_DAMAGE",
    "舞绫鼓武学增伤": "DRUM_DAMAGE",
    "舞绫鼓武学增效": "DRUM_DAMAGE",
    "鼓武学增效": "DRUM_DAMAGE",
    # 防御
    "气血最大值": "MAX_HP",
    "体": "EXTERNAL_DEFENCE",
    "御": "EXTERNAL_DEFENCE",
    "外功防御": "EXTERNAL_DEFENCE",
    "外功抗性": "EXTERNAL_DEFENCE",
}

# 武器/环/佩 → 穿透类定音；其余 → 技能增伤类定音
_PENETRATION_SLOTS = frozenset({"weapon", "ring", "pendant"})


# ── 内部工具 ──────────────────────────────────────────

def _map_equipment_key(equip_type: str | None) -> str | None:
    """装备类型 → equipmentKey（不支持的类型返回 None）"""
    if not equip_type:
        return None
    return _EQUIPMENT_KEY_MAP.get(equip_type)


def _map_affix_key(name: str) -> str | None:
    """词条中文名 → external stat key。

    先查精确映射；未命中时检查是否为技能增伤类定音词条
    （名称以「增伤」结尾，不限是否含 ·）。
    """
    if name in _AFFIX_KEY_MAP:
        return _AFFIX_KEY_MAP[name]
    # 技能增伤类定音：名称以「增伤」结尾（含/不含 · 均匹配）
    if name.endswith("增伤"):
        return "SKILL_DAMAGE"
    return None


def _is_pitch_key(slot_type: str, key: str) -> bool:
    """判断该 external key 是否为该部位的合法定音词条"""
    if slot_type in _PENETRATION_SLOTS:
        return key in ("EXTERNAL_PENETRATION", "WUXIANG_PENETRATION")
    return key == "SKILL_DAMAGE"


def _affix_to_stat(affix: dict | None) -> dict | None:
    """将 bag_items 中的 affix 字典转为 ``{key, value}``，无法映射则返回 None"""
    if not affix or not affix.get("name"):
        return None
    key = _map_affix_key(affix["name"])
    if key is None:
        return None
    value = affix.get("value")
    if not isinstance(value, (int, float)):
        return None
    return {"key": key, "value": float(value)}


def _dingyin_to_stat(dingyin: dict | None) -> dict | None:
    """将定音词条字典转为 ``{key, value}``，无法映射则返回 None"""
    if not dingyin or not dingyin.get("name"):
        return None
    name = dingyin["name"]
    # 穿透类定音
    if name in ("外功穿透", "无相穿透"):
        key = _AFFIX_KEY_MAP[name]
    # 技能增伤类定音（含「增伤」关键字）
    elif "增伤" in name:
        key = "SKILL_DAMAGE"
    else:
        return None
    value = dingyin.get("value")
    if not isinstance(value, (int, float)):
        return None
    return {"key": key, "value": float(value)}


def _convert_item(equip: dict, slot_type: str) -> dict | None:
    """将单件 session 装备数据转为 leoq7 ExternalItem 格式"""
    equip_type = equip.get("type")
    equipment_key = _map_equipment_key(equip_type)
    if equipment_key is None:
        return None

    # affix_1 → firstTuning
    first = _affix_to_stat(equip.get("affix_1"))
    first_tuning = [first] if first else []

    # affix_2-4 → secondaryTuning（最多 4 条）
    secondary: list[dict] = []
    for i in range(2, 5):
        stat = _affix_to_stat(equip.get(f"affix_{i}"))
        if stat:
            secondary.append(stat)

    # affix_5 + dingyin → pitch 判定
    affix_5_stat = _affix_to_stat(equip.get("affix_5"))
    dingyin_stat = _dingyin_to_stat(equip.get("dingyin"))

    pitch: list[dict] = []
    if dingyin_stat and _is_pitch_key(slot_type, dingyin_stat["key"]):
        pitch = [dingyin_stat]
    elif affix_5_stat and _is_pitch_key(slot_type, affix_5_stat["key"]):
        pitch = [affix_5_stat]
    else:
        # affix_5 不是合法定音 → 归入副词条
        if affix_5_stat:
            secondary.append(affix_5_stat)

    # 副词条上限 4 条
    if len(secondary) > 4:
        secondary = secondary[:4]

    item: dict = {
        "equipmentKey": equipment_key,
        "type": slot_type,
        "firstTuning": first_tuning,
        "secondaryTuning": secondary,
        "pitch": pitch,
        "isChengyin": bool(equip.get("is_chengyin", False)),
    }
    # 可选字段：装备名称（网站的 convertExternalEquipmentItem 用它显示）
    name = equip.get("name")
    if name:
        item["equipmentName"] = name
    return item


def _encode_transfer(json_bytes: bytes) -> str:
    """JSON → XOR 加密 → Base64 → 带 HEADER 前缀的传输文本"""
    key_len = len(_TRANSFER_KEY)
    encrypted = bytes(
        b ^ _TRANSFER_KEY[i % key_len] ^ ((17 * i + 29) % 251)
        for i, b in enumerate(json_bytes)
    )
    return f"{_HEADER}\n{base64.standard_b64encode(encrypted).decode()}"


# ── 公开 API ──────────────────────────────────────────

# 已装备槽位 → slot_type
_EQUIPPED_SLOT_TYPE: dict[str, str] = {
    "main_weapon": "weapon",
    "sub_weapon": "weapon",
    "head": "head",
    "chest": "chest",
    "ring": "ring",
    "pendant": "pendant",
    "leg": "leg",
    "wrist": "wrist",
}

# 背包武器类型集合（排除防具）
_WEAPON_TYPES: frozenset[str] = frozenset(_EQUIPMENT_KEY_MAP.keys()) - frozenset({
    "环", "佩", "冠胄", "胸甲", "胫甲", "腕甲",
})
# 背包防具 → slot_type
_SLOT_TYPE_MAP: dict[str, str] = {
    "环": "ring", "佩": "pendant", "冠胄": "head",
    "胸甲": "chest", "胫甲": "leg", "腕甲": "wrist",
}


def export_leoq7(session_data: dict, role_name: str) -> str:
    """将 session 数据导出为 leoq7 V2 传输文本。

    Parameters
    ----------
    session_data:
        ``SessionManager().load(user_name)`` 返回的完整字典
    role_name:
        角色名（写入 payload 的 roleName 字段）

    Returns
    -------
    str
        可直接粘贴到 yysls.leoq7.com 的传输文本
    """
    items: list[dict] = []

    # 已装备
    for slot_key, equip in session_data.get("equipped", {}).items():
        slot_type = _EQUIPPED_SLOT_TYPE.get(slot_key)
        if not slot_type:
            continue
        item = _convert_item(equip, slot_type)
        if item:
            items.append(item)

    # 背包
    for _group_key, group_items in session_data.get("bag_items", {}).items():
        if not isinstance(group_items, dict):
            continue
        for _fp, equip in group_items.items():
            equip_type = equip.get("type")
            if not equip_type:
                continue  # OCR 完全失败的装备跳过
            if equip_type in _WEAPON_TYPES:
                slot_type = "weapon"
            elif equip_type in _SLOT_TYPE_MAP:
                slot_type = _SLOT_TYPE_MAP[equip_type]
            else:
                continue
            item = _convert_item(equip, slot_type)
            if item:
                items.append(item)

    payload = {
        "kind": "yysls-equipment-export",
        "schemaVersion": 2,
        "roleName": role_name or "装备导入",
        "items": items,
    }
    json_bytes = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return _encode_transfer(json_bytes)
