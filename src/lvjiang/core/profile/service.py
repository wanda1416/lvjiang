"""Profile 共享读写管线

提供 profile_action() 作为所有写入来源的统一入口，
以及 profile_read() / profile_read_all() 作为读取的统一入口：
- UI 增量/覆写（overview._adjust_value）
- DSL profile_set / profile_inc / profile_get / profile_all
- sync_engine 递归写入（_sync_write_adapter）

所有路径共享同一套 clamp → delta → db_upsert → sync_targets 管线，
保证行为一致：历史记录格式、触发器同步、上下限钳位。
"""

from __future__ import annotations

import math
from datetime import datetime

from loguru import logger

from ...errors import ExpectedError
from ...i18n import tr
from .maintenance import profile_operation, profile_write
from .models import (
    MODEL_NOTE,
    MODEL_QUOTA,
    MODEL_REGEN,
    MODEL_STOCK,
    QuotaKeyDef,
    RegenKeyDef,
    note_numeric_value,
)
from .periods import get_period_boundary
from .regen import (
    compute_realtime_value,
    compute_regen_entry,
    is_realtime_regen,
)
from .regen_gate import period_recovery_allowed
from .repository import (
    db_get_history,
    db_get_history_record,
    db_is_locked,
    db_read_all,
    db_read_entry,
    db_update_if_current,
    db_upsert,
)
from .rows import Entry
from .schema import get_profile_config

# ─── 内部工具函数 ─────────────────────────────────────────────


class ProfileWriteConflict(RuntimeError, ExpectedError):
    """写入冲突：目标已被其他进程改过，本次写入未生效。

    属于预期结果而不是缺陷——重读后再试即可。归入 `ExpectedError` 后，统一记账
    只记一行 WARNING，不打印堆栈（详见 `lvjiang/errors.py`）。
    """

    log_level = "WARNING"


class ProfileOperationRefused(ValueError, ExpectedError):
    """当前状态不允许这次 Profile 操作（例如目标已被锁定）。

    原因本身就是业务规则，会原样展示给用户；归入 `ExpectedError` 后日志只记
    一行 WARNING，不打印堆栈。保留 `ValueError` 血统以兼容既有捕获点。
    """

    log_level = "WARNING"


def profile_history_undo_unavailable_reason(record: dict) -> str | None:
    """撤销前的准入判断；可撤销时返回 ``None``。"""
    if not record:
        return tr("历史记录不存在")
    if record.op_type not in ("set", "delta"):
        return tr("仅可撤销写入记录")
    username = str(record.username)
    model_type = str(record.type)
    key = str(record.key)
    latest = db_get_history(username, model_type, key, limit=1)
    if not latest or latest[0].local_id != record.local_id:
        return tr("只能撤销该用户此数据项的最新记录")
    current = db_read_entry(username, model_type, key)
    if not current:
        return tr("当前数据项已不存在")
    if model_type == MODEL_NOTE:
        if (current.value_text or "") != (record.value_text or ""):
            return tr("数据已发生变化，请刷新后重试")
    else:
        expected = record.new_value
        if expected is None or float(current.value or 0) != float(expected):
            return tr("数据已发生变化，请刷新后重试")
    return None


@profile_write
def undo_profile_history(history_id: int) -> None:
    """按历史记录反向写一次：数值给反向增量，文本写回旧值。

    撤销就是一次普通写入——记账按写入方意图（数值是增量、文本是绝对值），来源记成
    「撤销：<原来源>」，所以它进同一份账本，也可以再被撤销。
    """
    record = db_get_history_record(history_id)
    reason = profile_history_undo_unavailable_reason(record)
    if reason is not None:
        raise ProfileOperationRefused(reason)
    username = str(record.username)
    key = str(record.key)
    source = f"撤销：{record.source or '手动'}"
    if str(record.type) == MODEL_NOTE:
        profile_action(username, key, set_value=record.old_value_text or "",
                       source=source, trigger_sync=False)
        return
    reverse = float(record.old_value or 0) - float(record.new_value or 0)
    profile_action(username, key, delta=reverse, source=source, trigger_sync=False)


