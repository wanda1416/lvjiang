"""日常页参数修改与批量任务共享配置的回归测试。"""

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
from lvjiang.core.config.wf_configs import get_wf_config
from lvjiang.ui.main.ui_state import UiStateMixin


class _DailyHarness(QWidget, UiStateMixin):
    def __init__(self, config):
        super().__init__()
        self._workflow_configs = [config]
        self._displayed_script_id = config["id"]
        self._param_panel = QWidget(self)
        self._param_layout = QFormLayout(self._param_panel)
        self._workflow_note_label = QLabel(self)

    def _get_selected_flow_config(self):
        return self._workflow_configs[0]


def test_daily_parameter_changes_are_persisted_immediately(qtbot, tmp_path, monkeypatch):
    import lvjiang.constants as constants_mod

    monkeypatch.setattr(constants_mod, "SESSION_PATH", tmp_path / "session.json")
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
    from lvjiang.ui.main.run_control import RunControlMixin
    assert RunControlMixin._collect_flow_params(panel) == {"code": value}
    panel._rebuild_param_panel()
    assert panel._param_panel.findChild(QPlainTextEdit, "code").toPlainText() == value
    reset_session_store()
