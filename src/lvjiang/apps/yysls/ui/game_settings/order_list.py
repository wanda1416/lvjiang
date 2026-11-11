"""带内部拖动排序的配置列表（排序只在系统配置/开发模式开放）。

武学 / 流派 / 玩法的顺序是**配置内容**：它决定下拉候选、方案默认值和反查顺序。
开发模式写的就是 system，所以顺序可以随便调；用户模式写的只是 local 覆盖，而
键序不参与 diff——放开拖动只会让用户改出一份存不下来的顺序，因此不给入口。

另见 `docs/30-architecture/30-overview/04-config-layering.md`。
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import TypeVar

from loguru import logger
from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QDropEvent
from PyQt6.QtWidgets import QAbstractItemView, QListWidget

from .....i18n import tr
from . import factory_guard

_M = TypeVar("_M", bound=Mapping)

#: 用户模式下列表不可排序时的说明
NO_REORDER_HINT = tr("顺序由系统配置决定；你新增的条目会排在最后。")


def reorder_mapping(mapping: _M, names: list[str]) -> dict:
    """按 ``names``（列表控件当前行序）重排映射的键序，返回新 dict。

    只改键序，值原样引用。``names`` 里没有的键（控件里没有对应行，通常是
    手工改过的畸形数据）保持原有相对顺序追加在末尾并记 warning，不会因为
    一次重排被丢掉。
    """
    source = dict(mapping or {})
    ordered: dict = {}
    for name in names:
        if name in source and name not in ordered:
            ordered[name] = source[name]
    leftover = [name for name in source if name not in ordered]
    if leftover:
        logger.warning(f"重排时发现 {len(leftover)} 个键没有对应列表行，已保留在末尾")
        for name in leftover:
            ordered[name] = source[name]
    return ordered


class DragOrderListWidget(QListWidget):
    """InternalMove 列表；drop 后通知面板按新行序写回配置。

    拖动期间屏蔽视图信号：``QListWidget`` 内部移动会重建行，如果仍触发
    ``itemChanged`` / ``currentRowChanged``，面板会把重排误判成改名或切换。
    """

    order_changed = pyqtSignal()

    def __init__(self, parent=None, *, reorderable: bool | None = None) -> None:
        super().__init__(parent)
        # 用户模式（写 local 的用户侧）不给排序入口，见模块文档。
        self._reorderable = (
            not factory_guard.is_user_mode()
            if reorderable is None else reorderable
        )
        if self._reorderable:
            self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
            self.setDefaultDropAction(Qt.DropAction.MoveAction)
            self.setDropIndicatorShown(True)
        else:
            self.setDragDropMode(QAbstractItemView.DragDropMode.NoDragDrop)
            self.setToolTip(NO_REORDER_HINT)

    def is_reorderable(self) -> bool:
        """当前是否允许拖动排序（用户模式下恒为 False）"""
        return self._reorderable

    def dropEvent(self, event: QDropEvent | None) -> None:
        if event is None or not self._reorderable:
            return
        self.blockSignals(True)
        try:
            super().dropEvent(event)
        finally:
            self.blockSignals(False)
        if event.isAccepted():
            # 投递到事件循环：面板要按新行序重建列表，不能在 drop 调用栈里
            # 清空正在被 Qt 处理的 item。
            QTimer.singleShot(0, self.order_changed.emit)
