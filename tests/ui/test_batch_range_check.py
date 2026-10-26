"""批量页脚本/单元列表的 Shift 区间勾选契约。

Shift+点击勾选框：以最近一次普通勾选行为锚点，把 [锚点, 点击行] 闭区间
统一设成点击行的目标状态；无锚点或行序变化后退化为单行勾选。
"""
from types import SimpleNamespace

from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QStyle, QStyleOptionViewItem

from lvjiang.ui.batch.batch_tab import _ReorderTreeWidget


def _make_tree(qtbot, count: int = 5) -> _ReorderTreeWidget:
    tree = _ReorderTreeWidget()
    qtbot.addWidget(tree)
    tree.setColumnCount(1)
    from PyQt6.QtWidgets import QTreeWidgetItem

    for index in range(count):
        row = QTreeWidgetItem([f"item-{index}"])
        row.setFlags(row.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        row.setCheckState(0, Qt.CheckState.Unchecked)
        tree.addTopLevelItem(row)
    tree.show()
    qtbot.waitExposed(tree)
    return tree


def _indicator_center(tree: _ReorderTreeWidget, row: int) -> QPoint:
    item = tree.topLevelItem(row)
    assert item is not None
    option = QStyleOptionViewItem()
    option.initFrom(tree)
    option.rect = tree.visualItemRect(item)
    option.viewItemPosition = QStyleOptionViewItem.ViewItemPosition.OnlyOne
    option.features |= QStyleOptionViewItem.ViewItemFeature.HasCheckIndicator
    option.checkState = item.checkState(0)
    rect = tree.style().subElementRect(
        QStyle.SubElement.SE_ItemViewItemCheckIndicator, option, tree)
    assert rect.isValid() and rect.width() > 0
    return rect.center()


def test_shift_click_checks_range_from_anchor(qtbot):
    tree = _make_tree(qtbot)
    emitted: list[tuple[int, int, int]] = []
    tree.range_check_requested.connect(
        lambda start, end, target: emitted.append((start, end, target)))

    # 普通点击第 0 行勾选框：不触发区间，只把锚点定在第 0 行。
    QTest.mouseClick(
        tree.viewport(), Qt.MouseButton.LeftButton,
        pos=_indicator_center(tree, 0))
    assert emitted == []
    assert tree._check_anchor_row == 0

    # Shift+点击第 3 行勾选框：区间 [0, 3]，目标状态 = 勾选（第 3 行原本未勾）。
    QTest.mouseClick(
        tree.viewport(), Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.ShiftModifier,
        pos=_indicator_center(tree, 3))
    assert emitted == [(0, 3, Qt.CheckState.Checked.value)]
    assert tree._check_anchor_row == 3


def test_shift_click_without_anchor_degrades_to_single_click(qtbot):
    tree = _make_tree(qtbot)
    emitted: list[tuple[int, int, int]] = []
    tree.range_check_requested.connect(
        lambda start, end, target: emitted.append((start, end, target)))

    QTest.mouseClick(
        tree.viewport(), Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.ShiftModifier,
        pos=_indicator_center(tree, 2))
    assert emitted == []
    assert tree._check_anchor_row == 2


def test_shift_click_on_row_body_does_not_emit_range(qtbot):
    tree = _make_tree(qtbot)
    emitted: list[tuple[int, int, int]] = []
    tree.range_check_requested.connect(
        lambda start, end, target: emitted.append((start, end, target)))

    QTest.mouseClick(
        tree.viewport(), Qt.MouseButton.LeftButton,
        pos=_indicator_center(tree, 0))
    item = tree.topLevelItem(4)
    assert item is not None
    body = tree.visualItemRect(item)
    QTest.mouseClick(
        tree.viewport(), Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.ShiftModifier,
        pos=QPoint(body.right() - 4, body.center().y()))
    assert emitted == []


def test_reset_anchor_forgets_previous_row(qtbot):
    tree = _make_tree(qtbot)
    emitted: list[tuple[int, int, int]] = []
    tree.range_check_requested.connect(
        lambda start, end, target: emitted.append((start, end, target)))

    QTest.mouseClick(
        tree.viewport(), Qt.MouseButton.LeftButton,
        pos=_indicator_center(tree, 0))
    tree.reset_check_anchor()
    QTest.mouseClick(
        tree.viewport(), Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.ShiftModifier,
        pos=_indicator_center(tree, 3))
    assert emitted == []


def test_apply_script_range_check_updates_checked_order_and_persists_once(qtbot):
    from lvjiang.ui.batch.batch_tab import BatchTab

    tree = _make_tree(qtbot)
    persisted: list[bool] = []
    batch = SimpleNamespace(
        _script_list=tree,
        _script_candidate_order=["a", "b", "c", "d", "e"],
        _script_order=["c"],
        _updating_script_list=False,
    )
    batch._script_id = BatchTab._script_id.__get__(batch)
    batch._refresh_script_order_column = lambda: None
    batch._persist_script_order = lambda: persisted.append(True)
    batch._sync_script_order_from_rows = (
        BatchTab._sync_script_order_from_rows.__get__(batch))
    batch._apply_script_range_check = (
        BatchTab._apply_script_range_check.__get__(batch))

    # 与 _script_id 的 UserRole 约定一致：脚本行 data(0) 是配置 dict。
    for index, script_id in enumerate(batch._script_candidate_order):
        tree.topLevelItem(index).setData(
            0, Qt.ItemDataRole.UserRole, {"id": script_id})

    batch._apply_script_range_check(0, 3, Qt.CheckState.Checked.value)

    assert [tree.topLevelItem(i).checkState(0) for i in range(5)] == [
        Qt.CheckState.Checked,
        Qt.CheckState.Checked,
        Qt.CheckState.Checked,
        Qt.CheckState.Checked,
        Qt.CheckState.Unchecked,
    ]
    # 之前已勾的 c（第 2 行）在区间内保持勾选；执行顺序按行序取勾选项。
    assert batch._script_order == ["a", "b", "c", "d"]
    assert batch._script_candidate_order == ["a", "b", "c", "d", "e"]
    assert len(persisted) == 1
