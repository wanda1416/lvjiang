"""更新检查服务的 Qt 线程适配。"""
from PyQt6.QtCore import QThread, pyqtSignal

from ...core.update import UpdateCheckError, fetch_latest_release


class UpdateChecker(QThread):
    finished = pyqtSignal(object)
    error = pyqtSignal(str)

    def run(self):
        try:
            release = fetch_latest_release()
        except UpdateCheckError as error:
            self.error.emit(str(error))
            return
        self.finished.emit(release)