@profile_write
def delete_profile_history(history_id: int, username: str, model_type: str, key: str) -> None:
    from .repository import get_profile_db

    if not get_profile_db().delete_history(history_id, username, model_type, key):
        raise ProfileWriteConflict(tr('该条历史已不存在或归属不匹配，请刷新后重试'))


def _is_continuous_regen(kd) -> bool:
    return isinstance(kd, RegenKeyDef) and is_realtime_regen(kd)


def _normalize_float_noise(value: float) -> float:
    """归整极小浮点误差；真实小数不处理。"""
    nearest = round(value)
    if abs(value - nearest) < 1e-9:
        return float(nearest)
    return value


def _read_current_value(username: str, model_type: str, key: str, kd) -> float | str | None:
    """读取当前值（regen 模型按实时计算，note 返回文本或 None）

    锁定 key 视为物理失效：返回 None，且不参与 regen 实时计算——锁定期间
    值必须停在锁定时的样子，不能每次读取都算出一个"活"的数字。
    """
    if db_is_locked(username, model_type, key):
        return None
    if model_type == MODEL_NOTE:
        entry = db_read_entry(username, model_type, key)
        return entry.value_text or None
    entry = db_read_entry(username, model_type, key)
    if entry is None:
        return 0.0
    current = entry.value or 0
    if model_type == MODEL_QUOTA and isinstance(kd, QuotaKeyDef):
        current = _quota_display_value(entry, kd)
    if model_type == MODEL_REGEN and kd and isinstance(kd, RegenKeyDef):
        if _is_continuous_regen(kd):
            current = compute_realtime_value(
                entry.value or 0,
                entry.event_at,
                kd,
            )
        else:
            current = compute_regen_entry(entry, kd).value
    return current


def _quota_display_value(entry: dict, kd: QuotaKeyDef) -> float:
    """配额跨过周期边界后读出来是 0，不另写一条 reset。"""
    stored = float(entry.value or 0)
    event_at = entry.event_at or ""
    from .arbitrate import parse_event

    event = parse_event(event_at)
    if event is None:
        return 0.0
    try:
        boundary = get_period_boundary(
            kd.period, kd.reset_time, datetime.now(), getattr(kd, "reset_day", 0),
        )
    except (TypeError, ValueError):
        return stored
    if event < boundary:
        return 0.0
    return stored


def normalize_note_text(text: str, kd) -> str:
    """note 写入值：能转成数字就取整（硬上限再截断），否则原样保留

    note 存的是自由文本，但配置里仍然给它保留了「上限 / 展示上限」两个开关。
    既然开关在，填进来的又确实是数字，就按数值语义归一：取整 + 按硬上限截断。
    这样展示上限时 ``X/Y`` 两边才是同一量纲，不会出现 ``12.7/20`` 或
    ``25/20`` 这种自相矛盾的显示。

    填的是「已完成」这类文字则跳过取整与取上限，原样存下——展示层仍会照常
    补 ``/Y``，那是展示开关的职责，与值本身能不能算数无关。

    软上限（``soft=True``）沿用数值模型的语义：只提醒不截断，所以只取整。
    """
    number = note_numeric_value(text)
    if number is None:
        return text
    value = round(number)
    cap = getattr(kd, "cap", None)
    if cap is not None and not getattr(kd, "soft", False):
        value = min(value, cap)
    return str(value)


def _clamp(new_value: float, model_type: str, kd) -> float:
    """按模型类型钳位：下限 0，上限按 cap/soft 规则"""
    new_value = max(0, new_value)

    if kd:
        if model_type == MODEL_QUOTA:
            cap = getattr(kd, "cap", None)
            soft = getattr(kd, "soft", False)
            if cap is not None and not soft:
                new_value = min(new_value, cap)

        elif model_type == MODEL_REGEN:
            cap = getattr(kd, "cap", None)
            if cap is not None:
                new_value = min(new_value, cap)
            if abs(new_value - int(new_value)) < 1e-9:
                new_value = float(int(new_value))

        elif model_type == MODEL_STOCK:
            cap = getattr(kd, "cap", None)
            soft = getattr(kd, "soft", False)
            if cap is not None and not soft:
                new_value = min(new_value, cap)

    return new_value


# ─── 共享读取管线 ─────────────────────────────────────────────


