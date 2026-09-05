"""等级配置面板不丢失律准石嵌套规则。"""

from lvjiang.apps.yysls.ui.game_settings.level_config_panel import (
    LevelConfigPanel,
    StoneRuleDialog,
)


def test_level_panel_preserves_105_stone_rules(qtbot):
    panel = LevelConfigPanel()
    qtbot.addWidget(panel)

    row = next(
        item for item in panel._configs_raw() if item["level"] == 105)

    assert row["reset_no_refund"] is True
    assert row["tuning_stones"]["gold"]["tune_cost"] == {
        1: 0.0, 2: 6.0, 3: 12.0, 4: 24.0, 5: 36.0}
    assert row["tuning_stones"]["gold"]["recycle_refund"][1] == 6.0
    assert row["tuning_stones"]["gold"]["recycle_refund"][5] == 68.4


def test_stone_dialog_uses_five_tune_rows_and_quality_zones(qtbot):
    raw = {
        "gold": {
            "tune_cost": {1: 0, 2: 6, 3: 12, 4: 24, 5: 36},
            "recycle_refund": {
                1: 6, 2: 10.8, 3: 20.4, 4: 39.6, 5: 68.4},
        },
    }
    dialog = StoneRuleDialog(raw, reset_no_refund=True)
    qtbot.addWidget(dialog)

    assert dialog._table.rowCount() == 5
    assert dialog._table.columnCount() == 7
    assert [
        dialog._table.cellWidget(row, 0).text() for row in range(5)
    ] == ["宫", "商", "角", "徵", "羽"]

    # 宫的调律消耗、全部重置返还均是灰色空白，不显示 0。
    assert dialog._cell_stacks[("gold", 1, "tune_cost")].currentIndex() == 1
    assert dialog._cell_stacks[("gold", 5, "reset_refund")].currentIndex() == 1
    # 未启用的紫装区整体灰色空白。
    assert all(
        stack.currentIndex() == 1
        for (quality, _affix, _key), stack in dialog._cell_stacks.items()
        if quality == "purple"
    )
    # 金装可编辑项仍显示原值。
    assert dialog._cell_stacks[("gold", 2, "tune_cost")].currentIndex() == 0
    assert dialog._spins[("gold", 2, "tune_cost")].value() == 6
