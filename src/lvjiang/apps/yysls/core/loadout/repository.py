from __future__ import annotations

import copy
import json
import os
import tempfile
import threading
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
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

    def update_real_development(self, old_fp: str, equip: dict) -> str:
        """原子保存真实装备的转律/承音/培养结果。

        真实装备的指纹会随等级、承音和词条变化；因此必须在
        同一次仓储更新中写入新指纹、迁移全部方案引用并删除旧版。
        """
        from ..equip_parser.models import make_fingerprint

        value = copy.deepcopy(equip)
        if old_fp.startswith("mock_") or bool(
            (value.get("_extra") or {}).get("is_mock")):
            raise ValueError("扫描装备养成不支持模拟装备")
        new_fp = make_fingerprint(value)
        if not new_fp:
            raise ValueError("装备数据无法生成指纹")

        def mutate(state: LoadoutState) -> None:
            old = state.equipment_items.get(old_fp)
            if old is None:
                raise ValueError(f"待养成装备已不存在: {old_fp}")
            self._validate_real_development(old, value)
            if any(
                (old.get(f"affix_{index}") or {}).get("name")
                != (value.get(f"affix_{index}") or {}).get("name")
                for index in range(1, 6)
            ):
                from ...config import get_game_config

                days = get_game_config().get_equipment_cooldown_days()
                value["cooldown_expires_at"] = (
                    datetime.now(timezone.utc) + timedelta(days=days)
                ).isoformat(timespec="milliseconds")
            stamped = stamp_equipment_write(
                value, new_fp, state.equipment_items.get(new_fp))
            target = state.equipment_items.get(new_fp)
            stamped[EQUIPMENT_CREATED_AT] = _pick_timestamp(
                (old.get(EQUIPMENT_CREATED_AT),
                 target.get(EQUIPMENT_CREATED_AT) if target else None),
                latest=False,
            )
            state.equipment_items[new_fp] = stamped
            for plan in state.plans.values():
                for slot, fp in plan.equipment.items():
                    if fp == old_fp:
                        plan.equipment[slot] = new_fp
            if old_fp != new_fp:
                state.equipment_items.pop(old_fp, None)

        self.update(mutate)
        return new_fp

    @staticmethod
    def _validate_real_development(old: dict, new: dict) -> None:
        """仓储边界的真实装备不可变字段与单向变化校验。"""
        immutable = (
            "type", "name", "quality", "original_level",
            "base_attr", "base_attr_2",
        )
        changed = [key for key in immutable if old.get(key) != new.get(key)]
        if changed:
            raise ValueError(
                "扫描装备的既定属性不可修改: " + "、".join(changed))
        old_level = int(old.get("level") or 0)
        new_level = int(new.get("level") or 0)
        if new_level < old_level:
            raise ValueError("承音后的装备等级不能降低")
        old_chengyin = bool(old.get("is_chengyin"))
        new_chengyin = bool(new.get("is_chengyin"))
        if old_chengyin and not new_chengyin:
            raise ValueError("承音状态不能撤销")
        if new_level != old_level:
            from ...config import get_game_config

            configs = sorted(
                get_game_config().get_level_configs(),
                key=lambda item: item.level,
            )
            current = next(
                (item for item in configs if item.level == old_level), None)
            next_level = next(
                (item.level for item in configs if item.level > old_level), None)
            if (old_chengyin or not new_chengyin or current is None
                    or not current.allow_chengyin or new_level != next_level):
                raise ValueError("承音只能提升到下一个已配置等级")
        elif old_chengyin != new_chengyin:
            raise ValueError("承音必须同时提升装备等级")

        old_dingyin = old.get("dingyin") or {}
        new_dingyin = new.get("dingyin") or {}
        if old_dingyin.get("name") != new_dingyin.get("name"):
            raise ValueError("扫描装备不能新增、删除或更换定音词条")
        if float(new_dingyin.get("value") or 0) < float(
                old_dingyin.get("value") or 0):
            raise ValueError("培养只能提高定音数值")

        changed_names: list[int] = []
        old_transferred: list[int] = []
        for index in range(1, 6):
            before = old.get(f"affix_{index}") or {}
            after = new.get(f"affix_{index}") or {}
            before_transferred = bool(before.get("is_transferred"))
            after_transferred = bool(after.get("is_transferred"))
            if before_transferred:
                old_transferred.append(index)
            if before.get("name") != after.get("name"):
                changed_names.append(index)
                if not before.get("name") or not after.get("name"):
                    raise ValueError("扫描装备不能新增或删除词条")
                continue
            if before_transferred != after_transferred:
                raise ValueError("转律槽位标记不能单独修改")
            if float(after.get("value") or 0) < float(before.get("value") or 0):
                raise ValueError("培养只能提高词条数值")
        if len(changed_names) > 1 or changed_names == [1]:
            raise ValueError("转律只能修改商角徵羽中的一个词条")
        if old_transferred and changed_names and changed_names != old_transferred:
            raise ValueError("再次转律只能修改原固定槽位")
        if changed_names:
            changed_affix = new.get(f"affix_{changed_names[0]}") or {}
            if not changed_affix.get("is_transferred"):
                raise ValueError("转律后的词条必须标记 is_transferred")
