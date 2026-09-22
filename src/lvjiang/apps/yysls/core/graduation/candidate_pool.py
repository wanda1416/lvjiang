"""最优组合的候选池：从仓储快照按方案武学与筛选条件分发到八个槽位。

这是领域逻辑，不属于页面：武器按方案两门武学派生的武器类型精确分配到
主/副武器槽，防具与首饰按分组落槽。仓储 ``equipment_items`` 按 fp 一条一
记录，已穿戴只是方案里对某条记录的引用，背包 / 模拟视图又是同一批记录按
分组摆放——候选就是这批记录做减法（模拟开关、武器类型、等级与词条筛选），
不存在重复，也不需要任何第二套“是否同一件”的判断。页面只负责把结果摆成
勾选列表。
"""
from __future__ import annotations

from dataclasses import dataclass

from ...config.equipment_slots import EQUIPMENT_SLOTS
from ..equip_parser.dingyin_parser import has_normal_dingyin

#: 非武器分组 → 槽位；武器按方案武器类型分配，不在此表。
GROUP_TO_SLOTS: dict[str, tuple[str, ...]] = {
    "head": ("head",),
    "chest": ("chest",),
    "ring": ("ring",),
    "pendant": ("pendant",),
    "leg": ("leg",),
    "wrist": ("wrist",),
}


@dataclass(frozen=True)
class CandidateFilter:
    """装备页当前筛选：等级门槛与词条筛选（``all`` / ``dingyin`` / ``full_tuning``）。"""

    level_threshold: int = 0
    affix_filter: str = "all"

    def passes(self, equip: dict) -> bool:
        try:
            level = int(equip.get("level") or 0)
        except (TypeError, ValueError):
            level = 0
        if self.level_threshold > 0 and level < self.level_threshold:
            return False
        if self.affix_filter == "dingyin":
            # 只认普通定音：毕业率模型只读 dingyin，止戈贡献为 0，
            # 放它进候选池等于让一件没定音的装备冒充定过音。
            return has_normal_dingyin(equip)
        if self.affix_filter == "full_tuning":
            return all(
                isinstance(equip.get(f"affix_{i}"), dict)
                and bool(equip[f"affix_{i}"].get("name"))
                for i in range(1, 6)
            )
        return True


def candidate_passes(
    slot_key: str, equip: dict, *,
    main_weapon_type: str, sub_weapon_type: str,
    filters: CandidateFilter,
) -> bool:
    """统一应用方案武学派生的武器类型、等级和词条筛选。"""
    required_weapon = (main_weapon_type if slot_key == "main_weapon"
                       else sub_weapon_type if slot_key == "sub_weapon" else "")
    if required_weapon and equip.get("type", "") != required_weapon:
        return False
    return filters.passes(equip)


def collect_candidates(
    bag_items: dict[str, dict[str, dict]],
    mock_items: dict[str, dict[str, dict]] | None,
    *,
    main_weapon_type: str,
    sub_weapon_type: str,
    filters: CandidateFilter,
) -> dict[str, list[dict]]:
    """按槽位收集候选：背包记录 + 可选模拟记录，保持仓储出现顺序。

    ``mock_items`` 为 None 表示排除模拟装备（已穿戴的模拟件同样不在背包
    视图里，自然被排除）。两个武器槽分别按方案当前位置上的武学派生类型
    分配，与流派配置中武学的声明顺序无关。
    """
    slot_candidates: dict[str, list[dict]] = {key: [] for key in EQUIPMENT_SLOTS}

    def passes(slot_key: str, eq: dict) -> bool:
        return candidate_passes(
            slot_key, eq, main_weapon_type=main_weapon_type,
            sub_weapon_type=sub_weapon_type, filters=filters)

    pools: list[dict] = [bag_items]
    if mock_items is not None:
        pools.append(mock_items)
    for pool in pools:
        if not isinstance(pool, dict):
            continue
        for group_key, items_dict in pool.items():
            if not isinstance(items_dict, dict):
                continue
            if group_key == "weapon":
                for eq in items_dict.values():
                    if not isinstance(eq, dict):
                        continue
                    eq_type = eq.get("type", "")
                    if (main_weapon_type and eq_type == main_weapon_type
                            and passes("main_weapon", eq)):
                        slot_candidates["main_weapon"].append(eq)
                    if (sub_weapon_type and eq_type == sub_weapon_type
                            and passes("sub_weapon", eq)):
                        slot_candidates["sub_weapon"].append(eq)
                continue
            slots = GROUP_TO_SLOTS.get(group_key, ())
            if not slots:
                continue
            for eq in items_dict.values():
                if not isinstance(eq, dict):
                    continue
                for sk in slots:
                    if passes(sk, eq):
                        slot_candidates[sk].append(eq)
    return slot_candidates
