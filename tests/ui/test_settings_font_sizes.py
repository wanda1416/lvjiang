"""Font-size settings tab and immediate-apply tests."""

from types import SimpleNamespace
from unittest.mock import MagicMock

from lvjiang.core.config.models import FontSizeConfig, UserConfig
from lvjiang.ui.main.menu_ops import MenuOpsMixin
from lvjiang.ui.settings_dialog import SettingsDialog


def test_font_tab_follows_system_parameters_and_loads_saved_values(
    qtbot, monkeypatch,
):
    config = UserConfig(font_sizes=FontSizeConfig(13, 15))
    monkeypatch.setattr(
        "lvjiang.ui.settings_dialog.load_user_config", lambda: config
    )

    dialog = SettingsDialog()
    qtbot.addWidget(dialog)
    labels = [dialog._tabs.tabText(i) for i in range(dialog._tabs.count())]

    assert labels.index("字体设置") == labels.index("系统参数") + 1
    assert dialog._overview_font_spin.value() == 13
    assert dialog._user_info_font_spin.value() == 15
    assert dialog._overview_font_spin.minimum() == 8
    assert dialog._overview_font_spin.maximum() == 24


def test_save_persists_and_emits_font_sizes_immediately(qtbot, monkeypatch):
    saved = {}
    monkeypatch.setattr(
        "lvjiang.ui.settings_dialog.load_user_config", UserConfig
    )
    monkeypatch.setattr(
        "lvjiang.ui.settings_dialog.save_settings", lambda values: saved.update(values)
    )
    monkeypatch.setattr(
        "lvjiang.ui.settings_dialog.save_app_config", lambda *_args: None
    )
    dialog = SettingsDialog()
    qtbot.addWidget(dialog)
    emitted = []
    dialog.font_sizes_saved.connect(emitted.append)
    dialog._collect_custom = lambda: {}
    dialog._collect_envs = lambda: []
    dialog._overview_font_spin.setValue(14)
    dialog._user_info_font_spin.setValue(16)

    dialog._on_save()

    expected = {"user_overview": 14, "user_info": 16}
    assert saved["font_sizes"] == expected
    assert emitted == [expected]


def test_main_window_applies_saved_font_sizes_to_both_pages():
    host = SimpleNamespace(
        _user_config=UserConfig(),
        _profile_tab=MagicMock(),
        _user_info_tab=MagicMock(),
    )

    MenuOpsMixin._apply_font_size_settings(
        host, {"user_overview": 17, "user_info": 19}
    )

    assert host._user_config.font_sizes == FontSizeConfig(17, 19)
    host._profile_tab.apply_content_font_size.assert_called_once_with(17)
    host._user_info_tab.apply_content_font_size.assert_called_once_with(19)
