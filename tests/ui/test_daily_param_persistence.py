"""日常页参数修改与批量任务共享配置的回归测试。"""

from PyQt6.QtWidgets import QCheckBox, QComboBox, QFormLayout, QLabel, QSpinBox, QWidget

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

    assert get_wf_config("daily_task") == {
        "count": "7",
        "enabled": True,
        "mode": "b",
        "items": {"x": True, "y": False},
    }
    reset_session_store()