def profile_read(username: str, key: str) -> float | str | None:
    """读取 profile 单个 key 的当前值（regen 自动实时计算，note 返回文本）

    key 未在 profile.yaml 中定义或无数据时返回 None。
    锁定 key 视为物理失效也返回 None：引擎完全忽略该值，调用方要区分
    "有没有锁"请用 ``profile_is_locked``。
    与 UI 读取路径共用 _read_current_value，保证行为一致。
    """
    config = get_profile_config()
    model_type = config.get_model_type(key)
    if model_type is None:
        return None
    entry = db_read_entry(username, model_type, key)
    if not entry:
        return None
    if entry.locked:
        return None
    kd = config.get_key(key)
    return _read_current_value(username, model_type, key, kd)


def profile_is_locked(username: str, key: str) -> bool:
    """该用户的这个 key 是否已锁定（DSL 的 ``profile_locked`` 走这里）"""
    config = get_profile_config()
    model_type = config.get_model_type(key)
    if model_type is None:
        return False
    return db_is_locked(username, model_type, key)


def profile_read_all(username: str) -> dict:
    """读取全部 profile 数据（regen 自动实时计算）

    返回 {model_type: {key: {value, event_at, ...}}} 结构。
    regen 条目的 value 已按当前时间计算。

    锁定条目保留 key 的存在性（带 ``locked: True``），但 value/value_text 一律
    为 None/"" —— 与 ``profile_read`` 返回 None 的口径一致：锁定的 key 对这个
    用户物理失效，`profile_all` 的消费方不该再读到冻结值。
    """
    entries = db_read_all(username)
    config = get_profile_config()
    for entry in entries:
        if entry.locked:
            entry.value = None
            entry.value_text = ""
    index = {(entry.type, entry.key): entry for entry in entries}
    for kd in config.get_keys_by_model(MODEL_QUOTA):
        if not isinstance(kd, QuotaKeyDef):
            continue
        entry = index.get((MODEL_QUOTA, kd.key))
        if entry and not entry.locked:
            entry.value = _quota_display_value(entry, kd)
    for kd in config.get_keys_by_model(MODEL_REGEN):
        if not isinstance(kd, RegenKeyDef):
            continue
        entry = index.get((MODEL_REGEN, kd.key))
        if entry and not entry.locked:
            entry.value = compute_regen_entry(entry, kd).value
    # JSON 边界：DSL / workbench 读的是可序列化结构，只在这里转一次
    payload: dict[str, dict[str, dict]] = {}
    for entry in entries:
        payload.setdefault(entry.type, {})[entry.key] = {
            "value": entry.value,
            "value_text": entry.value_text,
            "event_at": entry.event_at,
            "created_at": entry.created_at,
            "op_id": entry.op_id,
            "locked": entry.locked,
        }
    return payload


