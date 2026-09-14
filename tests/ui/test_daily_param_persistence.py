"""日常页参数修改与批量任务共享配置的回归测试。"""

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QSpinBox,
    QWidget,
)

from lvjiang.core.config.session import reset_session_store
from lvjiang.core.config.wf_configs import get_wf_config, set_wf_config
from lvjiang.core.user_config import (
    UserConfigManager,
    get_user_workflow_params,
    set_user_workflow_params,
)
from lvjiang.ui.execution_user_selector import ExecutionUserSelector
from lvjiang.ui.main.run_control import RunControlMixin
from lvjiang.ui.main.ui_state import UiStateMixin


class _DailyHarness(QWidget, UiStateMixin):
    user_changed = pyqtSignal(str)
    _collect_flow_params = RunControlMixin._collect_flow_params
    _on_user_changed = RunControlMixin._on_user_changed

    def __init__(self, config):
        super().__init__()
        self._workflow_configs = [config]
        self.run_env = "desktop"
        self._displayed_script_id = config["id"]
        self._param_panel = QWidget(self)
        self._param_layout = QFormLayout(self._param_panel)
        self._workflow_note_label = QLabel(self)
        self._independent_params_checkbox = QCheckBox(self)
        self._independent_params_checkbox.setObjectName("user_independent_params")
        self._independent_params_checkbox.toggled.connect(
            self._on_independent_params_toggled)

    def _get_selected_flow_config(self):
        return self._workflow_configs[0]

    def _selected_run_env(self):
        return self.run_env


def test_parameter_panel_filters_by_env_and_preserves_hidden_values(
    qtbot, tmp_path, monkeypatch,
):
    import lvjiang.constants as constants_mod

    monkeypatch.setattr(constants_mod, "SESSION_PATH", tmp_path / "session.json")
    reset_session_store()
    config = {
        "id": "dual_env", "scope": "daily",
        "parameters": [
            {"name": "common", "type": "text", "default": "c"},
            {"name": "pc", "type": "text", "default": "p", "env": ["desktop"]},
            {"name": "phone", "type": "text", "default": "a", "env": ["android"]},
        ],
    }
    set_wf_config("dual_env", {"common": "C", "pc": "P", "phone": "A"})
    panel = _DailyHarness(config)
    qtbot.addWidget(panel)

    panel._rebuild_param_panel()
    assert panel._param_panel.findChild(QLineEdit, "common") is not None
    assert panel._param_panel.findChild(QLineEdit, "pc") is not None
    assert panel._param_panel.findChild(QLineEdit, "phone") is None
    panel._param_panel.findChild(QLineEdit, "pc").setText("PC")
    assert get_wf_config("dual_env") == {
        "common": "C", "pc": "PC", "phone": "A",
    }

    panel.run_env = "android"
    panel._rebuild_param_panel()
    assert panel._param_panel.findChild(QLineEdit, "pc") is None
    assert panel._param_panel.findChild(QLineEdit, "phone").text() == "A"
    reset_session_store()


def test_user_parameter_save_preserves_other_environment(
    qtbot, tmp_path, monkeypatch,
):
    import lvjiang.constants as constants_mod

    monkeypatch.setattr(constants_mod, "SESSION_PATH", tmp_path / "session.json")
    monkeypatch.setattr(constants_mod, "USERS_DIR", tmp_path / "users")
    reset_session_store()
    config = {
        "id": "dual_user", "scope": "daily",
        "parameters": [
            {"name": "pc", "type": "text", "env": ["desktop"]},
            {"name": "phone", "type": "text", "env": ["android"]},
        ],
    }
    panel = _DailyHarness(config)
    panel._user_manager = UserConfigManager()
    panel._daily_execution_user_selector = ExecutionUserSelector(panel._user_manager)
    username = panel._user_manager.get_active_user_name()
    set_user_workflow_params(
        username, "dual_user", {"pc": "P", "phone": "A"},
        panel._user_manager.users_dir,
    )
    qtbot.addWidget(panel)
    qtbot.addWidget(panel._daily_execution_user_selector)

    panel._rebuild_param_panel()
    panel._param_panel.findChild(QLineEdit, "pc").setText("PC")

    assert get_user_workflow_params(
        username, "dual_user", panel._user_manager.users_dir,
    ) == {"pc": "PC", "phone": "A"}
    reset_session_store()


