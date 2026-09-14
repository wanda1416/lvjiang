"""场景实体名称拖拽排序表格。"""

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
