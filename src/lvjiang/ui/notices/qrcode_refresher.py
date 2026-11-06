"""桌面二维码刷新线程。"""
from collections.abc import Callable

from loguru import logger
from PyQt6.QtCore import QThread, pyqtSignal

from ...core import group_qrcode


class QrCodeRefresher(QThread):
    """后台下载并保存最新交流群二维码。"""

    # 不能叫 finished：那会遮蔽 QThread.finished，而后者是「线程已真正退出」
    # 唯一的可靠信号。本类的成功信号在 run() 里 emit，那一刻线程还没退；
    # 把 deleteLater 挂在它上，主线程就可能先处理延迟删除——销毁一个仍在
    # 运行的 QThread 会直接让进程崩溃（满载并发下这个竞态稳定复现）。
    succeeded = pyqtSignal(bytes)  # 下载并写盘成功后的图片二进制
    error = pyqtSignal(str)

    def run(self):
        try:
            data = group_qrcode.fetch_qrcode_bytes()
            group_qrcode.save_qrcode(data)
        except group_qrcode.QrCodeError as exc:
            logger.warning(f"[二维码] 刷新失败: {exc}")
            self.error.emit(str(exc))
        except Exception as exc:  # noqa: BLE001 - 线程边界统一转成信号
            logger.exception("[二维码] 刷新失败")
            self.error.emit(f"刷新二维码失败: {exc}")
        else:
            self.succeeded.emit(data)


class ImageRefreshThread(QThread):
    """后台执行一次取图动作，成功与失败都带上 key。

    赞助对话框一次要管微信、支付宝两张图，结果必须能对上是哪一张；把 key
    随信号发出，对话框就不用为每个渠道各写一个 QThread 子类。
    """

    # 同 QrCodeRefresher：成功信号不能叫 finished，见上面的注释。
    succeeded = pyqtSignal(str, bytes)  # key, 图片二进制
    error = pyqtSignal(str, str)        # key, 可读错误信息

    def __init__(self, key: str, loader: Callable[[], bytes], parent=None):
        super().__init__(parent)
        self.key = key
        self._loader = loader

    def run(self):
        try:
            data = self._loader()
        except Exception as exc:  # noqa: BLE001 - 线程边界统一转成信号
            logger.warning(f"[二维码] {self.key} 获取失败: {exc}")
            self.error.emit(self.key, str(exc))
        else:
            self.succeeded.emit(self.key, data)
