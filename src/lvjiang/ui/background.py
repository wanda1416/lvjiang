"""桌面后台服务的 Qt 线程适配器；业务服务位于 core。"""
from PyQt6.QtCore import QThread, pyqtSignal

from ..core.telemetry import reporter
from ..core.updates.release import UpdateCheckError, fetch_latest_release


class TelemetryReporter(QThread):
    """匿名上报服务的 Qt 线程适配；结果回到主线程记账。"""
    finished_ok = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, job: reporter.ReportJob, parent=None):
        super().__init__(parent)
        self._job = job

    def run(self):
        try:
            outcome = reporter.run_report(self._job)
        except Exception as error:
            self.failed.emit(str(error))
            return
        self.finished_ok.emit(outcome)


class UpdateChecker(QThread):
    """更新检查服务的 Qt 线程适配。"""
    finished = pyqtSignal(object)
    error = pyqtSignal(str)

    def run(self):
        try:
            release = fetch_latest_release()
        except UpdateCheckError as error:
            self.error.emit(str(error))
            return
        self.finished.emit(release)
