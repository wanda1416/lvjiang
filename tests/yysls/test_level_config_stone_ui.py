"""等级配置面板不丢失律准石嵌套规则。"""

from lvjiang.apps.yysls.ui.game_settings.level_config_panel import (
    LevelConfigPanel,
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
