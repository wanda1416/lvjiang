from __future__ import annotations

import copy
import json
import os
import tempfile
import threading
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .models import (
    EQUIPMENT_CREATED_AT,
    EQUIPMENT_SLOTS,
    EQUIPMENT_UPDATED_AT,
    LoadoutPlan,
    LoadoutState,
)

_LOCKS_GUARD = threading.Lock()
_LOCKS: dict[str, threading.RLock] = {}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def stamp_equipment_write(
    equip: dict,
    fp: str,
    existing: dict | None,
    *,
    now: str | None = None,
) -> dict:
    """生成一次 fp 写入的数据。

    新 fp 同时记录创建和更新时间；已有 fp 保留其创建时间并刷新更新时间。
    历史装备没有创建时间时保持空值，不能用当前时间伪造，也不允许调用方
    携带的旧时间覆盖仓储中的事实。
    """
    timestamp = now or _now_iso()
    value = copy.deepcopy(equip)
    value["_fp"] = fp
    if existing is None:
        value[EQUIPMENT_CREATED_AT] = timestamp
    else:
        created = existing.get(EQUIPMENT_CREATED_AT)
        value[EQUIPMENT_CREATED_AT] = (
            created if isinstance(created, str) else "")
    value[EQUIPMENT_UPDATED_AT] = timestamp
    return value