@profile_operation
def profile_observe(
    username: str,
    key: str,
    value: float,
    *,
    source: str = "OCR 观测",
) -> dict:
    """将外部观测值按当前配额周期单调同步到 Profile。

    同一周期内拒绝小于已存值的观测，避免 OCR 脏数据回写；若数据库条目
    属于上一个周期，则以本周期初始值 0 比较，允许周/月/日重置后的合法
    归零。返回值适合直接暴露给 DSL。
    """
    config = get_profile_config()
    model_type = config.get_model_type(key)
    kd = config.get_key(key, model_type=model_type) if model_type else None
    if model_type is not None and db_is_locked(username, model_type, key):
        # 锁定 key 物理失效：写入被拒，不能把"什么都没写"报成 accepted
        logger.error(
            f"profile_observe: {username}.{model_type}.{key} 已锁定，拒绝观测写入")
        return {"accepted": False, "value": -1, "reason": "locked"}
    if model_type != MODEL_QUOTA or not isinstance(kd, QuotaKeyDef):
        current = profile_read(username, key)
        return {
            "accepted": False,
            "value": current if isinstance(current, (int, float)) else -1,
            "reason": "undefined" if model_type is None else "not_quota",
        }

    try:
        candidate = float(value)
    except (TypeError, ValueError):
        return {"accepted": False, "value": -1, "reason": "invalid"}
    if not math.isfinite(candidate) or candidate < 0:
        return {"accepted": False, "value": -1, "reason": "invalid"}

    for _ in range(3):
        entry = db_read_entry(username, MODEL_QUOTA, key)
        now = datetime.now()
        boundary = get_period_boundary(
            kd.period, kd.reset_time, now, getattr(kd, "reset_day", 0)
        )
        is_current_period = False
        if entry:
            try:
                is_current_period = datetime.fromisoformat(
                    entry.event_at
                ) >= boundary
            except (TypeError, ValueError):
                is_current_period = False

        current = _quota_display_value(entry, kd) if entry else 0.0
        if not is_current_period:
            # 跨周期：先把旧周期满值原子归零落库（历史里留一条 reset），
            # 再按新周期从 0 起算写入本次观测；只做显示归零会让库里长期留着旧值。
            reset = db_update_if_current(
                username,
                MODEL_QUOTA,
                key,
                expected_value=(entry.value if entry else 0) or 0,
                expected_updated_at=entry.event_at if entry else "",
                expected_entry_exists=entry is not None,
                new_value=0,
                op_type="reset",
                delta_value=-float((entry.value if entry else 0) or 0),
                source="周期重置",
            )
            if not reset:
                continue
            if candidate == 0:
                return {"accepted": True, "value": 0.0, "reason": "updated"}
            # 已原子推进到当前周期；下一轮按同周期观测写入并触发同步。
            continue
        if candidate < current:
            return {"accepted": False, "value": current, "reason": "regressed"}
        if candidate == current:
            return {"accepted": True, "value": current, "reason": "unchanged"}

        try:
            written = profile_action(
                username,
                key,
                set_value=candidate,
                source=source,
                current_value=current,
                expected_entry=entry,
                trigger_sync=True,
                use_cas=True,
            )
        except ProfileWriteConflict:
            continue
        return {"accepted": True, "value": written, "reason": "updated"}

    latest = profile_read(username, key)
    return {
        "accepted": False,
        "value": latest if isinstance(latest, (int, float)) else -1,
        "reason": "conflict",
    }


# ─── 共享写入管线 ─────────────────────────────────────────────


