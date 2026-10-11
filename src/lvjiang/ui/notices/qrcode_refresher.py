"""桌面二维码刷新线程。"""
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
