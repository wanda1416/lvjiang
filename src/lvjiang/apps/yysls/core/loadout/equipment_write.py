"""装备写入的唯一合并契约。

同一件实体装备在仓储里只有一条记录（指纹故意不含定音等可变状态），而它会被
多个备战方案共用。任何一次写入如果整条替换，就会把上一次写入留下、而本次
数据里根本不可能带的东西抹掉——定音和模拟转律目标都是这么丢的。

所以所有写 ``equipment_items`` 的路径都必须先过这里：本次带了什么就更新
什么，没带的从旧记录搬回来。新增「同一实体的可变状态」时只改这一个文件，
不再去七个写入点各补一遍。

``dingyin_type`` 是例外，它由写入来源决定：

- 背包扫描看到的是用户自己给这件装备定的展示状态，可以更新；
- 备战扫描看到的是切换备战方案时游戏自动切过去的状态，用户根本没碰这件
  装备，不能当成它的展示偏好，保持旧值不动（该方案自己的选择另存在
  ``LoadoutPlan.dingyin`` 里）。
"""

from __future__ import annotations

import copy
from enum import Enum

from ..equip_parser.dingyin_parser import (
    DINGYIN_NORMAL,
    DINGYIN_TYPE_KEY,
    DINGYIN_TYPES,
    DINGYIN_ZHIGE_KEY,
)
from .transmute import TARGET_NAME_KEY, TARGET_VALUE_KEY, saved_transmute_target

#: 本次写入没带、需要从旧记录搬回来的定音槽。
DINGYIN_SLOT_KEYS = ("dingyin", DINGYIN_ZHIGE_KEY)


class WriteSource(Enum):
    """一次装备写入的来源，只用于决定 ``dingyin_type`` 怎么处理。"""

    #: 背包扫描：本次读到的展示状态就是这件装备的展示状态。
    BAG_SCAN = "bag_scan"
    #: 备战扫描：展示状态属于该方案，不改装备自身的。
    PLAN_SCAN = "plan_scan"
    #: 手动编辑、养成、组合应用等：调用方不负责判定展示状态，保持旧值。
    EDIT = "edit"


def _slot(equip: dict, key: str) -> dict | None:
    value = equip.get(key)
    return value if isinstance(value, dict) and value.get("name") else None


def _carry_transmute_target(source: dict, target: dict) -> None:
    """把 ``source`` 上的转律目标搬到 ``target`` 同一槽位（槽位词条同名时）。"""
    saved = saved_transmute_target(source)
    if saved is None:
        return
    index, name, value = saved
    before = (source.get(f"affix_{index}") or {}).get("name")
    affix = target.get(f"affix_{index}")
    if not isinstance(affix, dict) or affix.get("name") != before:
        return
    affix[TARGET_NAME_KEY] = name
    affix[TARGET_VALUE_KEY] = value


def merge_equipment_write(
    incoming: dict,
    existing: dict | None,
    *,
    source: WriteSource = WriteSource.EDIT,
) -> dict:
    """把一次写入合并到旧记录上，返回可直接落盘的新记录（不改入参）。

    ``existing`` 为空表示这件装备第一次入库；备战扫描仍只把本次类型记到
    方案，装备自身采用普通定音这个默认展示状态。
    """
    value = copy.deepcopy(incoming)
    if existing is None:
        if source is WriteSource.PLAN_SCAN:
            value[DINGYIN_TYPE_KEY] = DINGYIN_NORMAL
        return value

    # 一次扫描只可能读到一种定音，另一种槽必须原样留着——否则切到止戈扫一次
    # 就把普通定音抹了，切回原来的备战方案会发现定音没了。
    for key in DINGYIN_SLOT_KEYS:
        if _slot(value, key) is None and _slot(existing, key) is not None:
            value[key] = copy.deepcopy(existing[key])

    kind = str(value.get(DINGYIN_TYPE_KEY) or "")
    if source is not WriteSource.BAG_SCAN or kind not in DINGYIN_TYPES:
        old_kind = str(existing.get(DINGYIN_TYPE_KEY) or "")
        if old_kind in DINGYIN_TYPES:
            value[DINGYIN_TYPE_KEY] = old_kind
        else:
            value[DINGYIN_TYPE_KEY] = DINGYIN_NORMAL

    # 转律目标是用户算出来的计划，扫描数据永远不带它，只能从旧版本搬。
    if saved_transmute_target(value) is None:
        _carry_transmute_target(existing, value)
    return value


def union_dingyin_slots(target: dict, sources: list[dict]) -> None:
    """把若干条同一实体的旧记录的定音槽并进 ``target``（原地）。

    用于指纹合并：保留记录缺哪个槽就从被合并掉的记录里取第一个有值的。
    ``dingyin_type`` 以保留记录为准——它才是这件装备当前的实体。
    """
    for key in DINGYIN_SLOT_KEYS:
        if _slot(target, key) is not None:
            continue
        for item in sources:
            found = _slot(item, key)
            if found is not None:
                target[key] = copy.deepcopy(found)
                break
