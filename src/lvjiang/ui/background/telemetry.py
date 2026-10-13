"""匿名上报服务的 Qt 线程适配；结果回到主线程记账。"""
from PyQt6.QtCore import QThread, pyqtSignal

from ...core.telemetry import reporter


class TelemetryReporter(QThread):
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