@profile_write
def profile_action(
    username: str,
    key: str,
    *,
    model_type: str | None = None,
    delta: float | None = None,
    set_value: float | str | None = None,
    source: str = tr("DSL 写入"),
    current_value: float | None = None,
    expected_entry: Entry | None = None,
    trigger_sync: bool = True,
    use_cas: bool = False,
    regen_progress_source: str = "target",
    force_write: bool = False,
) -> float | str:
    """统一 profile 写入入口

    数值模型（quota/regen/stock）：
    读当前值 → 计算新值 → clamp → 算 actual_delta → db_upsert → sync_targets

    note 模型：
    直接写入 value_text 列，不走数值管线，不触发同步。

    Parameters
    ----------
    username:
        用户名。
    key:
        profile key（自动识别模型类型）。
    delta:
        增量（与 set_value 二选一）。
    set_value:
        绝对设定值（与 delta 二选一）。数值模型内部转化为 delta；note 模型直接存储文本。
        内部转化为 delta = set_value - current，后续管线完全一致。
    source:
        变更来源，写入 history。

    Returns
    -------
    float | str
        数值模型返回写入后的新值；note 模型返回写入的文本；key 未定义时返回 0。
    """
    config = get_profile_config()
    model_type = model_type or config.get_model_type(key)
    if model_type is None:
        logger.warning(f"profile_action: key '{key}' 未在 profile.yaml 中定义")
        return 0
    kd = config.get_key(key, model_type=model_type)

    # ── 锁定短路：锁定 key 物理失效，任何写入一律拒绝 ──
    # 记 error 而不是 warning：走到这里说明调用方没先查 profile_locked，
    # 属于漏判，需要在日志里暴露出来（正常流程应当先判锁再决定是否写）。
    current_entry = db_read_entry(username, model_type, key)
    if current_entry is not None and current_entry.locked:
        logger.error(
            f"profile 写入被拒绝：{username}.{model_type}.{key} 已锁定"
            f"（来源 {source}）")
        if model_type == MODEL_NOTE:
            return current_entry.value_text or ""
        return current_entry.value or 0

    # ── note 短路：文本直接写入，不走数值管线 ──
    if model_type == MODEL_NOTE:
        if kd is None:
            logger.warning(f"profile_action: note key '{key}' 未在 profile.yaml 中定义")
            return ""
        text = str(set_value) if set_value is not None else (str(delta) if delta is not None else "")
        text = normalize_note_text(text, kd)
        old_entry = db_read_entry(username, model_type, key)
        old_text = old_entry.value_text if old_entry else None
        if old_entry and text == (old_text or "") and not force_write:
            return text
        from .triggers import (
            prepare_trigger_event,
            submit_profile_trigger,
            trigger_context,
        )
        event = prepare_trigger_event(
            username=username,
            model=model_type,
            key=key,
            old_value=old_text,
            new_value=text,
            delta=None,
            source=source,
            op_type="set",
            script=kd.change_script,
            synchronous=kd.change_script_sync,
        )
        if event is None:
            return old_text or ""
        if event.synchronous:
            submit_profile_trigger(event)
        db_upsert(
            username, model_type, key, 0,
            op_type="set",
            value_text=text,
            source=source,
        )
        with trigger_context(event):
            if not event.synchronous:
                submit_profile_trigger(event)
        return text

    # ── 1. 读当前值 ──
    if current_value is None or set_value is not None:
        _raw = _read_current_value(username, model_type, key, kd)
        # note 模型已提前返回，此处必为数值类型；None/str 兜底为 0
        current_value = float(_raw or 0) if _raw is None or isinstance(_raw, str) else _raw

    # ── 1.5 regen 固化：先把「上次写入至今的恢复」落成一条 set ──
    # 不固化就直接写，历史行会出现「old 是陈旧存储值、delta 却按恢复后基线算」，
    # 两处基线不一致：回放（delta 相加）、撤销、按来源聚合都会算错，
    # 当天的恢复也会从账本里消失。
    if model_type == MODEL_REGEN and isinstance(kd, RegenKeyDef):
        stored_value = float((current_entry.value if current_entry else 0) or 0)
        restored = compute_regen_entry(current_entry, kd)
        restored_value = float(restored.value)
        restored_at = restored.event_at
        if int(restored_value) != int(stored_value):
            # 闸门：本次进程尚未完成首次同步时不得物化，否则会把「基于过期锚点
            # 算出的恢复」写成绝对值 set 上行，覆盖别的设备真实发生的消耗
            # （2026-11-09 体力事故）。同步完成后锚点已在本周期，这里自然不触发。
            if period_recovery_allowed():
                db_upsert(
                    username, model_type, key, restored_value,
                    event_at=restored_at,
                    # 物化用 set 而非 tick：set 会随上行通道推送，「锚点 set + delta」
                    # 成对到达回放端，delta 加在新锚点上，回放端无需再算 regen。
                    op_type="set",
                    delta_value=_normalize_float_noise(restored_value - stored_value),
                    source=tr("自动恢复"),
                )
                current_entry = db_read_entry(username, model_type, key)
                current_value = float(restored.value)
                if expected_entry is not None:
                    # 固化是本事务自己写的：CAS 期望值必须跟着换，否则会被误判成
                    # 「其他进程更新过」，单进程一次消耗也会报冲突。
                    expected_entry = current_entry

    # ── 2. 计算目标新值 ──
    if set_value is not None:
        target = float(set_value)
    else:
        target = current_value + float(delta or 0)
    target = _normalize_float_noise(target)

    # ── 3. clamp ──
    new_value = _normalize_float_noise(_clamp(target, model_type, kd))

    # clamp 后值未变 → 不产生写入
    if new_value == current_value and not force_write:
        return current_value

    # ── 4. 计算 clamp 后的实际 delta ──
    actual_delta = _normalize_float_noise(new_value - current_value)
    semantic_new_value = new_value

    # realtime 直接存精确小数和真实时间，不再回拨 event_at。
    write_value = new_value
    op_type = "set"
    ledger_delta = actual_delta
    from .triggers import prepare_trigger_event, submit_profile_trigger, trigger_context
    event = None
    if actual_delta != 0:
        event = prepare_trigger_event(
            username=username,
            model=model_type,
            key=key,
            old_value=current_value,
            new_value=semantic_new_value,
            delta=actual_delta,
            source=source,
            op_type=op_type,
            script=kd.change_script if kd else "",
            synchronous=kd.change_script_sync if kd else False,
        )
        if event is None:
            return current_value
        if event.synchronous:
            submit_profile_trigger(event)
    if trigger_sync and use_cas and expected_entry is not None:
        # 三态：None=调用方不做 CAS；EXPECT_ABSENT=期望该行不存在（原空 dict）；
        # Entry=按打开界面时的快照做 CAS。
        absent = expected_entry is EXPECT_ABSENT
        updated = db_update_if_current(
            username, model_type, key,
            expected_value=(0 if absent else expected_entry.value) or 0,
            expected_updated_at="" if absent else expected_entry.event_at,
            expected_entry_exists=not absent,
            new_value=write_value,
            new_updated_at=None,
            op_type=op_type,
            delta_value=ledger_delta,
            source=source,
        )
        if not updated:
            raise ProfileWriteConflict(f"{username} {model_type}.{key} CAS failed")
    else:
        db_upsert(
            username, model_type, key, write_value,
            op_type=op_type,
            delta_value=ledger_delta,
            source=source,
        )
    logger.debug(
        f"profile_action: {username}.{model_type}.{key} = "
        f"{semantic_new_value} (delta={actual_delta:+g})"
    )

    # ── 8. 触发器同步 ──
    if event is not None:
        with trigger_context(event):
            if not event.synchronous:
                submit_profile_trigger(event)
            if trigger_sync and kd and kd.sync_targets:
                from .sync import fire_sync_targets
                fire_sync_targets(
                    write_fn=sync_write_adapter,
                    user_name=username,
                    source_kd=kd,
                    delta=actual_delta,
                    source=source,
                )

    return semantic_new_value


