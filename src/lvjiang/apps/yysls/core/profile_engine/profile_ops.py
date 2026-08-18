"""Profile 共享读写管线

提供 profile_action() 作为所有写入来源的统一入口，
以及 profile_read() / profile_read_all() 作为读取的统一入口：
- UI 增减/覆写（overview._adjust_value）
- DSL profile_set / profile_inc / profile_get / profile_all
- sync_engine 递归写入（_sync_write_adapter）

所有路径共享同一套 clamp → delta → detail → db_upsert → sync_targets 管线，
保证行为一致：历史记录格式、触发器同步、上下限钳位。
"""

from __future__ import annotations

import math
from datetime import datetime

from loguru import logger

from .....i18n import tr
from ...config.profile_models import (
    MODEL_NOTE,
    MODEL_QUOTA,
    MODEL_REGEN,
    MODEL_STOCK,
    RegenKeyDef,
)
from ...config.user_profile import get_profile_config
from .profile_db import db_read_all, db_read_entry, db_update_if_current, db_upsert
from .regen_math import (
    compute_realtime_value,
    compute_regen_entry,
    is_realtime_regen,
    normalize_realtime_write,
)

# ─── 内部工具函数 ─────────────────────────────────────────────


class ProfileWriteConflict(RuntimeError):
    """CAS 写入冲突。"""


def _is_continuous_regen(kd) -> bool:
    return isinstance(kd, RegenKeyDef) and is_realtime_regen(kd)


def _normalize_continuous_regen_write(kd: RegenKeyDef, raw_value: float) -> tuple[float, str]:
    return normalize_realtime_write(kd, max(0.0, float(raw_value)))


def _normalize_float_noise(value: float) -> float:
    """归整极小浮点误差；真实小数不处理。"""
    nearest = round(value)
    if abs(value - nearest) < 1e-9:
        return float(nearest)
    return value


def _read_current_value(username: str, model_type: str, key: str, kd) -> float | str | None:
    """读取当前值（regen 模型按实时计算，note 返回文本或 None）"""
    if model_type == MODEL_NOTE:
        entry = db_read_entry(username, model_type, key)
        return entry.get("value_text", "") or None
    profile_data = db_read_all(username)
    entry = profile_data.get(model_type, {}).get(key, {})
    current = entry.get("value", 0) or 0
    if model_type == MODEL_REGEN and kd and isinstance(kd, RegenKeyDef):
        if _is_continuous_regen(kd):
            current = compute_realtime_value(
                entry.get("value", 0) or 0,
                entry.get("updated_at", ""),
                kd,
            )
        else:
            current = compute_regen_entry(entry, kd).value
    return current


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
    与 UI 读取路径共用 _read_current_value，保证行为一致。
    """
    config = get_profile_config()
    model_type = config.get_model_type(key)
    if model_type is None:
        return None
    entry = db_read_entry(username, model_type, key)
    if not entry:
        return None
    kd = config.get_key(key)
    return _read_current_value(username, model_type, key, kd)


def profile_read_all(username: str) -> dict:
    """读取全部 profile 数据（regen 自动实时计算）

    返回 {model_type: {key: {value, updated_at, ...}}} 结构。
    regen 条目的 value 已按当前时间计算。
    """
    data = db_read_all(username)
    config = get_profile_config()
    for kd in config.get_keys_by_model(MODEL_REGEN):
        if not isinstance(kd, RegenKeyDef):
            continue
        entry = data.get(MODEL_REGEN, {}).get(kd.key)
        if entry:
            entry["value"] = compute_regen_entry(entry, kd).value
    return data


# ─── 共享写入管线 ─────────────────────────────────────────────


def profile_action(
    username: str,
    key: str,
    *,
    model_type: str | None = None,
    delta: float | None = None,
    set_value: float | str | None = None,
    source: str = tr("DSL 写入"),
    current_value: float | None = None,
    expected_entry: dict | None = None,
    is_action: bool = True,
    use_cas: bool = False,
    regen_progress_source: str = "target",
    force_write: bool = False,
) -> float | str:
    """统一 profile 写入入口

    数值模型（quota/regen/stock）：
    读当前值 → 计算新值 → clamp → 算 actual_delta → detail → db_upsert → sync_targets

    note 模型：
    直接写入 value_text 列，不走数值管线，不触发同步。

    Parameters
    ----------
    username:
        角色名。
    key:
        profile key（自动识别模型类型）。
    delta:
        增减量（与 set_value 二选一）。
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

    # ── note 短路：文本直接写入，不走数值管线 ──
    if model_type == MODEL_NOTE:
        if kd is None:
            logger.warning(f"profile_action: note key '{key}' 未在 profile.yaml 中定义")
            return ""
        text = str(set_value) if set_value is not None else (str(delta) if delta is not None else "")
        db_upsert(username, model_type, key, 0, value_text=text, source=source)
        return text

    # ── 1. 读当前值 ──
    if current_value is None:
        current_value = _read_current_value(username, model_type, key, kd)

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

    # ── 5. detail ──
    detail = f"delta:{actual_delta:+g}" if is_action else f"override:{new_value}"

    # ── 6. regen 连续恢复规范化 ──
    custom_updated_at = None
    write_value = new_value  # 默认：语义值 = 入库值
    if model_type == MODEL_REGEN and kd and _is_continuous_regen(kd):
        assert isinstance(kd, RegenKeyDef)  # mypy narrowing
        progress_value = new_value if regen_progress_source == "target" else current_value
        write_value, custom_updated_at = _normalize_continuous_regen_write(
            kd, progress_value
        )
        new_value = float(math.floor(new_value))
        cap = kd.cap
        if cap is not None and new_value >= cap:
            new_value = float(cap)
            semantic_new_value = float(cap)
            actual_delta = _normalize_float_noise(semantic_new_value - current_value)
            write_value = float(cap)
            custom_updated_at = datetime.now().isoformat(timespec="seconds")
        elif regen_progress_source == "current":
            write_value = new_value

    # ── 7. 写入 DB ──
    change_type = "action" if is_action else "override"
    if (
        model_type == MODEL_REGEN
        and is_action
        and use_cas
        and expected_entry is not None
    ):
        updated = db_update_if_current(
            username, model_type, key,
            expected_value=expected_entry.get("value", 0) or 0,
            expected_updated_at=expected_entry.get("updated_at", ""),
            new_value=write_value,
            new_updated_at=custom_updated_at,
            change_type=change_type,
            detail=detail,
            source=source,
        )
        if not updated:
            raise ProfileWriteConflict(f"{username} {model_type}.{key} CAS failed")
    else:
        db_upsert(
            username, model_type, key, write_value,
            updated_at=custom_updated_at,
            change_type=change_type,
            detail=detail,
            source=source,
        )
    logger.debug(
        f"profile_action: {username}.{model_type}.{key} = "
        f"{semantic_new_value} ({detail})"
    )

    # ── 8. 触发器同步 ──
    if is_action and kd and kd.sync_targets:
        from .sync_engine import fire_sync_targets
        fire_sync_targets(
            write_fn=sync_write_adapter,
            user_name=username,
            source_kd=kd,
            delta=actual_delta,
            source=source,
        )

    return semantic_new_value


