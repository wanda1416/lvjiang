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

from fasteners import InterProcessLock

from lvjiang.core.user_file_locks import user_file_lock_path

from ..equipment_cooldown import next_cooldown_expiry
from .development_rules import check_real_development
from .equipment_write import (
    WriteSource,
    merge_equipment_write,
    union_dingyin_slots,
)
from .models import (
    EQUIPMENT_CREATED_AT,
    EQUIPMENT_SLOTS,
    EQUIPMENT_UPDATED_AT,
    LoadoutPlan,
    LoadoutState,
)
from .transmute import (
    TARGET_NAME_KEY,
    TARGET_VALUE_KEY,
    saved_transmute_target,
    strip_transmute_targets,
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
    source: WriteSource = WriteSource.EDIT,
) -> dict:
    """生成一次 fp 写入的数据：先过合并契约，再盖时间戳。

    新 fp 同时记录创建和更新时间；已有 fp 保留其创建时间并刷新更新时间。
    历史装备没有创建时间时保持空值，不能用当前时间伪造，也不允许调用方
    携带的旧时间覆盖仓储中的事实。

    合并放在这里而不是各调用点：写 ``equipment_items`` 的路径都要盖时间戳，
    盯住这一个出口就不会再漏掉某条路径整条替换掉定音或转律目标。
    """
    timestamp = now or _now_iso()
    value = merge_equipment_write(equip, existing, source=source)
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
    key = os.path.normcase(str(path.resolve()))
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(key, threading.RLock())


