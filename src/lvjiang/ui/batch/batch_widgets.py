"""批量执行页的状态数据模型与可拖拽排序树控件。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import cast

from PyQt6.QtCore import QEvent, QObject, QPoint, QRect, Qt, pyqtSignal
from PyQt6.QtGui import QAction, QDropEvent, QMouseEvent
from PyQt6.QtWidgets import (
    QMenu,
    QStyle,
    QStyleOptionViewItem,
    QTreeWidget,
    QTreeWidgetItem,
    QWidget,
)

from ...i18n import tr
from ..widgets.theme import get_theme_manager
from .batch_runner import (
    ST_FAILED,
    ST_NOT_RUN,
    ST_PENDING,
    ST_RUNNING,
    ST_SKIPPED,
    ST_SUCCESS,
    PlannedTask,
)


@dataclass
class _BatchProgressState:
    """一次批量运行在进度页里的完整状态。

    轮次是一等维度：``statuses`` 以 ``(轮次, 条目序号, 脚本 ID)`` 为键，
    新一轮的写入不会覆盖上一轮，历史轮因此可以随时回看。
    """

    entries: list[str]
    scripts: list[tuple[str, str]]
    plans: dict[tuple[int, str], PlannedTask]
    rounds_total: int = 1
    #: 属性单元各自推进轮次，没有全局轮屏障；用户模式下所有条目同轮。
    attr_mode: bool = False
    statuses: dict[tuple[int, int, str], str] = field(default_factory=dict)
    #: 条目序号 → 该条目最近一次进入的轮次。
    unit_rounds: dict[int, int] = field(default_factory=dict)
    #: 0 = 看当前轮；>0 = 只读查看该历史轮。
    viewing_round: int = 0
    terminal: bool = False


def _status_color(status: str):
    """返回随主题变化的批量任务状态背景色。"""
    from PyQt6.QtGui import QColor
    tokens = get_theme_manager().tokens
    colours = {
        ST_PENDING: tokens.surface_alt,
        ST_NOT_RUN: tokens.surface_alt,
        ST_RUNNING: tokens.info_surface,
        ST_SUCCESS: tokens.success_surface,
        ST_FAILED: tokens.danger_surface,
        ST_SKIPPED: tokens.warning_surface,
    }
    value = colours.get(status)
    return QColor(value) if value else None

_BATCH_LIST_VERTICAL_PADDING = 12
_TABLE_COLUMN_HORIZONTAL_PADDING = 20
_TABLE_VIEWPORT_SAFETY_MARGIN = 4


def _batch_list_row_height(widget: QWidget) -> int:
    """Use the same font-based row height in the script and config pages."""
    return widget.fontMetrics().height() + _BATCH_LIST_VERTICAL_PADDING


def _four_cjk_column_width(widget: QWidget) -> int:
    """Default compact width that still fits four CJK characters."""
    return (
        widget.fontMetrics().horizontalAdvance("汉字宽度")
        + _TABLE_COLUMN_HORIZONTAL_PADDING
    )


@dataclass(frozen=True)
class ProfileOrderStatus:
    """右键「指定顺序排序」当前的可用性。

    属性单元或配置组没有指定排序时，这个动作不适用，隐藏；配置了但
    Profile 定义已被删除属于「功能存在、此刻不可用」，保留并置灰，把原因
    写在菜单项上，不让用户点了才发现没反应。
    """

    visible: bool = False
    enabled: bool = False
    reason: str = ""


class _ReorderTreeWidget(QTreeWidget):
    """Flat tree that reports a completed internal drag/drop reorder."""

    order_changed = pyqtSignal()
    restore_order_requested = pyqtSignal()
    shuffle_order_requested = pyqtSignal()
    profile_order_requested = pyqtSignal()
    #: Shift+点击勾选框区间勾选：start_row, end_row, target_state（Qt.CheckState.value）
    range_check_requested = pyqtSignal(int, int, int)

    def __init__(self, parent: QWidget | None = None, *, profile_order: bool = False) -> None:
        super().__init__(parent)
        self._profile_order = profile_order
        # 菜单弹出时现算：Profile 定义可能在别处被删，缓存值会过期。
        self.profile_order_status: Callable[[], ProfileOrderStatus] | None = None
        #: 最近一次普通勾选的行 index；行序变化（重排/刷新）后失效。
        self._check_anchor_row: int | None = None
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_order_menu)
        viewport = self.viewport()
        if viewport is not None:
            viewport.installEventFilter(self)

    def reset_check_anchor(self) -> None:
        """清空区间勾选锚点：整表刷新、重排或全选/全不选后调用。"""
        self._check_anchor_row = None

    def dropEvent(self, event: QDropEvent | None) -> None:
        super().dropEvent(event)
        if event is not None and event.isAccepted():
            self._check_anchor_row = None
            self.order_changed.emit()

    def _check_indicator_rect(self, item: QTreeWidgetItem) -> QRect:
        option = QStyleOptionViewItem()
        option.initFrom(self)
        option.rect = self.visualItemRect(item)
        option.viewItemPosition = QStyleOptionViewItem.ViewItemPosition.OnlyOne
        option.features |= QStyleOptionViewItem.ViewItemFeature.HasCheckIndicator
        option.checkState = item.checkState(0)
        style = self.style()
        if style is None:
            return QRect()
        return style.subElementRect(
            QStyle.SubElement.SE_ItemViewItemCheckIndicator, option, self)

    def eventFilter(self, watched: QObject | None, event: QEvent | None) -> bool:
        if watched is None or event is None:
            return super().eventFilter(watched, event)
        try:
            viewport = self.viewport()
        except RuntimeError:
            # 窗口销毁阶段 C++ 对象可能先于事件队列消失；此时再访问 self
            # 会在 Qt 事件循环里抛 RuntimeError（离屏下还会拖垮整个进程）。
            return False
        if (watched is viewport
                and event.type() == QEvent.Type.MouseButtonPress):
            mouse = cast(QMouseEvent, event)
            if mouse.button() == Qt.MouseButton.LeftButton:
                item = self.itemAt(mouse.position().toPoint())
                if item is not None:
                    row = self.indexOfTopLevelItem(item)
                    if self._check_indicator_rect(item).contains(
                            mouse.position().toPoint()):
                        shift = bool(
                            mouse.modifiers()
                            & Qt.KeyboardModifier.ShiftModifier)
                        if (shift and self._check_anchor_row is not None
                                and self._check_anchor_row != row):
                            target = (
                                Qt.CheckState.Unchecked
                                if item.checkState(0) == Qt.CheckState.Checked
                                else Qt.CheckState.Checked)
                            self.range_check_requested.emit(
                                self._check_anchor_row, row, target.value)
                            self._check_anchor_row = row
                            return True
                        self._check_anchor_row = row
        return super().eventFilter(watched, event)

    def _show_order_menu(self, position: QPoint) -> None:
        menu = QMenu(self)
        restore_action = QAction(tr("恢复默认顺序"), menu)
        shuffle_action = QAction(tr("随机打乱顺序"), menu)
        restore_action.triggered.connect(
            lambda _checked=False: self.restore_order_requested.emit())
        shuffle_action.triggered.connect(
            lambda _checked=False: self.shuffle_order_requested.emit())
        has_rows = self.topLevelItemCount() > 0
        restore_action.setEnabled(has_rows)
        shuffle_action.setEnabled(self.topLevelItemCount() > 1)
        menu.addAction(restore_action)
        menu.addAction(shuffle_action)
        if self._profile_order:
            status = (self.profile_order_status() if self.profile_order_status
                      else ProfileOrderStatus())
            if status.visible:
                text = tr("指定顺序排序")
                if status.reason:
                    text += f"（{status.reason}）"
                profile_action = QAction(text, menu)
                profile_action.setEnabled(has_rows and status.enabled)
                profile_action.setToolTip(status.reason)
                profile_action.triggered.connect(
                    lambda _checked=False: self.profile_order_requested.emit())
                menu.addAction(profile_action)
                menu.setToolTipsVisible(True)
        viewport = cast(QWidget, self.viewport())
        menu.exec(viewport.mapToGlobal(position))


