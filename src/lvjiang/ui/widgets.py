"""可复用 UI 控件"""

from PyQt6.QtCore import QEvent, QObject, QPointF, QRect, QSize
from PyQt6.QtGui import QTextCursor, QWheelEvent
from PyQt6.QtWidgets import (
    QAbstractSpinBox,
    QApplication,
    QComboBox,
    QLayout,
    QTextEdit,
)

_MAX_LOG_LINES = 1000


class WheelGuard(QObject):
    """应用级滚轮拦截器：下拉框/数字输入框一律屏蔽滚轮改值

    装到 QApplication 上，全局生效（含后续新增控件，无需逐个
    定制控件类）；滚轮事件交回父级滚动区域，页面滚动不受影响。
    """

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # type: ignore[override]
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


#: 去焦点框的样式规则，见 strip_focus_rect
_NO_FOCUS_RECT_QSS = "QAbstractItemView { outline: 0; }"


def strip_focus_rect(view) -> None:
    """只读表格/列表：移除点击后的虚线焦点框，仅保留选中底色

    内容不可就地编辑（双击弹对话框）的列表，虚线框只会干扰视觉。

    **用样式表而不是自定义 delegate**（原先是一个重写 ``initStyleOption``
    的 ``QStyledItemDelegate`` 子类）。Qt 在视图析构收尾阶段仍可能派发排队
    中的 paint 事件并回调 delegate 的虚函数；此时该虚函数若是 **Python 重写**
    的，PyQt 要重入 Python，就会先抛
    ``wrapped C/C++ object ... has been deleted``、随后在 C++ 侧**段错误**。
    只要构造过 SceneTab 这种较重的控件树再销毁（关闭场景编辑器、测试收尾
    统一 processEvents），就会稳定复现。

    换 parent（挂到 QApplication 下）和改成全局共享一个实例都试过，**都不能
    解决**——问题不在 delegate 的所有权，而在"Qt 在收尾阶段回调 Python 重写
    的虚函数"这件事本身。改用 Qt 原生 delegate 则不崩，故这里彻底不用
    Python 虚函数：样式表由 Qt 自己解析，全程不回调 Python。

    两种写法的渲染结果经像素级比对**完全一致**（见
    tests/ui/test_strip_focus_rect.py），不是等价的猜测。
    """
    existing = view.styleSheet()
    if _NO_FOCUS_RECT_QSS in existing:
        return
    view.setStyleSheet(
        f"{existing}\n{_NO_FOCUS_RECT_QSS}" if existing else _NO_FOCUS_RECT_QSS)


class TrimmedLogEdit(QTextEdit):
    """自动限制最大行数的只读日志文本框

    超过 _MAX_LOG_LINES 行时，自动裁剪掉前 1/4 的旧行，
    避免长时间运行时内存无限增长。
    """

    def __init__(self, max_lines: int = _MAX_LOG_LINES):
        super().__init__()
        self._max_lines = max_lines
        self.setReadOnly(True)

    def append(self, text: str):  # type: ignore[override]
        super().append(text)
        doc = self.document()
        assert doc is not None
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
