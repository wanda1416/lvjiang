"""可扩展的持久化统计数据源注册表。"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Protocol

from loguru import logger


@dataclass(frozen=True)
class SourceBatch:
    """待发送批次；receipts 只留在本地，不进入网络信封。"""

    source_name: str
    batch_id: str
    events: tuple[dict, ...]
    receipts: tuple[int | str, ...]


class TelemetrySource(Protocol):
    name: str

    def collect(self, limit: int) -> tuple[SourceBatch, ...]: ...

    def mark_reported(self, receipts: tuple[int | str, ...]) -> None: ...


_SOURCES: dict[str, TelemetrySource] = {}


def register_source(source: TelemetrySource) -> None:
    existing = _SOURCES.get(source.name)
    if existing is not None and existing is not source:
        raise ValueError(f"统计数据源重复注册: {source.name!r}")
    _SOURCES[source.name] = source


def collect_batches(limit_per_source: int = 200) -> tuple[SourceBatch, ...]:
    """逐个数据源取批次。

    **这个函数目前跑在 Qt 主线程上**（``reporter.build_job()`` → 启动期与设置页
    勾选遥测开关）。数据源可能读数据库，最坏情况会阻塞界面，因此逐源计时并打
    日志：先攒实测数据，再决定要不要把这一段拆到独立线程。
    """
    batches: list[SourceBatch] = []
    for source in tuple(_SOURCES.values()):
        started = time.perf_counter()
        try:
            collected = tuple(source.collect(limit_per_source))
        except Exception as exc:  # noqa: BLE001
            # 持久化数据源属于可选统计能力，数据库损坏或迁移失败不能阻止启动。
            logger.warning(
                f"[telemetry] 数据源 {source.name} 读取失败"
                f"（耗时 {time.perf_counter() - started:.3f}s）: {exc}")
            continue
        batches.extend(collected)
        events = sum(len(batch.events) for batch in collected)
        logger.info(
            f"[telemetry] 数据源 {source.name} 采集完成: "
            f"{len(collected)} 批 / {events} 条，"
            f"耗时 {time.perf_counter() - started:.3f}s")
    return tuple(batches)


def mark_reported(batch: SourceBatch) -> None:
    source = _SOURCES.get(batch.source_name)
    if source is not None:
        try:
            source.mark_reported(batch.receipts)
        except Exception as exc:  # noqa: BLE001
            # 服务端已经接收成功；本地未能落 reported_at 时，下次仍以稳定
            # batch_id 重试，由服务端幂等去重。
            logger.warning(f"[telemetry] 数据源 {source.name} 回写失败: {exc}")


def reset_sources() -> None:
    _SOURCES.clear()
