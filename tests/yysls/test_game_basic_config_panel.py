from lvjiang.apps.yysls.ui.game_settings.basic_config_panel import (
    BasicConfigPanel,
)
from lvjiang.apps.yysls.ui.game_settings.config_tab import GameConfigTab


class _FakeGameConfig:
    def __init__(self):
        self.saved: dict | None = None

    def get_equipment_cooldown_days(self) -> int:
        return 5

    def get_raw(self) -> dict:
        return {"level_configs": []}

    def save(self, data: dict) -> None:
        self.saved = data


def test_basic_config_cooldown_defaults_to_five_and_auto_saves(
    qtbot, monkeypatch,
):
    manager = _FakeGameConfig()
    monkeypatch.setattr(
        "lvjiang.apps.yysls.ui.game_settings.basic_config_panel.get_game_config",
        lambda: manager,
    )
    panel = BasicConfigPanel()
    qtbot.addWidget(panel)

    assert panel._cooldown_days.value() == 5
    assert manager.saved is None

    panel._cooldown_days.setValue(7)

    assert manager.saved is not None
    assert manager.saved["basic_config"]["equipment_cooldown_days"] == 7
    assert "已保存并生效" in panel._status_label.text()


def test_basic_config_is_the_first_game_config_tab(qtbot):
    tab = GameConfigTab()
    qtbot.addWidget(tab)

    assert tab._tabs.tabText(0) == "基础配置"
    assert tab._tabs.widget(0) is tab._basic_config_panel
