"""可复用 UI 控件"""

from PyQt6.QtCore import QEvent, QObject, QPointF, QRect, QSize
from PyQt6.QtGui import QTextCursor, QWheelEvent
from PyQt6.QtWidgets import (
    QAbstractSpinBox,
    QApplication,
    QComboBox,
    QLayout,
    QStyle,
    QStyledItemDelegate,
    QTextEdit,
)

_MAX_LOG_LINES = 1000


class WheelGuard(QObject):
    """应用级滚轮拦截器：下拉框/数字输入框一律屏蔽滚轮改值

    装到 QApplication 上，全局生效（含后续新增控件，无需逐个
    定制控件类）；滚轮事件交回父级滚动区域，页面滚动不受影响。
    """

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if (event.type() == QEvent.Type.Wheel
                and isinstance(obj, (QComboBox, QAbstractSpinBox))):
            # 克隆后沿父链上抛至被接收：页面照常滚动，控件值不变
            parent = obj.parentWidget()
            while parent is not None:
                pos = parent.mapFromGlobal(
                    event.globalPosition().toPoint())
                clone = QWheelEvent(
                    QPointF(pos), event.globalPosition(),
                    event.pixelDelta(), event.angleDelta(),
                    event.buttons(), event.modifiers(),
                    event.phase(), event.inverted())
                QApplication.sendEvent(parent, clone)
                if clone.isAccepted():
                    break
                parent = parent.parentWidget()
            return True
        return super().eventFilter(obj, event)


def install_wheel_guard(app) -> WheelGuard:
    """在 QApplication 上安装全局滚轮拦截器（返回值需持有防回收）"""
    guard = WheelGuard(app)
    app.installEventFilter(guard)
    return guard


class NoFocusRectDelegate(QStyledItemDelegate):
    """去掉单元格的虚线焦点框（只读列表靠选中底色区分即可）"""

    def initStyleOption(self, option, index):
        super().initStyleOption(option, index)
        option.state &= ~QStyle.StateFlag.State_HasFocus


def strip_focus_rect(view) -> None:
    """只读表格/列表：移除点击后的虚线焦点框，仅保留选中底色

    内容不可就地编辑（双击弹对话框）的列表，虚线框只会干扰视觉。
    注意：会覆盖视图上已设的 item delegate，有自定义 delegate 的
    视图应直接继承 NoFocusRectDelegate 而不是调本函数。
    """
    view.setItemDelegate(NoFocusRectDelegate(view))


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