# ─── sync_engine 独立写入适配器 ──────────────────────────────


def sync_write_adapter(
    user_name: str,
    model_type: str,
    key: str,
    *,
    delta: int | float,
    change_type: str = "action",
    detail: str = "",
    source: str = "",
) -> tuple[int | float, int | float] | None:
    """同步引擎的写入适配器：读当前值 → 加 delta → clamp → 写入

    返回 (new_value, applied_delta)；写入失败返回 None，
    引擎据此中止该目标的下游递归。供 fire_sync_targets 注入使用。
    """
    config = get_profile_config()
    kd = config.get_key(key, model_type=model_type)

    current_value = _read_current_value(user_name, model_type, key, kd)
    new_value = _normalize_float_noise(_clamp(current_value + delta, model_type, kd))
    semantic_new_value = new_value
    actual_delta = _normalize_float_noise(semantic_new_value - current_value)

    # clamp 后值未变 → 不产生写入
    if new_value == current_value:
        return current_value, 0

    custom_updated_at = None
    if model_type == MODEL_REGEN and kd and _is_continuous_regen(kd):
        assert isinstance(kd, RegenKeyDef)  # mypy narrowing
        progress_value = semantic_new_value  # 保留 floor 前的小数进度，用于回拨 updated_at
        new_value = float(math.floor(semantic_new_value))
        _, custom_updated_at = _normalize_continuous_regen_write(kd, progress_value)
        cap = kd.cap
        if cap is not None and new_value >= cap:
            new_value = float(cap)
            semantic_new_value = float(cap)
            actual_delta = _normalize_float_noise(semantic_new_value - current_value)
            custom_updated_at = datetime.now().isoformat(timespec="seconds")

    try:
        db_upsert(
            user_name, model_type, key, new_value,
            updated_at=custom_updated_at,
            change_type=change_type,
            detail=detail,
            source=source,
        )
    except Exception as e:
        logger.error(f"sync_write_adapter failed: {user_name}.{model_type}.{key}: {e}")
        return None

    return semantic_new_value, actual_delta
