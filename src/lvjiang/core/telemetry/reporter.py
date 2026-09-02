"""通道 A/B 共用的启动期上报编排：TelemetryReporter(QThread)。

三条硬约束（有真坑，逐条抄自现有 AnnouncementChecker 的先例）：

1. **payload 在主线程构造**，作为不可变对象传进线程构造函数。构造 payload
   要读 UserConfig/load_env()/i18n 等，不该在 worker 线程里首次触发懒加载。
2. **worker 线程内绝对不能写 SessionStore**。``main_window.py`` 给
   SessionStore 注册了 UI 回调，写锁超时会从调用线程弹 QMessageBox——
   非主线程弹原生模态框是未定义行为。节流字段的更新必须放在主线程的
   ``finished`` 槽里做。
3. **响应体完全忽略**（见 transport.py），本模块不做任何基于响应内容的
   状态迁移。
"""
from __future__ import annotations

import json
import time
from collections.abc import Sequence
from dataclasses import dataclass

from loguru import logger
from PyQt6.QtCore import QThread, pyqtSignal

from . import spool as spool_mod
from .sources import SourceBatch
from .spool import SpoolChunk
from .transport import post_report

MAX_BATCHES_PER_STARTUP = 4

# 单个 HTTP 请求的信封体积上限。服务端 MAX_BODY_BYTES 是 256KB，这里留一倍
# 余量：一条 per-session 事件约 1KB，50 条一批就是 ~50KB，遇到调满+重置的
# 长会话还会更大，四批塞进一个请求会顶穿。
#
# 顶穿的解法是**分多次请求**，不是缩小批次：批次大小同时决定 D1 的行数
# （一批 = roll_batch 一行），缩批次会让行数成倍上升，正好加重我们要解决的
# 额度问题。批次是存储粒度，请求是传输粒度，两者不该耦合。
MAX_ENVELOPE_BYTES = 128 * 1024


@dataclass(frozen=True)
class ReportJob:
    heartbeat: dict | None  # None 表示今天已经发过心跳，本次只发调律批次
    batches: tuple[SpoolChunk, ...]
    source_batches: tuple[SourceBatch, ...] = ()

    @property
    def is_empty(self) -> bool:
        return (self.heartbeat is None and not self.batches
                and not self.source_batches)


@dataclass(frozen=True)
class ReportOutcome:
    heartbeat_attempted: bool
    heartbeat_ok: bool
    sent_batches: tuple[SpoolChunk, ...]  # 已成功上报，主线程据此 drop
    sent_source_batches: tuple[SourceBatch, ...] = ()


def build_job() -> ReportJob:
    """在主线程调用：读取是否需要发心跳 + 取出待发批次。

    未同意/被离线模式覆盖时直接返回空 job，**不触碰 identity**——
    否则即便用户从未同意，这里也会静默生成一份本地标识，等于开关
    形同虚设。
    """
    from .consent import NetFeature, is_network_feature_enabled

    if not is_network_feature_enabled(NetFeature.TELEMETRY):
        return ReportJob(heartbeat=None, batches=(), source_batches=())

    from . import heartbeat as heartbeat_mod
    from . import identity as identity_mod

    identity = identity_mod.get_identity()
    hb = None
    if heartbeat_mod.should_send_heartbeat():
        hb = heartbeat_mod.build_heartbeat_payload(
            install_id=identity.install_id, first_seen=identity.first_seen)
    batches = tuple(spool_mod.take_batches(MAX_BATCHES_PER_STARTUP))
    # collect_batches 会读持久化数据源（当前是调律历史 SQLite），而本函数按
    # 上面的约束跑在主线程上，最坏情况会卡住界面。埋点先量出真实耗时，够不够
    # 格拆线程等实测数据说话。
    started = time.perf_counter()
    from .sources import collect_batches
    source_batches = collect_batches()
    elapsed = time.perf_counter() - started
    log = logger.warning if elapsed >= 0.5 else logger.info
    log(f"[telemetry] build_job 采集持久化数据源耗时 {elapsed:.3f}s"
        f"（主线程，{len(source_batches)} 批）")
    return ReportJob(
        heartbeat=hb, batches=batches, source_batches=source_batches)


