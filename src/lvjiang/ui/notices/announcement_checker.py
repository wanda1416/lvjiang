"""桌面公告异步适配器；手机直接复用 Qt 无关的公告服务。"""
from loguru import logger
from PyQt6.QtCore import QThread, pyqtSignal

from ...core import announcement
from ...i18n import tr


class AnnouncementChecker(QThread):
    """后台获取 GitHub Pages 公告。"""

    finished = pyqtSignal(object)  # AnnouncementFetchResult
    error = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._cached = announcement.load_cached_manifest()
        self._etag = announcement.get_cached_etag()

    def run(self):
        from ...core.telemetry.consent import is_offline_mode
        if is_offline_mode():
            self.error.emit(tr("当前为离线模式，请关闭离线模式后刷新公告"))
            return
        try:
            self.finished.emit(announcement.fetch_announcement_manifest(
                etag=self._etag,
                cached=self._cached,
            ))
        except announcement.AnnouncementNetworkError as exc:
            # 常见网络故障只输出一行可操作说明，不泄漏 urllib/SSL 调用栈。
            logger.warning(str(exc))
            self.error.emit(str(exc))
        except Exception as exc:  # noqa: BLE001 - 线程边界统一转成信号
            logger.warning(f"[公告] 获取失败: {exc}")
            self.error.emit(tr("获取公告失败: {error}").format(error=exc))
