"""Compatibility facade backed by the unified fp-keyed equipment pool."""
from __future__ import annotations

import copy

from loguru import logger

from ..loadout import EQUIPMENT_SLOTS, LoadoutRepository
from ..loadout.repository import stamp_equipment_write


class EquipmentInventory:
    def __init__(self, user_name: str) -> None:
        self._repo = LoadoutRepository(user_name)
        self.reload()

    def reload(self) -> None:
        self._state = self._repo.load()
        # 每次加载都用当前游戏配置刷新异常状态，使历史装备无需重新扫描也能
        # 发现新增的合法性异常；止戈定音使用独立标记，不混入异常原因。
        try:
            from ..equip_parser.dingyin_parser import refresh_dingyin_marker_dict
            from ..equip_validator import annotate_equipment_dict
        except Exception as e:  # noqa: BLE001 - 附加审计绝不能阻断装备加载
            logger.error(f"装备状态审计组件加载失败（不影响装备加载）: {e}")
            return
        # 逐件兜底：try 若包住整个循环，一件历史脏数据就会让它后面所有装备
        # 都不刷新，卡片上继续挂着上一轮遗留的旧「!」原因。
        for fp, equip in self._state.equipment_items.items():
            try:
                annotate_equipment_dict(equip)
                refresh_dingyin_marker_dict(equip)
            except Exception as e:  # noqa: BLE001 - 附加审计绝不能阻断装备加载
                logger.error(f"装备 {fp} 状态审计失败（不影响装备加载）: {e}")

    @property
    def state(self):
        """本次加载的只读快照（``LoadoutState``）；写操作后由 ``reload`` 刷新。"""
        return self._state

    @property
    def active_school(self) -> str:
        """当前激活方案的流派，由主副武学派生（``plan_school``）。"""
        from ...config import get_game_config
        return self._state.active_school(get_game_config().get_schools())

    @property
    def equipped(self) -> dict[str, dict]:
        return copy.deepcopy(self._state.resolved_equipment())

    @property
    def active_plan_id(self) -> str:
        return self._state.active_plan_id

    @property
    def active_plan(self):
        return self._state.active_plan

    @property
    def active_plan_fps(self) -> set[str]:
        """当前激活方案占用的装备指纹集合。"""
        return {fp for fp in self._state.active_plan.equipment.values() if fp}

    @property
    def referenced_plan_fps(self) -> set[str]:
        """被任意备战方案引用的装备指纹集合。"""
        return {
            fp
            for plan in self._state.plans.values()
            for fp in plan.equipment.values()
            if fp
        }

    @property
    def standby_plan_fps(self) -> set[str]:
        """被其他（非激活）方案引用、但未在当前方案中装备的指纹集合。"""
        return self.referenced_plan_fps - self.active_plan_fps

    @property
    def locked_item_fps(self) -> set[str]:
        """当前装备池中已锁定装备的指纹集合。"""
        return {
            fp for fp, equip in self._state.equipment_items.items()
            if equip.get("lock_status") == "locked"
        }

    def get_equipped(self, slot_key: str) -> dict | None:
        return self.equipped.get(slot_key)

    def _grouped(self, mock: bool) -> dict[str, dict[str, dict]]:
        from ...config import get_game_config
        type_to_group = get_game_config().get_type_to_group()
        result: dict[str, dict[str, dict]] = {}
        for fp, equip in self._state.equipment_items.items():
            if fp.startswith("mock_") != mock:
                continue
            group = type_to_group.get(equip.get("type", ""), "")
            if group:
                result.setdefault(group, {})[fp] = copy.deepcopy(equip)
            else:
                logger.warning(
                    f"装备 type={equip.get('type', '')!r} 无法映射到分组，"
                    f"已从背包视图跳过 (fp={fp})"
                )
        return result

    @property
    def bag_items(self) -> dict[str, dict[str, dict]]:
        return self._grouped(False)

    @property
    def mock_items(self) -> dict[str, dict[str, dict]]:
        return self._grouped(True)

    def get_all_candidates(self, slot_key: str, group_key: str) -> list[dict]:
        values = list(self.bag_items.get(group_key, {}).values())
        values += list(self.mock_items.get(group_key, {}).values())
        current = self.get_equipped(slot_key)
        return ([current] if current else []) + values

    def unequip(self, slot_key: str) -> dict | None:
        old = self.get_equipped(slot_key)
        self._repo.unassign(self._state.active_plan_id, slot_key)
        self.reload()
        return old

    def delete_items(
        self,
        fingerprints: set[str],
        *,
        preserve_referenced: bool = False,
        preserve_locked: bool = False,
    ) -> set[str]:
        """批量删除装备，并可保护备战引用或已锁定装备。"""
        deleted: set[str] = set()
        if fingerprints:
            deleted = self._repo.delete_items(
                fingerprints,
                preserve_referenced=preserve_referenced,
                preserve_locked=preserve_locked,
            )
            self.reload()
        return deleted

    def delete_all_mock(self) -> int:
        """删除全部模拟装备并返回删除数量。"""
        count = self._repo.delete_all_mock()
        self.reload()
        return count

    def equip_to_slot(self, slot_key: str, equip: dict, group_key: str) -> dict | None:
        old = self.get_equipped(slot_key)
        self._repo.assign_equipment(self._state.active_plan_id, slot_key, equip)
        self.reload()
        return old

    def replace_equipped_mock(self, slot_key: str, old_fp: str,
                              new_equip: dict) -> dict | None:
        """编辑已装备的模拟装备：先写新数据，后清理旧指纹（见仓储层）。"""
        old = self.get_equipped(slot_key)
        self._repo.update_equipped_mock(
            self._state.active_plan_id, slot_key, old_fp, new_equip)
        self.reload()
        return old

    def delete_from_bag(self, group_key: str, fp: str) -> bool:
        exists = fp in self._state.equipment_items and not fp.startswith("mock_")
        if exists:
            self._repo.delete_items({fp})
            self.reload()
        return exists

    def delete_from_mock(self, group_key: str, fp: str) -> bool:
        exists = fp in self._state.equipment_items and fp.startswith("mock_")
        if exists:
            self._repo.delete_items({fp})
            self.reload()
        return exists

    def _warn_group_mismatch(self, group_key: str, equip: dict) -> None:
        """新架构分组由 type 推导；若调用方传入的 group_key 不一致则告警。"""
        if not group_key:
            return
        from ...config import get_game_config
        expected = get_game_config().get_type_to_group().get(equip.get("type", ""), "")
        if expected and expected != group_key:
            logger.warning(
                f"group_key 不匹配: 传入 {group_key!r}, 由 type 推导为 {expected!r}"
            )

    def add_to_bag(self, group_key: str, equip: dict) -> None:
        self._warn_group_mismatch(group_key, equip)
        self._repo.upsert_item(equip)
        self.reload()

    def add_to_mock(self, group_key: str, equip: dict) -> None:
        self._warn_group_mismatch(group_key, equip)
        value = copy.deepcopy(equip)
        value.setdefault("_extra", {})["is_mock"] = True
        value.pop("_fp", None)
        self._repo.upsert_item(value)
        self.reload()

    def update_mock(self, old_group_key: str, old_fp: str, new_equip: dict,
                    new_group_key: str) -> None:
        self._repo.update_mock(old_fp, new_equip)
        self.reload()

    def update_real_development(self, old_fp: str, new_equip: dict) -> str:
        """保存扫描装备的受限养成，并迁移所有备战方案引用。"""
        new_fp = self._repo.update_real_development(old_fp, new_equip)
        self.reload()
        return new_fp

    def set_item_cooldown(self, fp: str, expires_at: str) -> None:
        """更新装备冷却到期时间，不重新审计全部装备。"""
        self._state = self._repo.set_item_cooldown(fp, expires_at)

    def set_item_lock_status(self, fp: str, locked: bool) -> None:
        """更新装备锁定状态，不重新审计全部装备。"""
        self._state = self._repo.set_item_lock_status(fp, locked)

    def set_plan_dingyin(self, slot_key: str, kind: str) -> None:
        """切换当前方案某个槽位展示哪种定音，不动装备自身的展示状态。"""
        self._state = self._repo.set_plan_dingyin(
            self._state.active_plan_id, slot_key, kind)

    def set_item_dingyin_type(self, fp: str, kind: str) -> None:
        """切换背包中某件装备默认展示哪种定音，不动任何方案的选择。"""
        self._state = self._repo.set_item_dingyin_type(fp, kind)

    def apply_transmute_targets(
        self,
        targets: dict[str, tuple[int, str, float] | None],
        *,
        expected_fps: set[str] | None = None,
    ) -> None:
        """把转律建议写入当前方案的装备；方案装备已变化时抛错要求重算。"""
        self._state = self._repo.set_transmute_targets(
            self._state.active_plan_id, targets, expected_fps=expected_fps)

    def clear_transmute_target(self, fp: str) -> None:
        self._state = self._repo.clear_transmute_target(fp)

    def apply_combos(self, combo_equipped: dict[str, dict]) -> None:
        plan_id = self._state.active_plan_id
        for slot in combo_equipped:
            if slot not in EQUIPMENT_SLOTS:
                raise ValueError(f"未知装备槽位: {slot}")

        def mutate(state):
            from ..equip_parser.models import make_fingerprint
            if plan_id not in state.plans:
                raise ValueError("目标备战方案已不存在")
            plan = state.plans[plan_id]
            for slot, equip in combo_equipped.items():
                fp = str(equip.get("_fp") or "") or make_fingerprint(
                    equip, is_mock=bool(equip.get("_extra", {}).get("is_mock")))
                # 组合应用写回的是仓储里的原始装备，不带定音假设，也不表达
                # 任何展示意图：走统一入口保住两种定音和转律目标即可。
                state.equipment_items[fp] = stamp_equipment_write(
                    equip, fp, state.equipment_items.get(fp))
                if plan.equipment[slot] != fp:
                    plan.dingyin.pop(slot, None)
                plan.equipment[slot] = fp
        self._repo.update(mutate)
        self.reload()