def _envelope(heartbeat: dict | None,
              chunks: Sequence[SpoolChunk | SourceBatch]) -> dict:
    from datetime import datetime, timezone

    from .heartbeat import normalized_app_version

    return {
        "v": 1,
        "sent_at": datetime.now(timezone.utc).isoformat(),
        # 每个信封都带版本号，不依赖心跳：心跳每 UTC 日只发一次，而
        # roll_batch.app_version 是事后剔除坏版本数据的唯一抓手，缺了它
        # 同一天后续批次全部无法归因到具体版本。
        "app_version": normalized_app_version(),
        "heartbeat": heartbeat,
        "batches": [
            {"batch_id": (chunk.path.stem if isinstance(chunk, SpoolChunk)
                          else chunk.batch_id),
             "events": list(chunk.events)}
            for chunk in chunks
        ],
    }


def _envelope_bytes(envelope: dict) -> int:
    return len(json.dumps(envelope, ensure_ascii=False).encode("utf-8"))


def plan_requests(job: ReportJob) -> list[
    tuple[dict | None, tuple[SpoolChunk | SourceBatch, ...]]
]:
    """把一次上报拆成若干个不超过 ``MAX_ENVELOPE_BYTES`` 的请求。

    心跳挂在第一个请求上；批次按体积贪心装箱。单个批次自己就超限时仍然
    单独成一个请求——发出去让服务端按它自己的上限判定，总好过在客户端
    静默丢弃（丢了就再也没有了，而服务端拒收只是这一批不落地）。
    """
    heartbeat = job.heartbeat
    plans: list[
        tuple[dict | None, tuple[SpoolChunk | SourceBatch, ...]]
    ] = []
    current: list[SpoolChunk | SourceBatch] = []
    chunks: tuple[SpoolChunk | SourceBatch, ...] = (
        *job.batches, *job.source_batches)
    for chunk in chunks:
        candidate = current + [chunk]
        if current and _envelope_bytes(_envelope(heartbeat, candidate)) > MAX_ENVELOPE_BYTES:
            plans.append((heartbeat, tuple(current)))
            heartbeat = None          # 心跳只发一次
            current = [chunk]
        else:
            current = candidate
    if current or heartbeat is not None:
        plans.append((heartbeat, tuple(current)))
    return plans


class TelemetryReporter(QThread):
    """一次启动期上报。只做 HTTP，不碰 SessionStore。"""

    finished_ok = pyqtSignal(object)  # ReportOutcome
    failed = pyqtSignal(str)

    def __init__(self, job: ReportJob, parent=None):
        super().__init__(parent)
        self._job = job

    def run(self) -> None:
        job = self._job
        if job.is_empty:
            self.finished_ok.emit(
                ReportOutcome(heartbeat_attempted=False, heartbeat_ok=False,
                              sent_batches=(), sent_source_batches=()))
            return
        sent: list[SpoolChunk] = []
        sent_sources: list[SourceBatch] = []
        heartbeat_ok = False
        try:
            for heartbeat, chunks in plan_requests(job):
                ok = post_report(_envelope(heartbeat, chunks))
                if not ok:
                    # 中途失败就停：剩余批次留在本地，下次启动重发。
                    # 已成功的部分照常记账，不因后续失败被重复上报。
                    break
                if heartbeat is not None:
                    heartbeat_ok = True
                sent.extend(c for c in chunks if isinstance(c, SpoolChunk))
                sent_sources.extend(
                    c for c in chunks if isinstance(c, SourceBatch))
        except Exception as e:  # noqa: BLE001 —— 见模块 docstring：探针出任何意外都不能外抛
            self.failed.emit(str(e))
            return
        self.finished_ok.emit(
            ReportOutcome(
                heartbeat_attempted=job.heartbeat is not None,
                heartbeat_ok=heartbeat_ok,
                sent_batches=tuple(sent),
                sent_source_batches=tuple(sent_sources),
            ))


def apply_outcome(outcome: ReportOutcome) -> None:
    """必须在主线程调用：写节流状态 + 删除已成功上报的批次文件。"""
    from . import heartbeat as heartbeat_mod

    if outcome.heartbeat_attempted:
        heartbeat_mod.mark_attempt(success=outcome.heartbeat_ok)
    for chunk in outcome.sent_batches:
        spool_mod.drop(chunk)
    from .sources import mark_reported
    for batch in outcome.sent_source_batches:
        mark_reported(batch)