def test_daily_parameter_changes_are_persisted_immediately(qtbot, tmp_path, monkeypatch):
    import lvjiang.constants as constants_mod

    monkeypatch.setattr(constants_mod, "SESSION_PATH", tmp_path / "session.json")
    reset_session_store()
    config = {
        "id": "redeem_code", "scope": "daily",
        "parameters": [{
            "name": "code", "type": "text", "multiline": True,
            "default": "", "placeholder": "每行一个兑换码",
        }],
    }
    panel = _DailyHarness(config)
    qtbot.addWidget(panel)
    panel._rebuild_param_panel()
    edit = panel._param_panel.findChild(QPlainTextEdit, "code")
    assert edit.placeholderText() == "每行一个兑换码"
    value = "ABC\n\n DEF "
    edit.setPlainText(value)
    assert get_wf_config("redeem_code") == {"code": value}
    assert RunControlMixin._collect_flow_params(panel) == {"code": value}
    panel._rebuild_param_panel()
    assert panel._param_panel.findChild(QPlainTextEdit, "code").toPlainText() == value
    reset_session_store()
    config = {
        "id": "daily_task",
        "scope": "daily",
        "parameters": [
            {"name": "count", "type": "number", "default": 1},
            {"name": "enabled", "type": "bool", "default": False},
            {
                "name": "mode",
                "type": "select",
                "default": "a",
                "options": [
                    {"label": "A", "value": "a"},
                    {"label": "B", "value": "b"},
                ],
            },
            {
                "name": "items",
                "type": "checkgroup",
                "default": {"x": True, "y": True},
                "options": ["x", "y"],
            },
            {"name": "code", "type": "text", "default": ""},
        ],
    }
    panel = _DailyHarness(config)
    qtbot.addWidget(panel)
    panel._rebuild_param_panel()

    panel._param_panel.findChild(QSpinBox, "count").setValue(7)
    panel._param_panel.findChild(QCheckBox, "enabled").setChecked(True)
    panel._param_panel.findChild(QComboBox, "mode").setCurrentIndex(1)
    panel._param_panel.findChild(QWidget, "items").findChild(
        QCheckBox, "y"
    ).setChecked(False)
    # 文本框同样即时落盘：批量执行读 wf_configs，等失焦才写会跑上一次的值
    panel._param_panel.findChild(QLineEdit, "code").setText("ABC123")

    assert get_wf_config("daily_task") == {
        "count": "7",
        "enabled": True,
        "mode": "b",
        "items": {"x": True, "y": False},
        "code": "ABC123",
    }
    reset_session_store()


def test_text_parameter_layout_separates_multiline_label(qtbot):
    config = {
        "id": "text_layout", "scope": "daily",
        "parameters": [
            {"name": "single", "type": "text", "label": "单行"},
            {"name": "codes", "type": "text", "label": "兑换码", "multiline": True},
        ],
    }
    panel = _DailyHarness(config)
    qtbot.addWidget(panel)
    panel._rebuild_param_panel()
    layout = panel._param_layout
    single = panel._param_panel.findChild(QLineEdit, "single")
    multiline = panel._param_panel.findChild(QPlainTextEdit, "codes")
    assert layout.getWidgetPosition(single) == (0, QFormLayout.ItemRole.FieldRole)
    assert layout.labelForField(single).text() == "单行:"
    assert layout.itemAt(1, QFormLayout.ItemRole.SpanningRole).widget().text() == "兑换码:"
    assert layout.getWidgetPosition(multiline) == (2, QFormLayout.ItemRole.SpanningRole)


class _RunHarness(QWidget):
    """只借 RunControlMixin 的参数收集方法，不装配整个运行控制页。"""

    from lvjiang.ui.main.run_control import RunControlMixin

    _collect_flow_params = RunControlMixin._collect_flow_params

    def __init__(self, config):
        super().__init__()
        self._config = config
        self._param_panel = QWidget(self)

    def _get_selected_flow_config(self):
        return self._config


def test_single_run_collects_text_parameter(qtbot):
    """单次运行读的是控件而不是 wf_configs，文本参数必须原样取到。

    兑换码走的就是这条路径：收不到就会拿着空串去粘贴。
    """
    config = {
        "id": "redeem_code",
        "scope": "daily",
        "parameters": [{"name": "code", "type": "text", "default": ""}],
    }
    harness = _RunHarness(config)
    qtbot.addWidget(harness)
    edit = QLineEdit(harness._param_panel)
    edit.setObjectName("code")
    edit.setText("ABC123")

    assert harness._collect_flow_params() == {"code": "ABC123"}


