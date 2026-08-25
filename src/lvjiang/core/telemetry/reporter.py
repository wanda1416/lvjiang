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

from dataclasses import dataclass

from PyQt6.QtCore import QThread, pyqtSignal

from . import spool as spool_mod
from .spool import SpoolChunk
from .transport import post_report

MAX_BATCHES_PER_STARTUP = 4


@dataclass(frozen=True)
class ReportJob:
    heartbeat: dict | None  # None 表示今天已经发过心跳，本次只发调律批次
    batches: tuple[SpoolChunk, ...]

    @property
    def is_empty(self) -> bool:
        return self.heartbeat is None and not self.batches


@dataclass(frozen=True)
class ReportOutcome:
    heartbeat_attempted: bool
    heartbeat_ok: bool
    sent_batches: tuple[SpoolChunk, ...]  # 已成功上报，主线程据此 drop


def build_job() -> ReportJob:
    """在主线程调用：读取是否需要发心跳 + 取出待发批次。

    未同意/被离线模式覆盖时直接返回空 job，**不触碰 identity**——
    否则即便用户从未同意，这里也会静默生成一份本地标识，等于开关
    形同虚设。
    """
    from .consent import NetFeature, is_network_feature_enabled

    if not is_network_feature_enabled(NetFeature.TELEMETRY):
        return ReportJob(heartbeat=None, batches=())

    from . import heartbeat as heartbeat_mod
    from . import identity as identity_mod

    identity = identity_mod.get_identity()
    hb = None
    if heartbeat_mod.should_send_heartbeat():
        hb = heartbeat_mod.build_heartbeat_payload(
            install_id=identity.install_id, first_seen=identity.first_seen)
    batches = tuple(spool_mod.take_batches(MAX_BATCHES_PER_STARTUP))
    return ReportJob(heartbeat=hb, batches=batches)


def _build_envelope(job: ReportJob) -> dict:
    from datetime import datetime, timezone
    return {
        "v": 1,
        "sent_at": datetime.now(timezone.utc).isoformat(),
        "heartbeat": job.heartbeat,
        "batches": [
            {"batch_id": chunk.path.stem, "events": list(chunk.events)}
            for chunk in job.batches
        ],
    }


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
                              sent_batches=()))
            return
        envelope = _build_envelope(job)
        try:
            ok = post_report(envelope)
        except Exception as e:  # noqa: BLE001 —— 见模块 docstring：探针出任何意外都不能外抛
            self.failed.emit(str(e))
            return
        sent = job.batches if ok else ()
        self.finished_ok.emit(
            ReportOutcome(
                heartbeat_attempted=job.heartbeat is not None,
                heartbeat_ok=ok and job.heartbeat is not None,
                sent_batches=sent,
            ))


def apply_outcome(outcome: ReportOutcome) -> None:
    """必须在主线程调用：写节流状态 + 删除已成功上报的批次文件。"""
    from . import heartbeat as heartbeat_mod

    if outcome.heartbeat_attempted:
        heartbeat_mod.mark_attempt(success=outcome.heartbeat_ok)
    for chunk in outcome.sent_batches:
        spool_mod.drop(chunk)
