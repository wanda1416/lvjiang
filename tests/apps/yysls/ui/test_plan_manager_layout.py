"""方案管理表格的列宽分配与进入编辑的方式。

两件事都在这里钉住：短文本列（玩法、对战类型）必须有可读宽度，不能被相邻
的长列挤成一条；进入编辑必须靠双击，单击只负责选中。
"""

from pathlib import Path

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QAbstractItemView, QApplication

from lvjiang.apps.yysls.core.loadout import LoadoutRepository
from lvjiang.apps.yysls.ui.loadout.plan_manager_dialog import PlanManagerDialog
from lvjiang.apps.yysls.ui.loadout.plan_table_delegate import (
    COL_COMBAT,
    COL_MAIN_ART,
    COL_NAME,
    COL_PLAYSTYLE,
    MIN_COLUMN_WIDTHS,
)

EDITING = QAbstractItemView.State.EditingState


@pytest.fixture
def dialog(qtbot, tmp_path: Path):
    repo = LoadoutRepository("tester", tmp_path)
    repo.create_plan("甲方案", "无名剑法", "斩雪刀法", playstyle="走地玉")
    repo.create_plan("乙方案", "无名枪法", "斩雪刀法", playstyle="威威")
    widget = PlanManagerDialog(["tester"], "tester", tmp_path)
    qtbot.addWidget(widget)
    widget.resize(1000, 560)
    widget.show()
    QApplication.processEvents()
    return widget


def _measure(dialog: PlanManagerDialog, width: int) -> tuple[int, list[int]]:
    """把对话框拉到指定宽度，返回表格视口宽度与各列实际列宽。"""
    dialog.resize(width, 560)
    QApplication.processEvents()
    table = dialog._table
    header = table.horizontalHeader()
    assert header is not None
    return (table.viewport().width(),
            [header.sectionSize(col) for col in range(table.columnCount())])


def _double_click(table, row: int, col: int) -> None:
    """按真实事件序列双击：press/release 之后 dblclick 才能进入编辑。"""
    index = table.model().index(row, col)
    table.scrollTo(index)
    QApplication.processEvents()
    center = table.visualRect(index).center()
    QTest.mousePress(table.viewport(), Qt.MouseButton.LeftButton,
                     Qt.KeyboardModifier.NoModifier, center)
    QTest.mouseRelease(table.viewport(), Qt.MouseButton.LeftButton,
                       Qt.KeyboardModifier.NoModifier, center)
    QTest.mouseDClick(table.viewport(), Qt.MouseButton.LeftButton,
                      Qt.KeyboardModifier.NoModifier, center)
    QApplication.processEvents()


def test_extra_width_is_shared_with_arts_columns(dialog):
    """拉宽对话框时富余宽度分给名称与武学列，短列保持内容宽度。"""
    viewport_narrow, narrow = _measure(dialog, 1000)
    viewport_wide, wide = _measure(dialog, 1300)

    assert viewport_wide > viewport_narrow
    # 名称列不再独吞剩余宽度，最多只占视口的一小半。
    assert narrow[COL_NAME] < viewport_narrow // 2
    # 富余宽度同时分给了武学列，两者都随窗口变宽。
    assert wide[COL_NAME] > narrow[COL_NAME]
    assert wide[COL_MAIN_ART] > narrow[COL_MAIN_ART]
    # 玩法是固定很短的值，按内容收窄即可，不该跟着窗口变宽。
    assert wide[COL_PLAYSTYLE] == narrow[COL_PLAYSTYLE]


def test_short_columns_keep_a_readable_width(dialog):
    """玩法和对战类型内容很短，但列宽不能被压到只剩一条。"""
    _measure(dialog, 1000)
    header = dialog._table.horizontalHeader()
    assert header is not None
    assert header.sectionSize(COL_PLAYSTYLE) >= MIN_COLUMN_WIDTHS[COL_PLAYSTYLE]
    assert header.sectionSize(COL_COMBAT) >= MIN_COLUMN_WIDTHS[COL_COMBAT]


def test_double_click_starts_editing(dialog):
    """双击单元格进入编辑：只写 SelectedClicked 时双击根本进不去。"""
    _double_click(dialog._table, 0, COL_NAME)
    assert dialog._table.state() == EDITING


def test_single_click_only_selects(dialog):
    """单击只换选中行，不进入编辑——挑行时不该误触编辑器。"""
    table = dialog._table
    table.selectRow(0)
    QApplication.processEvents()
    center = table.visualRect(table.model().index(0, COL_NAME)).center()
    QTest.mouseClick(table.viewport(), Qt.MouseButton.LeftButton,
                     Qt.KeyboardModifier.NoModifier, center)
    QApplication.processEvents()

    assert table.state() != EDITING
