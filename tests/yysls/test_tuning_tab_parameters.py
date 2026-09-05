"""调律 Tab 的两页结构与 PC 后台滚动参数持久化。"""

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWidgets import QLabel

from lvjiang.apps.yysls.ui.tuning.tuning_tab import TuningTab
from lvjiang.core.config.models import HotkeyConfig
from lvjiang.core.config.session import reset_session_store
from lvjiang.core.config.wf_configs import get_wf_config


class _Users:
    def list_users(self):
        return ["测试用户"]

    def get_active_user_name(self):
        return "测试用户"


class _Host(QObject):
    automation_state_changed = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.user_manager = _Users()
        self._user_config = type("Config", (), {"hotkeys": HotkeyConfig()})()


def test_tuning_tab_has_rules_and_parameters_pages(qtbot, tmp_path, monkeypatch):
    import lvjiang.constants as constants_mod

    monkeypatch.setattr(constants_mod, "SESSION_PATH", tmp_path / "session.json")
    reset_session_store()
    tab = TuningTab(_Host())
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
    assert not tab._pc_background_scroll_cb.isChecked()
    assert not tab._positional_traversal_cb.isChecked()
    tab._pc_background_scroll_cb.setChecked(True)
    tab._positional_traversal_cb.setChecked(True)
    assert get_wf_config("auto_tuning")["pc_background_scroll"] is True
    assert get_wf_config("auto_tuning")["scroll_strategy"] == "positional"

    tab._positional_traversal_cb.setChecked(False)
    assert get_wf_config("auto_tuning")["scroll_strategy"] == ""
    reset_session_store()
