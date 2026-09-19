"""可复用 UI 控件"""

from PyQt6.QtCore import QPointF, QRect, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QAction, QTextCursor, QWheelEvent
from PyQt6.QtWidgets import (
    QAbstractSpinBox,
    QApplication,
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLayout,
    QMenu,
    QPushButton,
    QTextEdit,
    QWidget,
)

_MAX_LOG_LINES = 1000


def _forward_wheel_to_parent(self, event: QWheelEvent | None) -> None:
    """下拉框/数字输入框不响应滚轮：克隆后沿父链上抛至被接收，页面照常滚动。"""
    if event is None:
        return
    parent = self.parentWidget()
    while parent is not None:
        pos = parent.mapFromGlobal(event.globalPosition().toPoint())
        clone = QWheelEvent(
            QPointF(pos), event.globalPosition(),
            event.pixelDelta(), event.angleDelta(),
            event.buttons(), event.modifiers(),
            event.phase(), event.inverted())
        QApplication.sendEvent(parent, clone)
        if clone.isAccepted():
            break
        parent = parent.parentWidget()
    event.accept()


def install_wheel_guard(app=None) -> None:
    """全局屏蔽下拉框/数字输入框的滚轮改值（防滑动页面时误改）。

    直接替换两个基类的 ``wheelEvent``：PyQt 按实例的 Python 类型链查找虚函数
    重写，之后创建的 QComboBox / QAbstractSpinBox 及其子类都生效。早先装在
    QApplication 上的事件过滤器要为每个控件的每个事件回到 Python 判一次类型，
    启动期间几十万次调用能占掉约一秒。
    """
    QComboBox.wheelEvent = _forward_wheel_to_parent  # type: ignore[method-assign, assignment]
    QAbstractSpinBox.wheelEvent = _forward_wheel_to_parent  # type: ignore[method-assign, assignment]


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


def centered_cell_widget(widget: QWidget) -> QWidget:
    """把单个控件放进无边距容器，供表格单元格水平、垂直居中。"""
    container = QWidget()
    layout = QHBoxLayout(container)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.addWidget(widget, alignment=Qt.AlignmentFlag.AlignCenter)
    return container


def add_top_aligned_row(form: QFormLayout, label_text: str, field: QWidget) -> QLabel:
    """向表单加一行键值，键标签顶对齐。

    QFormLayout 给多行字段配的标签单元格可达 7/4 行高，而 QLabel 默认垂直
    居中，键会落到值的第一行下方；键标签自身顶对齐后二者首行齐平。
    """
    label = QLabel(label_text)
    label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
    form.addRow(label, field)
    return label


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
        margins = self.contentsMargins()
        item_sizes = [
            item.sizeHint()
            for item in self._items
            if item.widget() is not None
        ]
        width = sum(size.width() for size in item_sizes)
        width += self._spacing * max(0, len(item_sizes) - 1)
        height = max((size.height() for size in item_sizes), default=0)
        return QSize(
            width + margins.left() + margins.right(),
            height + margins.top() + margins.bottom(),
        )

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


class MultiSelectMenu(QMenu):
    """勾选后保持展开的菜单，便于一次勾选多项。"""

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 — Qt 命名
        action = self.activeAction()
        if action is not None and action.isCheckable():
            action.trigger()
            event.accept()
            return
        super().mouseReleaseEvent(event)


class MultiSelectButton(QPushButton):
    """带下拉多选菜单的按钮：首项「全部」一键全选/清空，按钮文字汇总当前选择。

    ``options`` 为 ``(key, label)`` 列表；默认全选。``selected_keys()`` 返回
    仍按 ``options`` 顺序排列的已选 key。全选时按钮显示 ``all_label``，
    一项未选显示 ``none_label``，其余列出前几项标签。
    """

    selection_changed = pyqtSignal()

    def __init__(
        self,
        options: list[tuple[str, str]],
        *,
        all_label: str,
        none_label: str = "",
        max_shown: int = 3,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._options = list(options)
        self._all_label = all_label
        self._none_label = none_label or all_label
        self._max_shown = max_shown
        self._syncing = False

        self._menu = MultiSelectMenu(self)
        self._all_action = QAction(all_label, self._menu)
        self._all_action.setCheckable(True)
        self._all_action.setChecked(True)
        self._all_action.toggled.connect(self._on_all_toggled)
        self._menu.addAction(self._all_action)
        self._menu.addSeparator()
        self._actions: dict[str, QAction] = {}
        for key, label in self._options:
            action = QAction(label, self._menu)
            action.setCheckable(True)
            action.setChecked(True)
            action.toggled.connect(self._on_item_toggled)
            self._menu.addAction(action)
            self._actions[key] = action
        self.setMenu(self._menu)
        self._refresh_text()

    # ── 状态 ──

    def selected_keys(self) -> list[str]:
        return [key for key, _label in self._options
                if self._actions[key].isChecked()]

    def all_selected(self) -> bool:
        return all(action.isChecked() for action in self._actions.values())

    def set_selected(self, keys) -> None:
        wanted = set(keys)
        self._syncing = True
        try:
            for key, action in self._actions.items():
                action.setChecked(key in wanted)
            self._all_action.setChecked(self.all_selected())
        finally:
            self._syncing = False
        self._refresh_text()
        self.selection_changed.emit()

    # ── 内部 ──

    def _on_all_toggled(self, checked: bool) -> None:
        if self._syncing:
            return
        self.set_selected(self._actions if checked else ())

    def _on_item_toggled(self, _checked: bool) -> None:
        if self._syncing:
            return
        self._syncing = True
        try:
            self._all_action.setChecked(self.all_selected())
        finally:
            self._syncing = False
        self._refresh_text()
        self.selection_changed.emit()

    def _refresh_text(self) -> None:
        selected = self.selected_keys()
        if not self._options or len(selected) == len(self._options):
            text = self._all_label
        elif not selected:
            text = self._none_label
        else:
            labels = [label for key, label in self._options if key in selected]
            text = "、".join(labels[:self._max_shown])
            if len(labels) > self._max_shown:
                text += f" +{len(labels) - self._max_shown}"
        self.setText(text)
