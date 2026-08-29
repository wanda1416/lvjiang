"""触发器同步引擎

将 KeyDef 的 sync_targets 配置转化为实际的跨模型写入。
支持多目标、独立倍率（负数 / 小数）、跨模型命名空间、递归链式同步、防环。

fire_sync_targets 通过注入 write_fn 避免直接依赖 UI 层（overview），
write_fn 语义：读取当前值 → 加 delta → clamp → 写入 → 返回 (new_value, applied_delta)，
写入失败时返回 None（引擎中止该目标的下游递归）。
"""

from __future__ import annotations

from typing import Callable

from loguru import logger

from .models import DIR_NEG, DIR_POS, KeyDef, parse_sync_key
from .schema import get_profile_config

# write_fn 签名：(user_name, model_type, key, delta, change_type, detail, source)
# -> (new_value, applied_delta)；写入失败返回 None
SyncWriteFn = Callable[..., tuple[int | float, int | float] | None]


def fire_sync_targets(
    write_fn: SyncWriteFn,
    user_name: str,
    source_kd: KeyDef,
    delta: int | float,
    source: str,
    visited: frozenset[tuple[str, str]] = frozenset(),
) -> None:
    """触发 source_kd 的所有 sync_targets，递归处理链式同步

    Parameters
    ----------
    write_fn:
        注入的写入函数，签名为
        ``(user_name, model_type, key, *, delta, change_type, detail, source)
        -> (new_value, applied_delta) | None``。
        负责读取当前值、加 delta、clamp、写入；applied_delta 是 clamp 后的实际生效量，
        失败时返回 None。
    user_name:
        用户名。
    source_kd:
        触发同步的源 KeyDef。
    delta:
        源字段的实际变更量（已 clamp）。
    source:
        默认来源描述，可被 SyncTargetDef.source 覆盖。
    visited:
        防环集合，按 (model_type, key) 去重。
    """
    config = get_profile_config()
    # 将源节点加入 visited，防止目标反向同步回源（A→B→A）
    source_id = (config.get_model_type(source_kd.key) or "", source_kd.key)
    visited = visited | {source_id}

    for target in source_kd.sync_targets:
        # ratio=0 显式禁用该目标
        if target.ratio == 0:
            continue

        # 方向限定：仅当源变动方向匹配时触发
        if target.direction == DIR_POS and delta <= 0:
            continue
        if target.direction == DIR_NEG and delta >= 0:
            continue

        model_type, key = parse_sync_key(target.key)
        target_kd = config.get_key(key, model_type=model_type or None)
        if target_kd is None:
            logger.warning(f"sync target {target.key} not found, skip")
            continue

        # 获取目标的实际模型类型（KeyDef 不存储 model_type）
        target_model = config.get_model_type(target_kd.key) or model_type

        # 防环
        target_id = (target_model, target_kd.key)
        if target_id in visited:
            logger.warning(f"sync cycle detected at {target.key}, skip")
            continue

        # 倍率计算；取整后为 0 跳过无意义写入
        scaled_delta = _apply_ratio(delta, target.ratio, target_model, target_kd)
        if scaled_delta == 0:
            continue

        # 写入目标
        effective_source = target.source or source
        result = write_fn(
            user_name, target_model, target_kd.key,
            delta=scaled_delta, change_type="action",
            detail=f"sync_from:{source_kd.key}",
            source=effective_source,
        )
        if result is None:
            logger.error(f"sync write failed: {target_model}.{target_kd.key}")
            continue  # 写入失败，中止该目标的下游递归

        new_value, applied_delta = result

        # 获取源的模型类型用于日志
        source_model = config.get_model_type(source_kd.key) or ""
        logger.debug(
            f"[sync] {source_model}.{source_kd.key} → "
            f"{target_model}.{target_kd.key} "
            f"x{target.ratio} = {applied_delta:+g} (new={new_value})"
        )

        # 递归：用目标实际生效增量（clamp 后）驱动下游，避免数据漂移
        if target_kd.sync_targets and applied_delta != 0:
            fire_sync_targets(
                write_fn, user_name, target_kd, applied_delta,
                source=effective_source,
                visited=visited | {target_id},
            )


def _apply_ratio(
    delta: int | float, ratio: float, target_model: str, target_kd: KeyDef | None = None,
) -> int | float:
    """按目标字段配置决定返回类型：decimal 保留小数，其余 round。"""
    if getattr(target_kd, "decimal", False):
        return delta * ratio
    return round(delta * ratio)
