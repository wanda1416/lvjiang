"""场景实体名称拖拽排序表格。"""

from PyQt6.QtCore import QPointF, Qt
from PyQt6.QtWidgets import QAbstractItemView, QTableWidgetItem

from lvjiang.ui.scene_editor.entity_order_table import EntityOrderTable


def _table(qtbot):
    table = EntityOrderTable(0, 2)
    qtbot.addWidget(table)
    for key in ("a", "b", "readonly", "c"):
        row = table.rowCount()
        table.insertRow(row)
        table.setItem(row, 0, QTableWidgetItem(key.upper()))
        table.setItem(row, 1, QTableWidgetItem(key))
        if key != "readonly":
            table.set_entity_order_key(row, key)
    return table


def test_entity_order_table_only_orders_marked_rows(qtbot):
    table = _table(qtbot)

    assert table.dragDropMode() == QAbstractItemView.DragDropMode.InternalMove
    assert table.reordered_keys(3, 0, below=False) == ["c", "a", "b"]
    assert table.reordered_keys(0, 1, below=True) == ["b", "a", "c"]
    # 只读引用行既不能发起拖拽，也不能作为落点。
    assert table.reordered_keys(2, 0, below=False) == ["a", "b", "c"]
    assert table.reordered_keys(0, 2, below=False) == ["a", "b", "c"]


class _FakeDropEvent:
    def __init__(self, pos):
        self._pos = QPointF(pos)
        self.action = Qt.DropAction.IgnoreAction
        self.accepted = False

    def position(self):
        return self._pos

    def setDropAction(self, action):
        self.action = action

    def accept(self):
        self.accepted = True

    def ignore(self):
        self.accepted = False


def test_drop_refresh_waits_until_qt_finishes_move_cleanup(qtbot):
    table = _table(qtbot)
    table.resize(400, 240)
    table.show()
    qtbot.waitExposed(table)
    received = []

    def refresh(order, moved_key):
        received.append((order, moved_key))
        table.setRowCount(0)
        for key in (*order[:2], "readonly", *order[2:]):
            row = table.rowCount()
            table.insertRow(row)
            table.setItem(row, 0, QTableWidgetItem(key.upper()))
            table.setItem(row, 1, QTableWidgetItem(key))
            if key != "readonly":
                table.set_entity_order_key(row, key)

    table.entity_order_changed.connect(refresh)
    table._drag_source_row = 3
    target_pos = table.visualItemRect(table.item(0, 0)).center()
    event = _FakeDropEvent(target_pos)

    table.dropEvent(event)  # type: ignore[arg-type]

    # dropEvent 返回后 Qt 才会清理 MoveAction 的源行；刷新必须晚于它。
    assert received == []
    for column in range(table.columnCount()):
        table.takeItem(3, column)
    qtbot.waitUntil(lambda: bool(received))

    assert event.accepted
    assert event.action == Qt.DropAction.MoveAction
    assert received == [(["a", "c", "b"], "c")]
    assert all(
        table.item(row, column) is not None
        for row in range(table.rowCount())
        for column in range(table.columnCount())
    )
