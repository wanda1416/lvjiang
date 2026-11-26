"""兵鉴（yysls-bingjian.com）装备库导入格式导出。

目标站点：https://www.yysls-bingjian.com/

导出为兵鉴前端的「导入装备库」原生 JSON（``bingjian-gear`` v1）：:

    {
      "format": "bingjian-gear",
      "version": 1,
      "name": "<角色名>",
      "source": "律匠",
      "items": [
        {
          "name": "踏雪含光",
          "slot": "weapon",
          "weapon": "剑",
          "purple": false,
          "chengyin": true,
          "initial": {"name": "最小外功攻击", "value": 114.1},
          "subs": [{"name": "会心率", "value": 15.2, "transmuted": true}],
          "pitch": {"name": "外功穿透", "value": 16.8}
        }
      ]
    }

字段口径（对齐站点前端的读取逻辑）：

- ``slot`` 用英文部位 key：``weapon`` / ``ring`` / ``pendant`` / ``head`` /
  ``chest`` / ``legs`` / ``hands``；
- ``weapon`` 只在武器上出现，取值必须是站点认识的武器名（舞绫鼓写作“鼓”）；
- 词条用**中文名**而不是英文标识，``transmuted`` 表示该条带“转”；
- ``initial`` 是首词条（``affix_1``），``subs`` 最多四条（``affix_2``~``affix_5``），
  ``pitch`` 是定音；
- 不导出装备固有主属性：站点按部位与品质自己查表（115 阶金装/紫装），
  因此低阶装备的主属性会按当前版本表计算。
"""
from __future__ import annotations

import json

from .equipment_exchange import (
    AFFIX_FILTER_ALL,
    SLOT_WEAPON,
    ExchangeEntry,
    iter_export_entries,
)

BINGJIAN_FORMAT = "bingjian-gear"
BINGJIAN_VERSION = 1
#: 站点显示成「从「律匠」导出的文件」
BINGJIAN_SOURCE = "律匠"


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _stat(affix: object, *, keep_transmuted: bool = True) -> dict | None:
    """``{"name": 词条名, "value": 数值}``；缺名或无数值时返回 None。"""
    if not isinstance(affix, dict):
        return None
    name = affix.get("name")
    if not isinstance(name, str) or not name.strip():
        return None
    value = _number(affix.get("value"))
    if value is None:
        return None
    stat: dict = {"name": name.strip(), "value": value}
    if keep_transmuted and affix.get("is_transferred"):
        stat["transmuted"] = True
    return stat


def _convert_entry(entry: ExchangeEntry) -> dict | None:
    """单件装备 → 兵鉴条目；部位或武器认不出时返回 None。"""
    equip = entry.equip
    item: dict = {
        "name": str(equip.get("name") or "").strip(),
        "slot": entry.slot,
    }
    if entry.slot == SLOT_WEAPON:
        weapon = entry.weapon_name
        if weapon is None:
            return None
        item["weapon"] = weapon
    item["purple"] = str(equip.get("quality") or "").strip() == "purple"
    item["chengyin"] = bool(equip.get("is_chengyin", False))

    initial = _stat(equip.get("affix_1"))
    if initial is not None:
        item["initial"] = initial
    subs = [
        stat
        for index in range(2, 6)
        if (stat := _stat(equip.get(f"affix_{index}"))) is not None
    ]
    if subs:
        item["subs"] = subs
    pitch = _stat(equip.get("dingyin"), keep_transmuted=False)
    if pitch is not None:
        item["pitch"] = pitch
    return item


def export_bingjian(
    session_data: dict,
    role_name: str,
    *,
    level_threshold: int = 0,
    affix_filter: str = AFFIX_FILTER_ALL,
) -> str:
    """把 session 装备数据导出为兵鉴「导入装备库」JSON 文本。

    Parameters
    ----------
    session_data:
        ``SessionManager().load(user_name)`` 返回的完整字典（使用
        ``equipped`` 与 ``bag_items``）。
    role_name:
        角色名，写入 ``name`` 字段，导入后成为装备库名称。
    level_threshold:
        背包物品的等级筛选阈值（0 表示不筛选）。
    affix_filter:
        背包物品的词条筛选："all" / "dingyin" / "full_tuning"。
    """
    items: list[dict] = []
    for entry in iter_export_entries(
        session_data, level_threshold=level_threshold, affix_filter=affix_filter
    ):
        item = _convert_entry(entry)
        if item is not None:
            items.append(item)

    payload = {
        "format": BINGJIAN_FORMAT,
        "version": BINGJIAN_VERSION,
        "name": (role_name or "").strip(),
        "source": BINGJIAN_SOURCE,
        "items": items,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
