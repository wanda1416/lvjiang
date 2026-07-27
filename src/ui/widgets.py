"""可复用 UI 控件"""

from PyQt6.QtGui import QTextCursor
from PyQt6.QtWidgets import QComboBox, QDoubleSpinBox, QSpinBox, QTextEdit

_MAX_LOG_LINES = 1000


class NoWheelSpinBox(QSpinBox):
    """禁用滚轮的整数输入框（避免滑动页面时误改数值）"""

    def wheelEvent(self, event):
        event.ignore()


class NoWheelDoubleSpinBox(QDoubleSpinBox):
    """禁用滚轮的浮点数输入框（避免滑动页面时误改数值）"""

    def wheelEvent(self, event):
        event.ignore()


class NoWheelComboBox(QComboBox):
    """禁用滚轮的下拉框（避免滑动页面时误改选项）"""

    def wheelEvent(self, event):
        event.ignore()


class TrimmedLogEdit(QTextEdit):
    """自动限制最大行数的只读日志文本框

    超过 _MAX_LOG_LINES 行时，自动裁剪掉前 1/4 的旧行，
    避免长时间运行时内存无限增长。
    """

    def __init__(self, max_lines: int = _MAX_LOG_LINES):
        super().__init__()
        self._max_lines = max_lines
        self.setReadOnly(True)

    def append(self, text: str):
        super().append(text)
        doc = self.document()
        if doc.blockCount() > self._max_lines:
            cursor = self.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.Start)
            # 一次裁剪到上限以下，避免频繁触发
            trim_count = doc.blockCount() - self._max_lines + (self._max_lines // 4)
            for _ in range(trim_count):
                cursor.movePosition(QTextCursor.MoveOperation.Down, QTextCursor.MoveMode.KeepAnchor)
            cursor.removeSelectedText()
            cursor.deleteChar()  # 删除残留换行
