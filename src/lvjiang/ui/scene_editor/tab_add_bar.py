"""Tab 条末尾的 +：紧贴最后一个 Tab，而不是吸在条的最右侧。"""

from collections.abc import Callable

from PyQt6.QtCore import QPoint, Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import QPushButton, QTabBar, QTabWidget, QToolButton

from ..widgets.button_styles import apply_compact_button_style


class _AddAwareTabBar(QTabBar):
    """Tab 布局变化时发信号；QTabBar.tabLayoutChange 是受保护虚函数，不是信号。"""

    layout_changed = pyqtSignal()

    def tabLayoutChange(self) -> None:  # type: ignore[override]
        super().tabLayoutChange()
        self.layout_changed.emit()

    def resizeEvent(self, event):  # type: ignore[override]
        super().resizeEvent(event)
        self.layout_changed.emit()

    def showEvent(self, event):  # type: ignore[override]
        super().showEvent(event)
        self.layout_changed.emit()


class InlineAddTabWidget(QTabWidget):
    """Tab 条末尾的 "+"：挂在 TabWidget 上，贴着最后一个 Tab 右侧。

    QTabBar 在 Tab 不多时宽度只等于 Tab 总宽，把按钮放进 TabBar 会压住
    最后一个 Tab；放在 TabWidget 上才有它右侧的空白可用。位置随 Tab
    增减、拖拽排序和窗口宽度重算，没有 Tab 时贴在条的最左端。
    """

    _GAP = 4
    _EDGE = 2
    _WIDTH = 28

    def __init__(
        self, tooltip: str, on_click: Callable[[], None], parent=None,
    ):
        super().__init__(parent)
        self._add_bar = _AddAwareTabBar(self)
        self._add_bar.setMinimumHeight(
            max(24, self.fontMetrics().height() + 12))
        self.setTabBar(self._add_bar)
        self._button = QPushButton("+", self)
        self._button.setToolTip(tooltip)
        self._button.setAccessibleName(tooltip)
        self._button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._button.setAutoDefault(False)
        apply_compact_button_style(self._button, variant="neutral")
        self._button.clicked.connect(on_click)
        self._add_bar.layout_changed.connect(self._sync_button)
        QTimer.singleShot(0, self._sync_button)

    @property
    def add_button(self) -> QPushButton:
        return self._button

    def resizeEvent(self, event):  # type: ignore[override]
        super().resizeEvent(event)
        self._sync_button()

    def showEvent(self, event):  # type: ignore[override]
        super().showEvent(event)
        self._sync_button()

    def _sync_button(self) -> None:
        bar = self._add_bar
        fallback = self.fontMetrics().height() + 8
        height = max(20, (bar.height() or fallback) - 4)
        self._button.resize(self._WIDTH, height)
        origin = bar.mapTo(self, QPoint(0, 0))
        if bar.count():
            last = bar.tabRect(bar.count() - 1)
            x = origin.x() + last.right() + 1 + self._GAP
        else:
            x = origin.x() + self._EDGE
        limit = self.width() - self._EDGE - self._WIDTH
        # Tab 溢出时右侧滚动按钮属于 Tab 条，给箭头让位。
        for child in bar.findChildren(QToolButton):
            if not child.isVisible():
                continue
            if child.geometry().center().x() <= bar.width() // 2:
                continue
            arrow_x = bar.mapTo(self, child.geometry().topLeft()).x()
            limit = min(limit, arrow_x - self._EDGE - self._WIDTH)
        x = max(origin.x() + self._EDGE, min(x, limit))
        y = origin.y() + max(0, (bar.height() - height) // 2)
        self._button.move(x, y)
