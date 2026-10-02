"""二维码控件：用 QPainter 直接画 segno 的矩阵。

不经 PNG/PIL：矩阵本来就是 0/1 的点阵，自己画既省一个图像依赖，也能跟着控件
尺寸和主题重绘——二维码在深色主题下必须保持"深色模块 + 浅色底"，反过来多数
手机扫不出来，所以这里不跟随主题反色，只固定黑白并留白边。
"""
from __future__ import annotations

from PyQt6.QtCore import QRect, QSize, Qt
from PyQt6.QtGui import QColor, QPainter, QPaintEvent
from PyQt6.QtWidgets import QWidget

#: 二维码四周必须留的静默区（模块数）。少于 4 个模块很多扫码器识别率骤降。
_QUIET_ZONE = 4


class QrCodeWidget(QWidget):
    """按内容渲染二维码；内容为空时留白。"""

    def __init__(self, parent: QWidget | None = None, *, min_size: int = 180):
        super().__init__(parent)
        self._matrix: list[list[int]] = []
        self._content = ""
        self._min_size = min_size
        self.setMinimumSize(QSize(min_size, min_size))

    @property
    def content(self) -> str:
        """当前编码的内容。空串表示没有可扫的东西。"""
        return self._content

    def set_content(self, content: str) -> bool:
        """设置二维码内容，返回是否成功编码。"""
        self._content = content or ""
        self._matrix = []
        if content:
            try:
                import segno

                code = segno.make(content, error="m")
                self._matrix = [list(row) for row in code.matrix]
            except Exception:  # noqa: BLE001 — 编码失败不该让整个对话框起不来
                from loguru import logger

                logger.exception("二维码编码失败")
                self._matrix = []
        self.update()
        return bool(self._matrix)

    def sizeHint(self) -> QSize:
        return QSize(self._min_size, self._min_size)

    def paintEvent(self, event: QPaintEvent | None) -> None:
        painter = QPainter(self)
        try:
            # 固定白底黑码：扫码器对反色的容忍度远低于对留白的容忍度
            painter.fillRect(self.rect(), QColor("#ffffff"))
            if not self._matrix:
                return
            modules = len(self._matrix) + _QUIET_ZONE * 2
            side = min(self.width(), self.height())
            scale = max(1, side // modules)
            drawn = scale * modules
            left = (self.width() - drawn) // 2
            top = (self.height() - drawn) // 2
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#000000"))
            for row, values in enumerate(self._matrix):
                for col, value in enumerate(values):
                    if not value:
                        continue
                    painter.drawRect(QRect(
                        left + (col + _QUIET_ZONE) * scale,
                        top + (row + _QUIET_ZONE) * scale,
                        scale, scale))
        finally:
            painter.end()
