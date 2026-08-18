from __future__ import annotations

import copy
import json
import os
import tempfile
import threading
from collections.abc import Callable
from pathlib import Path
from uuid import uuid4

from .models import EQUIPMENT_SLOTS, LoadoutPlan, LoadoutState

_LOCKS_GUARD = threading.Lock()
_LOCKS: dict[str, threading.RLock] = {}


def _path_lock(path: Path) -> threading.RLock:
    key = str(path.resolve())
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(key, threading.RLock())


class LoadoutRepository:
    """Atomic persistence for data shared independently by WF and UI."""

    def __init__(self, username: str, users_dir: Path | None = None):
        if users_dir is None:
            from lvjiang.constants import USERS_DIR
            users_dir = USERS_DIR
        users_dir.mkdir(parents=True, exist_ok=True)
        self.username = username
        self.path = users_dir / f"{username}.loadouts.json"
        self._lock = _path_lock(self.path)

    def load(self) -> LoadoutState:
        with self._lock:
            if not self.path.exists():
                state = LoadoutState.empty()
                self._save(state)
                return state
            return LoadoutState.from_dict(json.loads(
                self.path.read_text(encoding="utf-8")))

    def update(self, mutator: Callable[[LoadoutState], None]) -> LoadoutState:
        with self._lock:
            state = self.load()
            mutator(state)
            state.revision += 1
            self._save(state)
            return copy.deepcopy(state)

    def _save(self, state: LoadoutState) -> None:
        payload = json.dumps(state.to_dict(), ensure_ascii=False, indent=2)
        fd, tmp = tempfile.mkstemp(
            dir=str(self.path.parent), prefix=f".{self.path.stem}_", suffix=".tmp")
        tmp_path = Path(tmp)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(payload)
            os.replace(tmp_path, self.path)
        finally:
            if tmp_path.exists():
                tmp_path.unlink()

    def create_plan(self, name: str, main_martial_art: str,
                    sub_martial_art: str) -> LoadoutPlan:
        """新建方案：必须同时绑定主武学与副武学，不允许无武学方案。"""
        main_martial_art = main_martial_art.strip()
        sub_martial_art = sub_martial_art.strip()
        if not main_martial_art or not sub_martial_art:
            raise ValueError("新建方案必须同时绑定主武学和副武学")
        created: LoadoutPlan | None = None
        def mutate(state: LoadoutState) -> None:
            nonlocal created
            created = LoadoutPlan(
                id=uuid4().hex, name=name.strip() or "未命名方案",
                main_martial_art=main_martial_art,
                sub_martial_art=sub_martial_art)
            state.plans[created.id] = created
            state.active_plan_id = created.id
        self.update(mutate)
        assert created is not None
        return created

    def delete_plan(self, plan_id: str) -> None:
        def mutate(state: LoadoutState) -> None:
            if len(state.plans) <= 1:
                raise ValueError("至少保留一个备战方案")
            if plan_id not in state.plans:
                raise KeyError(plan_id)
            del state.plans[plan_id]
            if state.active_plan_id == plan_id:
                state.active_plan_id = next(iter(state.plans))
        self.update(mutate)

    def switch_plan(self, plan_id: str) -> None:
        def mutate(state: LoadoutState) -> None:
            if plan_id not in state.plans:
                raise KeyError(plan_id)
            state.active_plan_id = plan_id
        self.update(mutate)

    def configure_plan(self, plan_id: str, *, name: str | None = None,
                       main_martial_art: str | None = None,
                       sub_martial_art: str | None = None) -> None:
        def mutate(state: LoadoutState) -> None:
            if plan_id not in state.plans:
                raise KeyError(plan_id)
            plan = state.plans[plan_id]
            if name is not None:
                plan.name = name.strip() or plan.name
            if main_martial_art is not None:
                plan.main_martial_art = main_martial_art
            if sub_martial_art is not None:
                plan.sub_martial_art = sub_martial_art
        self.update(mutate)

    def upsert_item(self, equip: dict) -> str:
        fp = str(equip.get("_fp") or "")
        if not fp:
            from ..equip_parser.models import make_fingerprint
            is_mock = bool(equip.get("_extra", {}).get("is_mock"))
            fp = make_fingerprint(equip, is_mock=is_mock)
        if not fp:
            raise ValueError("装备数据无法生成指纹")
        value = copy.deepcopy(equip)
        value["_fp"] = fp
        self.update(lambda state: state.equipment_items.__setitem__(fp, value))
        return fp

    def assign_equipment(self, plan_id: str, slot_key: str, equip: dict) -> str:
        if slot_key not in EQUIPMENT_SLOTS:
            raise ValueError(f"未知装备槽位: {slot_key}")
        fp = str(equip.get("_fp") or "")
        if not fp:
            from ..equip_parser.models import make_fingerprint
            fp = make_fingerprint(
                equip, is_mock=bool(equip.get("_extra", {}).get("is_mock")))
        if not fp:
            raise ValueError("装备数据无法生成指纹")
        value = copy.deepcopy(equip)
        value["_fp"] = fp
        def mutate(state: LoadoutState) -> None:
            if plan_id not in state.plans:
                raise ValueError("目标备战方案已不存在")
            state.equipment_items[fp] = value
            state.plans[plan_id].equipment[slot_key] = fp
        self.update(mutate)
        return fp

    def unassign(self, plan_id: str, slot_key: str) -> None:
        def mutate(state: LoadoutState) -> None:
            if plan_id not in state.plans:
                raise KeyError(plan_id)
            state.plans[plan_id].equipment[slot_key] = None
        self.update(mutate)

    def delete_items(self, fingerprints: set[str]) -> None:
        def mutate(state: LoadoutState) -> None:
            for fp in fingerprints:
                state.equipment_items.pop(fp, None)
            for plan in state.plans.values():
                for slot, fp in plan.equipment.items():
                    if fp in fingerprints:
                        plan.equipment[slot] = None
        self.update(mutate)

    def delete_all_real(self) -> None:
        def mutate(state: LoadoutState) -> None:
            fps = {fp for fp in state.equipment_items if not fp.startswith("mock_")}
            for fp in fps:
                state.equipment_items.pop(fp, None)
            for plan in state.plans.values():
                for slot, fp in plan.equipment.items():
                    if fp in fps:
                        plan.equipment[slot] = None
        self.update(mutate)

    def update_equipped_mock(self, plan_id: str, slot_key: str,
                             old_fp: str, equip: dict) -> str:
        """编辑已装备槽位中的模拟装备。

        顺序约束：先写入新数据并迁移槽位引用，再清理旧指纹。
        宁可短暂存在脏数据，也不能丢失新数据；旧指纹仅在无任何
        方案引用时才清除，避免误删被其他方案共用的装备。
        """
        from ..equip_parser.models import make_fingerprint
        value = copy.deepcopy(equip)
        value.setdefault("_extra", {})["is_mock"] = True
        new_fp = make_fingerprint(value, is_mock=True)
        value["_fp"] = new_fp
        def mutate(state: LoadoutState) -> None:
            if plan_id not in state.plans:
                raise ValueError("目标备战方案已不存在")
            # 第一步：写入新数据并切换槽位引用
            state.equipment_items[new_fp] = value
            state.plans[plan_id].equipment[slot_key] = new_fp
            # 第二步：清理旧指纹（仅当新旧不同且不再被任何方案引用）
            if old_fp and old_fp != new_fp:
                referenced = any(
                    fp == old_fp
                    for plan in state.plans.values()
                    for fp in plan.equipment.values()
                )
                if not referenced:
                    state.equipment_items.pop(old_fp, None)
        self.update(mutate)
        return new_fp

    def update_mock(self, old_fp: str, equip: dict) -> str:
        from ..equip_parser.models import make_fingerprint
        value = copy.deepcopy(equip)
        value.setdefault("_extra", {})["is_mock"] = True
        new_fp = make_fingerprint(value, is_mock=True)
        value["_fp"] = new_fp
        def mutate(state: LoadoutState) -> None:
            if not old_fp.startswith("mock_"):
                raise ValueError("只能编辑模拟装备")
            state.equipment_items.pop(old_fp, None)
            state.equipment_items[new_fp] = value
            for plan in state.plans.values():
                for slot, fp in plan.equipment.items():
                    if fp == old_fp:
                        plan.equipment[slot] = new_fp
        self.update(mutate)
        return new_fp