# ─── sync_engine 独立写入适配器 ──────────────────────────────


#: CAS 期望「该条目还不存在」的哨兵（对应旧协议里传空 dict 的语义）。
EXPECT_ABSENT = object()

@profile_write
def sync_write_adapter(
    user_name: str,
    model_type: str,
    key: str,
    *,
    delta: int | float,
    op_type: str = "delta",
    sync_from: str | None = None,
    source: str = "",
) -> tuple[int | float, int | float] | None:
    """同步引擎的写入适配器：读当前值 → 加 delta → clamp → 写入

    返回 (new_value, applied_delta)；写入失败返回 None，
    引擎据此中止该目标的下游递归。供 fire_sync_targets 注入使用。
    """
    config = get_profile_config()
    kd = config.get_key(key, model_type=model_type)
    if db_is_locked(user_name, model_type, key):
        # 目标被用户锁定：不是"写成功"，必须返回 None 让引擎中止下游递归，
        # 否则源 key 已扣、目标没动，还会继续往更下游传。
        logger.error(
            f"sync 目标已锁定，跳过同步写入：{user_name}.{model_type}.{key}")
        return None

    current_value = _read_current_value(user_name, model_type, key, kd)
    # sync 适配器仅用于数值模型
    if current_value is None or isinstance(current_value, str):
        current_value = 0.0
    new_value = _normalize_float_noise(_clamp(current_value + delta, model_type, kd))
    semantic_new_value = new_value
    actual_delta = _normalize_float_noise(semantic_new_value - current_value)

    # clamp 后值未变 → 不产生写入
    if new_value == current_value:
        return current_value, 0

    from .triggers import prepare_trigger_event, submit_profile_trigger, trigger_context
    event = prepare_trigger_event(
        username=user_name,
        model=model_type,
        key=key,
        old_value=current_value,
        new_value=semantic_new_value,
        delta=actual_delta,
        source=source,
        op_type="set",
        script=kd.change_script if kd else "",
        synchronous=kd.change_script_sync if kd else False,
    )
    if event is None:
        return current_value, 0

    if event.synchronous:
        submit_profile_trigger(event)

    try:
        db_upsert(
            user_name, model_type, key, new_value,
            op_type="set",
            delta_value=actual_delta,
            sync_from=sync_from,
            source=source,
        )
    except Exception as e:
        logger.error(f"sync_write_adapter failed: {user_name}.{model_type}.{key}: {e}")
        raise

    with trigger_context(event):
        if not event.synchronous:
            submit_profile_trigger(event)
    return semantic_new_value, actual_delta


