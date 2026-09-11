"""调律 Tab 的两页结构与 PC 后台滚动参数持久化。"""

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWidgets import QLabel

from lvjiang.apps.yysls.ui.tuning.tuning_tab import TuningTab
from lvjiang.core.config.models import HotkeyConfig
from lvjiang.core.config.session import reset_session_store
from lvjiang.core.user_config import (
    User,
    get_user_workflow_params,
    save_user_metadata,
)


class _Users:
    def __init__(self, users_dir, names=None):
        self.users_dir = users_dir
        self.names = names or ["测试用户"]
        self.active = self.names[0]

    def list_users(self):
        return list(self.names)

    def get_active_user_name(self):
        return self.active


class _Host(QObject):
    automation_state_changed = pyqtSignal(str)
    user_changed = pyqtSignal(str)

    def __init__(self, users_dir, names=None):
        super().__init__()
        self.user_manager = _Users(users_dir, names)
        self._user_config = type("Config", (), {"hotkeys": HotkeyConfig()})()


def test_tuning_tab_has_rules_and_parameters_pages(qtbot, tmp_path, monkeypatch):
    import lvjiang.constants as constants_mod

    monkeypatch.setattr(constants_mod, "SESSION_PATH", tmp_path / "session.json")
    reset_session_store()
    users_dir = tmp_path / "users"
    save_user_metadata(User(name="测试用户"), users_dir)
    tab = TuningTab(_Host(users_dir))
    qtbot.addWidget(tab)

    assert tab._config_tabs.count() == 2
    assert [tab._config_tabs.tabText(i) for i in range(2)] == ["规则", "参数"]
    labels = {
        label.text()
        for label in tab._config_tabs.widget(1).findChildren(QLabel)
    }
    assert {"<b>调律部位：</b>", "<b>全局开关：</b>",
            "<b>调律设置：</b>", "<b>调试参数：</b>"} <= labels

    assert len(tab._tuning_globals._switch_cbs) == 2
    assert not any(
        cfg["enabled"] for cfg in tab._tuning_config.get_config().values())
    sub_weapon = next(
        cb for cb in tab._tuning_checkboxes
        if cb.objectName() == "sub_weapon")
    assert not sub_weapon.isChecked()
    assert not sub_weapon.isEnabled()
    assert not tab._pc_background_scroll_cb.isChecked()
    assert not tab._positional_traversal_cb.isChecked()
    assert tab._use_stone_cache_cb.isChecked()
    assert not tab._initial_stone_check_cb.isChecked()
    assert not tab._initial_stone_min.isEnabled()
    assert tab._initial_stone_min.value() == 0
    assert tab._initial_stone_min.text() == ""
    assert not tab._validate_stone_cache_cb.isChecked()
    tab._pc_background_scroll_cb.setChecked(True)
    tab._positional_traversal_cb.setChecked(True)
    tab._initial_stone_check_cb.setChecked(True)
    tab._initial_stone_min.setValue(120)
    tab._validate_stone_cache_cb.setChecked(True)
    saved = get_user_workflow_params("测试用户", "auto_tuning", users_dir)
    assert saved is not None
    assert saved["pc_background_scroll"] is True
    assert saved["scroll_strategy"] == "positional"
    assert saved["use_stone_cache"] is True
    assert saved["initial_stone_check_enabled"] is True
    assert saved["initial_stone_min_count"] == 120
    assert saved["validate_stone_cache"] is True

    tab._positional_traversal_cb.setChecked(False)
    saved = get_user_workflow_params("测试用户", "auto_tuning", users_dir)
    assert saved is not None and saved["scroll_strategy"] == ""
    reset_session_store()


def test_switching_execution_user_switches_tuning_config(
    qtbot, tmp_path, monkeypatch,
):
    import lvjiang.constants as constants_mod

    monkeypatch.setattr(constants_mod, "SESSION_PATH", tmp_path / "session.json")
    reset_session_store()
    users_dir = tmp_path / "users"
    for username in ("alice", "bob"):
        save_user_metadata(User(name=username), users_dir)
    host = _Host(users_dir, ["alice", "bob"])
    tab = TuningTab(host)
    qtbot.addWidget(tab)

    tab._pc_background_scroll_cb.setChecked(True)
    combo = tab._execution_user_selector.combo
    combo.setCurrentIndex(combo.findData("bob"))
    assert not tab._pc_background_scroll_cb.isChecked()
    tab._positional_traversal_cb.setChecked(True)

    alice = get_user_workflow_params("alice", "auto_tuning", users_dir)
    bob = get_user_workflow_params("bob", "auto_tuning", users_dir)
    assert alice is not None and alice["pc_background_scroll"] is True
    assert alice["scroll_strategy"] == ""
    assert bob is not None and bob["pc_background_scroll"] is False
    assert bob["scroll_strategy"] == "positional"
    reset_session_store()
