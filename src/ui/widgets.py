"""可复用 UI 控件"""

from PyQt6.QtCore import QRect, QSize
from PyQt6.QtGui import QTextCursor
from PyQt6.QtWidgets import QComboBox, QDoubleSpinBox, QLayout, QSpinBox, QTextEdit

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


class FlowLayout(QLayout):
    """自动换行的流式布局"""

    def __init__(self, parent=None, spacing=4):
        super().__init__(parent)
        self._items = []
        self._spacing = spacing

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, index):
        if 0 <= index < len(self._items):
            return self._items[index]
        return None

    def takeAt(self, index):
        if 0 <= index < len(self._items):
            return self._items.pop(index)
        return None

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._do_layout(QRect(0, 0, width, 0), test_only=True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._do_layout(rect, test_only=False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        return size

    def _do_layout(self, rect, test_only=False):
        x = rect.x()
        y = rect.y()
        line_height = 0

        for item in self._items:
            wid = item.widget()
            if wid:
                space = self._spacing
                item_size = wid.sizeHint()
            else:
                continue

            next_x = x + item_size.width() + space
            if next_x - space > rect.right() and line_height > 0:
                x = rect.x()
                y = y + line_height + space
                next_x = x + item_size.width() + space
                line_height = 0

            if not test_only:
                item.setGeometry(QRect(x, y, item_size.width(), item_size.height()))

            x = next_x
            line_height = max(line_height, item_size.height())

        return y + line_height - rect.y()