def test_multiline_parameter_persists_collects_and_restores(qtbot, tmp_path, monkeypatch):
    import lvjiang.constants as constants_mod

    monkeypatch.setattr(constants_mod, "SESSION_PATH", tmp_path / "session.json")
    reset_session_store()


def test_daily_user_override_is_saved_separately(qtbot, tmp_path, monkeypatch):
    import lvjiang.constants as constants_mod

    monkeypatch.setattr(constants_mod, "SESSION_PATH", tmp_path / "session.json")
    monkeypatch.setattr(constants_mod, "USERS_DIR", tmp_path / "users")
    reset_session_store()
    panel = _DailyHarness({
        "id": "daily_task", "scope": "daily",
        "parameters": [{"name": "count", "type": "number", "default": 1}],
    })
    panel._user_manager = UserConfigManager()
    panel._daily_execution_user_selector = ExecutionUserSelector(panel._user_manager)
    qtbot.addWidget(panel)
    qtbot.addWidget(panel._daily_execution_user_selector)
    panel._rebuild_param_panel()

    panel._independent_params_checkbox.setChecked(True)
    panel._param_panel.findChild(QSpinBox, "count").setValue(9)

    username = panel._user_manager.get_active_user_name()
    assert get_user_workflow_params(
        username, "daily_task", tmp_path / "users") == {"count": "9"}
    assert get_wf_config("daily_task") == {}
    reset_session_store()


def test_follow_current_user_reloads_parameter_context(qtbot, tmp_path, monkeypatch):
    import lvjiang.constants as constants_mod

    monkeypatch.setattr(constants_mod, "SESSION_PATH", tmp_path / "session.json")
    monkeypatch.setattr(constants_mod, "USERS_DIR", tmp_path / "users")
    reset_session_store()
    panel = _DailyHarness({
        "id": "follow_user_task", "scope": "daily",
        "parameters": [{"name": "count", "type": "number", "default": 1}],
    })
    panel._user_manager = UserConfigManager()
    first_user = panel._user_manager.get_active_user_name()
    assert panel._user_manager.create_user("bob")
    panel._daily_execution_user_selector = ExecutionUserSelector(panel._user_manager)
    panel.user_combo = QComboBox(panel)
    panel.user_combo.addItems(panel._user_manager.list_users())
    panel.user_combo.currentIndexChanged.connect(panel._on_user_changed)
    qtbot.addWidget(panel)
    qtbot.addWidget(panel._daily_execution_user_selector)
    panel._rebuild_param_panel()

    panel._independent_params_checkbox.setChecked(True)
    panel._param_panel.findChild(QSpinBox, "count").setValue(7)
    panel.user_combo.setCurrentText("bob")

    assert panel._displayed_param_username == "bob"
    assert panel._param_panel.findChild(QSpinBox, "count").value() == 1
    assert get_user_workflow_params(
        first_user, "follow_user_task", tmp_path / "users") == {"count": "7"}
    reset_session_store()


def test_deleted_override_user_is_not_written_during_refresh(
    qtbot, tmp_path, monkeypatch,
):
    import lvjiang.constants as constants_mod

    monkeypatch.setattr(constants_mod, "SESSION_PATH", tmp_path / "session.json")
    monkeypatch.setattr(constants_mod, "USERS_DIR", tmp_path / "users")
    reset_session_store()
    panel = _DailyHarness({
        "id": "deleted_user_task", "scope": "daily",
        "parameters": [{"name": "count", "type": "number", "default": 1}],
    })
    panel._user_manager = UserConfigManager()
    deleted_user = panel._user_manager.get_active_user_name()
    assert panel._user_manager.create_user("bob")
    panel._daily_execution_user_selector = ExecutionUserSelector(panel._user_manager)
    qtbot.addWidget(panel)
    qtbot.addWidget(panel._daily_execution_user_selector)
    panel._rebuild_param_panel()
    panel._independent_params_checkbox.setChecked(True)
    panel._param_panel.findChild(QSpinBox, "count").setValue(7)

    assert panel._user_manager.delete_user(deleted_user)
    preserved = (tmp_path / "users" / f"{deleted_user}.json").read_text(
        encoding="utf-8")
    panel._daily_execution_user_selector.refresh_users()
    panel._on_daily_execution_user_changed(
        panel._daily_execution_user_selector.resolve_username())

    assert panel._displayed_param_username == "bob"
    assert (tmp_path / "users" / f"{deleted_user}.json").read_text(
        encoding="utf-8") == preserved
    reset_session_store()
