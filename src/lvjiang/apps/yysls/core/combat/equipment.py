"""装备库存管理器 — 封装所有装备数据的读写操作。

所有对 equipped / bag_items / mock_items 的增删改查统一收口于此，
调用方不再直接操作 session dict。

数据结构约定
-----------
session_data = {
    "equipped":   {slot_key: equip_dict, ...},
    "bag_items":  {group_key: {fingerprint: equip_dict, ...}, ...},
    "mock_items": {group_key: {fingerprint: equip_dict, ...}, ...},
}
"""
from __future__ import annotations

import copy

from loguru import logger


class EquipmentInventory:
    """封装用户装备数据的增删改查。

    每次变更操作（equip / unequip / swap / replace / delete / add 等）
    内部自动 load → mutate → save，保证 session 文件与内存一致。
    纯查询方法（get_equipped / get_bag / get_all 等）不触发写盘。
    """

    def __init__(self, user_name: str) -> None:
        self._user_name = user_name
        self._equipped: dict[str, dict] = {}
        self._bag_items: dict[str, dict[str, dict]] = {}
        self._mock_items: dict[str, dict[str, dict]] = {}
        self.reload()

    # ── 加载 / 持久化 ──────────────────────────────────────

    def reload(self) -> None:
        """从 session 重新加载全部装备数据。"""
        from lvjiang.core.config import SessionManager

        data = SessionManager().load(self._user_name)
        self._equipped = dict(data.get("equipped", {}))
        bag = data.get("bag_items", {})
        self._bag_items = bag if isinstance(bag, dict) else {}
        mock = data.get("mock_items", {})
        self._mock_items = mock if isinstance(mock, dict) else {}

    def _save(self) -> None:
        """将当前内存状态写回 session。"""
        from lvjiang.core.config import SessionManager

        mgr = SessionManager()
        data = mgr.load(self._user_name)
        data["equipped"] = self._equipped
        data["bag_items"] = self._bag_items
        data["mock_items"] = self._mock_items
        mgr.save(self._user_name, data)

    # ── 只读属性 ───────────────────────────────────────────

    @property
    def equipped(self) -> dict[str, dict]:
        """当前已装备 ``{slot_key: equip_dict}``（只读快照）。"""
        return dict(self._equipped)

    @property
    def bag_items(self) -> dict[str, dict[str, dict]]:
        """背包 ``{group_key: {fp: equip_dict}}``（只读快照）。"""
        return dict(self._bag_items)

    @property
    def mock_items(self) -> dict[str, dict[str, dict]]:
        """模拟装备 ``{group_key: {fp: equip_dict}}``（只读快照）。"""
        return dict(self._mock_items)

    def get_equipped(self, slot_key: str) -> dict | None:
        """获取指定槽位的已装备物品，无则返回 None。"""
        eq = self._equipped.get(slot_key)
        return eq if isinstance(eq, dict) else None

    def get_all_candidates(
        self,
        slot_key: str,
        group_key: str,
    ) -> list[dict]:
        """收集某槽位的全部候选装备（已装备 + 背包 + 模拟）。"""
        result: list[dict] = []
        eq = self.get_equipped(slot_key)
        if eq:
            result.append(eq)
        bag_group = self._bag_items.get(group_key, {})
        if isinstance(bag_group, dict):
            result.extend(
                v for v in bag_group.values() if isinstance(v, dict)
            )
        mock_group = self._mock_items.get(group_key, {})
        if isinstance(mock_group, dict):
            result.extend(
                v for v in mock_group.values() if isinstance(v, dict)
            )
        return result

    # ── 内部工具 ───────────────────────────────────────────

    @staticmethod
    def _fingerprint(equip: dict) -> str:
        """获取装备指纹：优先用已存储的 _fp，否则实时计算。"""
        fp = equip.get("_fp", "")
        if fp:
            return fp
        from ..equip_parser.models import make_fingerprint

        return make_fingerprint(
            equip,
            is_mock=equip.get("_extra", {}).get("is_mock", False),
        )

    @staticmethod
    def _is_mock(equip: dict) -> bool:
        return equip.get("_extra", {}).get("is_mock", False)

    @staticmethod
    def _group_key_for(equip: dict) -> str:
        """通过装备 type 推断 group_key。"""
        from ...config import get_game_config

        equip_type = equip.get("type", "")
        return get_game_config().get_type_to_group().get(equip_type, "")

    @staticmethod
    def _remove_from_store(
        store: dict[str, dict[str, dict]],
        group_key: str,
        fp: str,
    ) -> dict | None:
        """从 {group: {fp: equip}} 字典中移除一项，返回被移除的 equip。"""
        group = store.get(group_key)
        if not isinstance(group, dict) or fp not in group:
            return None
        removed = group.pop(fp)
        if not group:
            del store[group_key]
        return removed

    # ── 核心操作 ───────────────────────────────────────────

    def unequip(self, slot_key: str) -> dict | None:
        """卸载槽位装备 → 回存背包或模拟库。

        Returns
        -------
        被卸载的装备 dict，槽位为空时返回 None。
        """
        equip = self.get_equipped(slot_key)
        if equip is None:
            return None

        group_key = self._group_key_for(equip)
        if group_key:
            fp = self._fingerprint(equip)
            if fp:
                if self._is_mock(equip):
                    self._mock_items.setdefault(group_key, {})[fp] = equip
                else:
                    self._bag_items.setdefault(group_key, {})[fp] = equip

        self._equipped.pop(slot_key, None)
        self._save()
        logger.debug(f"卸载 {slot_key}: {equip.get('name', '?')}")
        return equip

    def equip_to_slot(
        self,
        slot_key: str,
        equip: dict,
        group_key: str,
    ) -> dict | None:
        """从背包/模拟穿戴到指定槽位，旧装备自动回存。

        Parameters
        ----------
        slot_key : 目标槽位
        equip : 要穿戴的装备
        group_key : 装备所在分组（"weapon" / "head" / …）

        Returns
        -------
        被替换下来的旧装备，无旧装备时返回 None。
        """
        current = self.get_equipped(slot_key)
        if current is not None and current == equip:
            return None
        old = self._equip_to_slot_in_memory(slot_key, equip, group_key)
        self._save()
        logger.debug(f"装备 {equip.get('name', '?')} → {slot_key}")
        return old

    def _equip_to_slot_in_memory(
        self, slot_key: str, equip: dict, group_key: str,
    ) -> dict | None:
        """标准穿戴内存实现；调用方负责保存或事务回滚。"""
        old = self.get_equipped(slot_key)
        if old is not None and old == equip:
            return None
        new_fp = self._fingerprint(equip)

        # 旧装备回存
        if old is not None:
            old_group_key = self._group_key_for(old)
            old_fp = self._fingerprint(old)
            if old_group_key and old_fp:
                if self._is_mock(old):
                    self._mock_items.setdefault(old_group_key, {})[old_fp] = old
                else:
                    self._bag_items.setdefault(old_group_key, {})[old_fp] = old

        # 从来源 store 移除新装备
        if self._is_mock(equip):
            self._remove_from_store(self._mock_items, group_key, new_fp)
        else:
            self._remove_from_store(self._bag_items, group_key, new_fp)

        self._equipped[slot_key] = equip
        return old

    def replace_equipped(
        self,
        slot_key: str,
        new_equip: dict,
    ) -> dict | None:
        """直接替换槽位装备（模拟装备编辑场景），旧装备丢弃不回存。

        Returns
        -------
        被替换的旧装备。
        """
        old = self.get_equipped(slot_key)
        self._equipped[slot_key] = new_equip
        self._save()
        return old

    def delete_from_bag(self, group_key: str, fp: str) -> bool:
        """从背包删除装备。返回是否实际删除了。"""
        removed = self._remove_from_store(self._bag_items, group_key, fp)
        if removed:
            self._save()
            logger.debug(f"背包删除: {removed.get('name', '?')}")
            return True
        return False

    def delete_from_mock(self, group_key: str, fp: str) -> bool:
        """从模拟库删除装备。返回是否实际删除了。"""
        removed = self._remove_from_store(self._mock_items, group_key, fp)
        if removed:
            self._save()
            logger.debug(f"模拟删除: {removed.get('name', '?')}")
            return True
        return False

    def add_to_bag(self, group_key: str, equip: dict) -> None:
        """添加装备到背包。"""
        fp = self._fingerprint(equip)
        self._bag_items.setdefault(group_key, {})[fp] = equip
        self._save()

    def add_to_mock(self, group_key: str, equip: dict) -> None:
        """添加装备到模拟库。"""
        fp = self._fingerprint(equip)
        self._mock_items.setdefault(group_key, {})[fp] = equip
        self._save()

    def update_mock(
        self,
        old_group_key: str,
        old_fp: str,
        new_equip: dict,
        new_group_key: str,
    ) -> None:
        """更新模拟装备（移除旧条目 + 添加新条目）。"""
        self._remove_from_store(self._mock_items, old_group_key, old_fp)
        new_fp = self._fingerprint(new_equip)
        self._mock_items.setdefault(new_group_key, {})[new_fp] = new_equip
        self._save()

    # ── 批量操作 ───────────────────────────────────────────

    def apply_combos(self, combo_equipped: dict[str, dict]) -> None:
        """原子应用组合：全部校验后内存变更，最后只保存一次。"""
        changes: list[tuple[str, dict, str]] = []
        for slot_key, new_eq in combo_equipped.items():
            current = self.get_equipped(slot_key)
            if current is not None and current == new_eq:
                continue
            group_key = self._group_key_for(new_eq)
            if not group_key:
                raise ValueError(
                    f"无法识别装备 {new_eq.get('name', '?')} 的分组"
                )
            changes.append((slot_key, new_eq, group_key))
        if not changes:
            return

        snapshot = (
            copy.deepcopy(self._equipped),
            copy.deepcopy(self._bag_items),
            copy.deepcopy(self._mock_items),
        )
        try:
            for slot_key, new_eq, group_key in changes:
                self._equip_to_slot_in_memory(slot_key, new_eq, group_key)
            self._save()
        except Exception:
            self._equipped, self._bag_items, self._mock_items = snapshot
            raise

        parts = [
            f"{slot_key}={equip.get('name', '?')}"
            for slot_key, equip, _group_key in changes
        ]
        logger.info(f"应用组合: {', '.join(parts)}")

