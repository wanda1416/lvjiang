"""支持从名称列拖拽调整场景实体定义顺序的表格。"""

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QDropEvent
from PyQt6.QtWidgets import QAbstractItemView, QTableWidget


class EntityOrderTable(QTableWidget):
    """不移动表格单元格，只发出拖拽后的实体 key 顺序。

    ``QTableWidget`` 的原生 ``InternalMove`` 会重建单元格，表格里的禁用
    复选框等 cell widget 会丢失。这里截住 drop，让调用方先持久化 YAML，
    再按注册表中的新顺序完整刷新表格。

    只有名称列（第 0 列）带 ``UserRole`` key 的行可拖动。引用等只读行不
    设置 key，因而不会混入本地定义顺序。
    """

    entity_order_changed = pyqtSignal(list, str)

    def __init__(self, rows: int = 0, columns: int = 0, parent=None):
        super().__init__(rows, columns, parent)
        self._drag_source_row = -1
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setDropIndicatorShown(True)

    def set_entity_order_key(self, row: int, key: str) -> None:
        """把名称单元格标记为可排序实体。"""
        item = self.item(row, 0)
        if item is None:
            raise ValueError("名称单元格必须先写入表格")
        item.setData(Qt.ItemDataRole.UserRole, key)

    def entity_key(self, row: int) -> str:
        item = self.item(row, 0)
        if item is None:
            return ""
        value = item.data(Qt.ItemDataRole.UserRole)
        return str(value) if value is not None else ""

    def reordered_keys(self, source_row: int, target_row: int, *,
                       below: bool) -> list[str]:
        """计算一次 drop 后的可排序 key 顺序，供 dropEvent 与测试共用。"""
        movable = [
            (row, self.entity_key(row))
            for row in range(self.rowCount())
            if self.entity_key(row)
        ]
        rows = [row for row, _key in movable]
        keys = [key for _row, key in movable]
        if source_row not in rows or target_row not in rows:
            return keys

        source_index = rows.index(source_row)
        target_index = rows.index(target_row) + int(below)
        key = keys.pop(source_index)
        if source_index < target_index:
            target_index -= 1
        keys.insert(max(0, min(target_index, len(keys))), key)
        return keys

    def startDrag(self, supported_actions: Qt.DropAction) -> None:
        # 需求约定从名称发起拖拽；点其他列只做普通整行选择。
        if self.currentColumn() != 0 or not self.entity_key(self.currentRow()):
            return
        self._drag_source_row = self.currentRow()
        try:
            super().startDrag(supported_actions)
        finally:
            self._drag_source_row = -1

    def dropEvent(self, event: QDropEvent | None) -> None:
        if event is None:
            return
        source_row = self._drag_source_row
        pos = event.position().toPoint()
        target = self.indexAt(pos)
        target_row = target.row()
        if (source_row < 0 or target_row < 0
                or not self.entity_key(target_row)):
            event.ignore()
            return

        below = pos.y() >= self.visualRect(target).center().y()
        old_order = [
            self.entity_key(row) for row in range(self.rowCount())
            if self.entity_key(row)
        ]
        new_order = self.reordered_keys(
            source_row, target_row, below=below)
        if new_order == old_order:
            event.ignore()
            return

        event.setDropAction(Qt.DropAction.MoveAction)
        event.accept()
        moved_key = self.entity_key(source_row)
        # QAbstractItemView.startDrag() 会在 dropEvent 返回后才执行 MoveAction
        # 的源单元格清理。若这里同步刷新，随后那次清理会把新表格中的同一行
        # 再挖空，看起来就是落点一列变白。延迟到下一轮事件循环，等 Qt 收尾
        # 完成后再由调用方完整刷新表格。
        QTimer.singleShot(
            0,
            lambda: self.entity_order_changed.emit(new_order, moved_key),
        )
