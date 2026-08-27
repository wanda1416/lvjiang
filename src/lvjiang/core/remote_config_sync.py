"""在线配置同步的后台线程包装。

同步逻辑本身在 `core/config/remote.py`，那个模块刻意保持 **Qt-free**
（整个 `core.config` 包都不 import PyQt6，脱 GUI 的脚本与单测才能直接用），
所以 QThread 包装放在这里——位置与 `core/announcement.py` 的
`AnnouncementChecker` 对齐。

三段式拆分，逐条对应 `core/telemetry/reporter.py` 那三条硬约束：

1. **入参在主线程构造**：``build_sync_job()`` 读 SessionStore 与
   ``get_version()``，作为不可变的 ``SyncJob`` 传进构造函数，不在 worker
   里首次触发这些模块的懒加载。
2. **worker 内绝对不写 SessionStore**：``main_window`` 给 SessionStore 注册
   了 UI 回调，写锁超时会从调用线程弹 QMessageBox，非主线程弹原生模态框是
   未定义行为。etag / config_version 的写回由主线程在 ``finished`` 槽里经
   ``apply_outcome()`` 完成。
3. **不阻塞启动**：失败只记日志、发 error 信号。拿不到在线配置的后果是
   "用出厂配置"，本来就是可用状态，绝不该因此卡住或弹窗打断用户。
"""
from __future__ import annotations

from loguru import logger
from PyQt6.QtCore import QThread, pyqtSignal

from .config.remote import SyncJob, SyncResult, build_sync_job, run_sync


class RemoteConfigSyncer(QThread):
    """后台拉取并落盘在线配置。

    ``job`` 必须在主线程构造（默认参数即在构造函数里调 ``build_sync_job()``，
    而 QThread 对象本身就该在主线程 new 出来，故默认值是安全的）。
    """

    finished_ok = pyqtSignal(object)  # SyncResult —— 主线程槽里 apply_outcome
    failed = pyqtSignal(str)

    def __init__(self, parent=None, *, job: SyncJob | None = None,
                 timeout: float = 10.0):
        super().__init__(parent)
        self._job = job if job is not None else build_sync_job()
        self._timeout = timeout

    def run(self):
        try:
            result: SyncResult = run_sync(self._job, timeout=self._timeout)
        except Exception as exc:  # noqa: BLE001 线程边界统一转成信号
            # 拿不到在线配置 = 用出厂配置，是可用状态，不打扰用户
            logger.warning(f"[在线配置] 同步失败: {exc}")
            self.failed.emit(str(exc))
            return
        self.finished_ok.emit(result)
