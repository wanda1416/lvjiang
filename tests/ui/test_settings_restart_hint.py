"""改语言并保存后，「重启应用后生效」提示必须用**新**语言显示。

语言词表在启动时加载，保存后进程仍跑在旧语言下。提示若走 ``tr()``，用户刚把
界面换成英文，看到的却是自己正要离开的那种语言。同时，没改语言时不能出现这行
提示，改回当前语言后必须收起。
"""

import pytest
from PyQt6.QtWidgets import QLabel, QPushButton, QTabWidget

from lvjiang.core.config import UserConfig
from lvjiang.ui import settings_dialog
from lvjiang.ui.combo_box import AutoWidthComboBox

ENGLISH_HINT = "Takes effect after restarting the app"


@pytest.fixture
def dialog(qtbot, monkeypatch):
    """只搭出提示相关的最小控件，不构建整页设置。

    进程语言固定为 zh_CN，模拟「界面还是中文、用户刚选了英文」这一刻。
    """
    monkeypatch.setattr(settings_dialog, "load_user_config", lambda: UserConfig())
    monkeypatch.setattr("lvjiang.i18n.current_language", lambda: "zh_CN")

    def setup(self):
        self._tabs = QTabWidget()
        self._tabs.addTab(QLabel("page"), "page")
        self._tabs.addTab(QLabel("ai"), "ai")
        self._ai_tab_index = 1
        self._privacy_tab_index = 2
        self._lang_combo = AutoWidthComboBox()
        for code in ("zh_CN", "en_US"):
            self._lang_combo.addItem(code, code)
        self._save_btn = QPushButton("save")
        self._restart_hint = QLabel()
        self._restart_hint.setVisible(False)
        self._refresh_save_button_visibility(self._tabs.currentIndex())

    monkeypatch.setattr(settings_dialog.SettingsDialog, "_setup_ui", setup)
    monkeypatch.setattr(
        settings_dialog.SettingsDialog, "_connect_dirty_signals", lambda self: None)
    widget = settings_dialog.SettingsDialog()
    qtbot.addWidget(widget)
    return widget


def test_no_hint_when_language_unchanged(dialog):
    dialog._lang_combo.setCurrentIndex(dialog._lang_combo.findData("zh_CN"))
    dialog._update_restart_hint()
    assert dialog._restart_hint.isHidden()
    assert dialog._restart_hint.text() == ""


def test_hint_uses_target_language(dialog):
    """选了英文就必须显示英文——这是整条提示存在的意义。"""
    dialog._lang_combo.setCurrentIndex(dialog._lang_combo.findData("en_US"))
    dialog._update_restart_hint()
    assert not dialog._restart_hint.isHidden()
    assert dialog._restart_hint.text() == ENGLISH_HINT


def test_hint_hides_when_language_switched_back(dialog):
    dialog._lang_combo.setCurrentIndex(dialog._lang_combo.findData("en_US"))
    dialog._update_restart_hint()
    assert not dialog._restart_hint.isHidden()

    dialog._lang_combo.setCurrentIndex(dialog._lang_combo.findData("zh_CN"))
    dialog._update_restart_hint()
    assert dialog._restart_hint.isHidden()


def test_hint_follows_save_button_visibility(dialog):
    """AI 页隐藏保存按钮，提示不能一个人留在那儿。"""
    dialog._lang_combo.setCurrentIndex(dialog._lang_combo.findData("en_US"))
    dialog._refresh_save_button_visibility(dialog._ai_tab_index)
    assert dialog._restart_hint.isHidden()

    dialog._refresh_save_button_visibility(0)
    assert not dialog._restart_hint.isHidden()