def _timestamp_rank(value) -> float | None:
    """合法 ISO 时间转排序值；缺失/损坏都视为无数据。"""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def _pick_timestamp(values, *, latest: bool) -> str:
    valid = [
        (rank, value)
        for value in values
        if (rank := _timestamp_rank(value)) is not None
    ]
    if not valid:
        return ""
    return (max if latest else min)(valid, key=lambda item: item[0])[1]


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
                       sub_martial_art: str | None = None,
                       playstyle: str | None = None,
                       base_attribute: str | None = None,
                       gongjue: str | None = None,
                       graduation_scheme: str | None = None) -> None:
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
            if playstyle is not None:
                plan.playstyle = playstyle
            if base_attribute is not None:
                plan.base_attribute = base_attribute
            if gongjue is not None:
                plan.gongjue = gongjue
            if graduation_scheme is not None:
                plan.graduation_scheme = graduation_scheme
        self.update(mutate)

    def upsert_item(self, equip: dict) -> str:
        fp = str(equip.get("_fp") or "")
        if not fp:
            from ..equip_parser.models import make_fingerprint
            is_mock = bool(equip.get("_extra", {}).get("is_mock"))
            fp = make_fingerprint(equip, is_mock=is_mock)
        if not fp:
            raise ValueError("装备数据无法生成指纹")
        def mutate(state: LoadoutState) -> None:
            state.equipment_items[fp] = stamp_equipment_write(
                equip, fp, state.equipment_items.get(fp))
        self.update(mutate)
        return fp

    def set_item_cooldown(self, fp: str, expires_at: str) -> None:
        """只修改指定装备的冷却到期时间，指纹不变。"""
        if not isinstance(expires_at, str):
            raise TypeError("冷却到期时间必须是字符串")

        def mutate(state: LoadoutState) -> None:
            equip = state.equipment_items.get(fp)
            if equip is None:
                raise ValueError(f"装备已不存在: {fp}")
            equip["cooldown_expires_at"] = expires_at
            equip[EQUIPMENT_UPDATED_AT] = _now_iso()

        self.update(mutate)

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
        def mutate(state: LoadoutState) -> None:
            if plan_id not in state.plans:
                raise ValueError("目标备战方案已不存在")
            state.equipment_items[fp] = stamp_equipment_write(
                equip, fp, state.equipment_items.get(fp))
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
                for slot, eq_fp in plan.equipment.items():
                    if eq_fp in fingerprints:
                        plan.equipment[slot] = None
        self.update(mutate)

    def delete_all_mock(self) -> int:
        """删除全部模拟装备并清理所有方案引用，返回删除数量。"""
        deleted = 0

        def mutate(state: LoadoutState) -> None:
            nonlocal deleted
            fingerprints = {
                fp for fp in state.equipment_items if fp.startswith("mock_")
            }
            deleted = len(fingerprints)
            for fp in fingerprints:
                state.equipment_items.pop(fp, None)
            for plan in state.plans.values():
                for slot, referenced_fp in plan.equipment.items():
                    if referenced_fp in fingerprints:
                        plan.equipment[slot] = None

        self.update(mutate)
        return deleted

    def merge_items(self, replacements: dict[str, str]) -> None:
        """原子删除旧快照并把所有备战方案引用迁移到保留版本。"""
        clean = {
            str(old): str(new)
            for old, new in replacements.items()
            if old and new and old != new
        }
        if not clean:
            return

        def resolve(fp: str) -> str:
            seen: set[str] = set()
            while fp in clean:
                if fp in seen:
                    raise ValueError("装备合并关系存在循环")
                seen.add(fp)
                fp = clean[fp]
            return fp

        resolved = {old: resolve(new) for old, new in clean.items()}

        def mutate(state: LoadoutState) -> None:
            for old, new in resolved.items():
                if old not in state.equipment_items:
                    raise ValueError(f"待合并装备已不存在: {old}")
                if new not in state.equipment_items:
                    raise ValueError(f"保留装备已不存在: {new}")
            for plan in state.plans.values():
                for slot, fp in plan.equipment.items():
                    if fp in resolved:
                        plan.equipment[slot] = resolved[fp]
            # 合并只是在存量快照之间建立同一实体关系，不算一次装备内容更新。
            # 保留版本继承整组最早创建时间和最新更新时间；全组旧数据都没有
            # 时间时明确写空，绝不退化成 Unix 纪元。
            grouped: dict[str, list[dict]] = {}
            for old, new in resolved.items():
                grouped.setdefault(new, []).append(state.equipment_items[old])
            for new, old_items in grouped.items():
                target = state.equipment_items[new]
                all_items = [target, *old_items]
                target[EQUIPMENT_CREATED_AT] = _pick_timestamp(
                    (item.get(EQUIPMENT_CREATED_AT) for item in all_items),
                    latest=False)
                target[EQUIPMENT_UPDATED_AT] = _pick_timestamp(
                    (item.get(EQUIPMENT_UPDATED_AT) for item in all_items),
                    latest=True)
            for old in resolved:
                state.equipment_items.pop(old, None)

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
        def mutate(state: LoadoutState) -> None:
            if plan_id not in state.plans:
                raise ValueError("目标备战方案已不存在")
            # 第一步：写入新数据并切换槽位引用
            state.equipment_items[new_fp] = stamp_equipment_write(
                value, new_fp, state.equipment_items.get(new_fp))
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

    # ── 用户级 UI 状态（筛选等） ──────────────────────────

    def get_ui_state(self, key: str) -> dict:
        """读取用户级 UI 状态节点（如 equip_filter）。"""
        state = self.load()
        value = state.ui_state.get(key)
        return dict(value) if isinstance(value, dict) else {}

    def set_ui_state(self, key: str, value: dict) -> None:
        """写入用户级 UI 状态节点。"""
        def mutate(state: LoadoutState) -> None:
            state.ui_state[key] = value
        self.update(mutate)

    def update_mock(self, old_fp: str, equip: dict) -> str:
        from ..equip_parser.models import make_fingerprint
        value = copy.deepcopy(equip)
        value.setdefault("_extra", {})["is_mock"] = True
        new_fp = make_fingerprint(value, is_mock=True)
        def mutate(state: LoadoutState) -> None:
            if not old_fp.startswith("mock_"):
                raise ValueError("只能编辑模拟装备")
            stamped = stamp_equipment_write(
                value, new_fp, state.equipment_items.get(new_fp))
            state.equipment_items.pop(old_fp, None)
            state.equipment_items[new_fp] = stamped
            for plan in state.plans.values():
                for slot, fp in plan.equipment.items():
                    if fp == old_fp:
                        plan.equipment[slot] = new_fp
        self.update(mutate)
        return new_fp