class LoadoutRepository:
    """Atomic persistence for data shared independently by WF and UI."""

    def __init__(self, username: str, users_dir: Path | None = None):
        if users_dir is None:
            from lvjiang.constants import USERS_DIR
            users_dir = USERS_DIR
        self.username = username
        self.users_dir = users_dir
        self.path = users_dir / f"{username}.loadouts.json"
        self._lock = _path_lock(self.path)

    def load(self) -> LoadoutState:
        """读取独立快照，不因浏览而创建文件或申请执行锁。"""
        with self._lock:
            try:
                data = self.path.read_text(encoding="utf-8")
            except FileNotFoundError:
                return LoadoutState.empty()
            return LoadoutState.from_dict(json.loads(data))

    def update(self, mutator: Callable[[LoadoutState], None]) -> LoadoutState:
        # 只串行化短暂的读改写事务，任务执行期间仍可浏览、编辑用户数据。
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            lock = InterProcessLock(str(user_file_lock_path(self.path)))
            if not lock.acquire(blocking=True, timeout=5):
                raise TimeoutError(f"装备数据写入锁超时: {self.path.name}")
            try:
                state = self.load()
                mutator(state)
                state.revision += 1
                self._save(state)
                return copy.deepcopy(state)
            finally:
                lock.release()

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
                    sub_martial_art: str, *, playstyle: str = "",
                    activate: bool = True) -> LoadoutPlan:
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
                sub_martial_art=sub_martial_art,
                playstyle=playstyle)
            state.plans[created.id] = created
            state.plan_order = state.ordered_plan_ids()
            if activate:
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
            order = state.ordered_plan_ids()
            index = order.index(plan_id)
            del state.plans[plan_id]
            state.plan_order = [pid for pid in order if pid != plan_id]
            if state.active_plan_id == plan_id:
                state.active_plan_id = state.plan_order[
                    min(index, len(state.plan_order) - 1)]
        self.update(mutate)

    def move_plan(self, plan_id: str, offset: int) -> None:
        """仅调整当前用户的方案展示顺序，不切换活动方案。"""
        if offset not in (-1, 1):
            raise ValueError("方案只能上移或下移一位")
        def mutate(state: LoadoutState) -> None:
            if plan_id not in state.plans:
                raise KeyError(plan_id)
            order = state.ordered_plan_ids()
            index = order.index(plan_id)
            target = index + offset
            if target < 0 or target >= len(order):
                return
            order[index], order[target] = order[target], order[index]
            state.plan_order = order
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

    def upsert_item(self, equip: dict,
                    *, source: WriteSource = WriteSource.BAG_SCAN) -> str:
        """写入一件背包/模拟装备。

        默认按背包扫描处理：背包里读到的定音就是这件装备该展示的定音。
        """
        fp = str(equip.get("_fp") or "")
        if not fp:
            from ..equip_parser.models import make_fingerprint
            is_mock = bool(equip.get("_extra", {}).get("is_mock"))
            fp = make_fingerprint(equip, is_mock=is_mock)
        if not fp:
            raise ValueError("装备数据无法生成指纹")
        def mutate(state: LoadoutState) -> None:
            state.equipment_items[fp] = stamp_equipment_write(
                equip, fp, state.equipment_items.get(fp), source=source)
        self.update(mutate)
        return fp

    def set_item_cooldown(self, fp: str, expires_at: str) -> LoadoutState:
        """只修改指定装备的冷却到期时间，指纹不变。"""
        if not isinstance(expires_at, str):
            raise TypeError("冷却到期时间必须是字符串")

        def mutate(state: LoadoutState) -> None:
            equip = state.equipment_items.get(fp)
            if equip is None:
                raise ValueError(f"装备已不存在: {fp}")
            equip["cooldown_expires_at"] = expires_at
            # 手动改到期时间不改变冷却类型：扫描出的"重置调律"不能因修正
            # 时间而变成"词条转律"；只有原本没有类型时才默认按转律记。
            if expires_at:
                equip["cooldown_kind"] = equip.get("cooldown_kind") or "transmute"
                equip["cooldown_state"] = "cooling"
            else:
                equip["cooldown_kind"] = ""
                equip["cooldown_state"] = ""
            equip[EQUIPMENT_UPDATED_AT] = _now_iso()

        return self.update(mutate)

    def set_item_lock_status(self, fp: str, locked: bool) -> LoadoutState:
        """只修改指定装备的锁定状态，指纹不变。"""
        if not isinstance(locked, bool):
            raise TypeError("锁定状态必须是布尔值")

        def mutate(state: LoadoutState) -> None:
            equip = state.equipment_items.get(fp)
            if equip is None:
                raise ValueError(f"装备已不存在: {fp}")
            equip["lock_status"] = "locked" if locked else "unlock"
            equip[EQUIPMENT_UPDATED_AT] = _now_iso()

        return self.update(mutate)

    def set_transmute_targets(
        self,
        plan_id: str,
        targets: dict[str, tuple[int, str, float] | None],
        *,
        expected_fps: set[str] | None = None,
    ) -> LoadoutState:
        """原子写入一组装备的转律目标；指纹、真实词条与更新时间戳均不变。

        ``targets`` 以指纹为键：值为 ``(词条序号, 目标名, 目标值)`` 表示写入，
        ``None`` 表示清除该件已有目标。``expected_fps`` 给出计算时方案八件
        装备的指纹快照；方案已经换装或装备被重扫改写时拒绝写入，要求重算。
        完全相同的重复应用不产生写入。
        """
        def mutate(state: LoadoutState) -> None:
            plan = state.plans.get(plan_id)
            if plan is None:
                raise ValueError(f"备战方案已不存在: {plan_id}")
            current = {fp for fp in plan.equipment.values() if fp}
            if expected_fps is not None and current != set(expected_fps):
                raise ValueError("备战方案装备已变化，请重新计算后再应用")
            for fp, target in targets.items():
                equip = state.equipment_items.get(fp)
                if equip is None:
                    raise ValueError(f"装备已不存在: {fp}")
                if target is None:
                    strip_transmute_targets(equip)
                    continue
                index, name, value = target
                affix = equip.get(f"affix_{index}")
                if not isinstance(affix, dict) or not affix.get("name"):
                    raise ValueError(f"装备 {fp} 第 {index} 条词条不存在")
                if not name or float(value) <= 0:
                    raise ValueError("转律目标名称与数值必须同时有效")
                strip_transmute_targets(equip)
                affix[TARGET_NAME_KEY] = str(name)
                affix[TARGET_VALUE_KEY] = float(value)

        before = self.load()
        after = copy.deepcopy(before)
        mutate(after)
        if after.to_dict() == before.to_dict():
            return before
        return self.update(mutate)

    def clear_transmute_target(self, fp: str) -> LoadoutState:
        """只删除指定装备的转律目标字段。"""
        return self.set_transmute_targets_for_items({fp: None})

    def set_transmute_targets_for_items(
        self, targets: dict[str, tuple[int, str, float] | None],
    ) -> LoadoutState:
        """不校验方案快照的目标写入，供单件清除使用。"""
        def mutate(state: LoadoutState) -> None:
            for fp, target in targets.items():
                equip = state.equipment_items.get(fp)
                if equip is None:
                    raise ValueError(f"装备已不存在: {fp}")
                strip_transmute_targets(equip)
                if target is None:
                    continue
                index, name, value = target
                affix = equip.get(f"affix_{index}")
                if not isinstance(affix, dict) or not affix.get("name"):
                    raise ValueError(f"装备 {fp} 第 {index} 条词条不存在")
                affix[TARGET_NAME_KEY] = str(name)
                affix[TARGET_VALUE_KEY] = float(value)

        return self.update(mutate)

    def assign_equipment(self, plan_id: str, slot_key: str,
                         equip: dict) -> str:
        """把装备挂到方案槽位。

        备战扫描读到的定音种类属于「切换备战方案时游戏自动切过去的状态」，
        不是用户给这件装备定的展示偏好，所以不写回装备自身。
        """
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
                equip, fp, state.equipment_items.get(fp),
                source=WriteSource.PLAN_SCAN)
            state.plans[plan_id].equipment[slot_key] = fp
        self.update(mutate)
        return fp

    def unassign(self, plan_id: str, slot_key: str) -> None:
        def mutate(state: LoadoutState) -> None:
            if plan_id not in state.plans:
                raise KeyError(plan_id)
            state.plans[plan_id].equipment[slot_key] = None
        self.update(mutate)

    def delete_items(
        self,
        fingerprints: set[str],
        *,
        preserve_referenced: bool = False,
        preserve_locked: bool = False,
    ) -> set[str]:
        """删除装备，按需原子保护备战引用或已锁定装备。

        保护集合必须在持有仓储写锁后重新计算，不能依赖确认对话框打开时的
        UI 快照；否则任务或其他页面在确认期间新建的引用仍可能被误删。
        返回实际删除的指纹，供调用方准确反馈结果。
        """
        deleted: set[str] = set()

        def mutate(state: LoadoutState) -> None:
            nonlocal deleted
            requested = set(fingerprints)
            if preserve_referenced:
                referenced = {
                    fp
                    for plan in state.plans.values()
                    for fp in plan.equipment.values()
                    if fp
                }
                requested.difference_update(referenced)
            if preserve_locked:
                requested = {
                    fp for fp in requested
                    if (state.equipment_items.get(fp) or {}).get(
                        "lock_status") != "locked"
                }
            deleted = requested & state.equipment_items.keys()
            for fp in deleted:
                state.equipment_items.pop(fp, None)
            for plan in state.plans.values():
                for slot, eq_fp in plan.equipment.items():
                    if eq_fp in deleted:
                        plan.equipment[slot] = None
        self.update(mutate)
        return deleted

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
                # 合并的是同一件实体：被合并掉那条身上的定音同样是这件装备的
                # 事实，保留记录缺哪个槽就从它们那里补，不能随记录一起丢掉。
                union_dingyin_slots(target, old_items)
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

    # ── 用户级 UI 状态：保存到 {user}.json，不改动高频的装备数据 ──

    COMBAT_PREFS_KEY = "combat_attrs"

    def get_combat_prefs(self) -> dict:
        """读取用户资料中的战斗属性页偏好。"""
        return self.get_ui_state(self.COMBAT_PREFS_KEY)

    def set_combat_prefs(self, prefs: dict) -> None:
        """写入用户资料中的战斗属性页偏好。"""
        self.set_ui_state(self.COMBAT_PREFS_KEY, prefs)

    # ── 用户级 UI 状态（筛选等） ──────────────────────────

    def get_ui_state(self, key: str) -> dict:
        """从 ``{user}.json`` 读取按用户隔离的界面状态。"""
        from lvjiang.core.user_config import get_user_ui_state

        return get_user_ui_state(self.username, key, self.users_dir)

    def set_ui_state(self, key: str, value: dict) -> None:
        """把按用户隔离的界面状态写入 ``{user}.json``。"""
        from lvjiang.core.user_config import set_user_ui_state

        set_user_ui_state(self.username, key, value, self.users_dir)

    def update_mock(self, old_fp: str, equip: dict) -> str:
        from ..equip_parser.models import make_fingerprint
        value = copy.deepcopy(equip)
        value.setdefault("_extra", {})["is_mock"] = True
        new_fp = make_fingerprint(value, is_mock=True)
        def mutate(state: LoadoutState) -> None:
            if not old_fp.startswith("mock_"):
                raise ValueError("只能编辑模拟装备")
            old = state.equipment_items.get(old_fp) or {}
            saved = saved_transmute_target(value)
            if saved is not None:
                index = saved[0]
                before = (old.get(f"affix_{index}") or {}).get("name")
                after = (value.get(f"affix_{index}") or {}).get("name")
                # 目标所在槽的词条已被改掉，旧目标不再成立。
                if before != after:
                    strip_transmute_targets(value)
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
        # 养成意味着等级、承音或词条已经真实变化，装备已用掉（或改变了）
        # 这次转律的前提，旧目标一律作废，需要重新分析。
        strip_transmute_targets(value)
        new_fp = make_fingerprint(value)
        if not new_fp:
            raise ValueError("装备数据无法生成指纹")

        def mutate(state: LoadoutState) -> None:
            old = state.equipment_items.get(old_fp)
            if old is None:
                raise ValueError(f"待养成装备已不存在: {old_fp}")
            # 指纹变了也还是同一件装备：两种定音都要跟过去，否则本次没带的
            # 那一种会随旧指纹一起被删掉。合并放在校验之前，校验看到的才是
            # 真正会落盘的内容。转律目标上面已按失效规则清掉，不会被搬回来。
            merged = merge_equipment_write(value, old)
            reason = check_real_development(old, merged)
            if reason:
                raise ValueError(reason)
            value.update(merged)
            if any(
                (old.get(f"affix_{index}") or {}).get("name")
                != (value.get(f"affix_{index}") or {}).get("name")
                for index in range(1, 6)
            ):
                from ...config import get_game_config

                game_config = get_game_config()
                carryover = (
                    game_config.is_equipment_cooldown_carryover_enabled())
                value["cooldown_expires_at"] = next_cooldown_expiry(
                    old.get("cooldown_expires_at"),
                    days=game_config.get_equipment_cooldown_days(),
                    carryover=carryover,
                )
                value["cooldown_kind"] = "transmute"
                value["cooldown_state"] = "cooling"
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
