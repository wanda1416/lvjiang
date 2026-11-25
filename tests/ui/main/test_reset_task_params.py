"""日常页「恢复默认参数」：清掉保存过的参数，回到脚本预置值。

锁住两条真实会踩错的契约：

1. 共享模式清的是全局参数——所有未使用独立参数的用户一起回到预置值；
2. 独立模式不能只删该用户的独立参数：全局若被改过，删掉会回落到那份改过的
   全局值，用户点了「恢复默认」却看不到默认值，所以要把预置值写进他的独立层。
"""
from copy import deepcopy
from types import SimpleNamespace

import pytest
from PyQt6.QtWidgets import (
    QGroupBox,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QWidget,
)

from lvjiang.core.config.wf_configs import get_wf_config, set_wf_config
from lvjiang.core.users.document import (
    User,
    get_user_workflow_params,
    save_user_metadata,
    set_user_workflow_params,
)
from lvjiang.ui.execution.user_selector import ExecutionUserSelector
from lvjiang.ui.main.ui_state import UiStateMixin
from lvjiang.ui.widgets.combo_box import AutoWidthComboBox

PARAMETERS = [
    {"name": "count", "type": "number", "default": 3},
    {"name": "mode", "type": "select", "default": "fast", "options": ["fast", "full"]},
    {"name": "label", "type": "text", "default": ""},
]


class Host(QWidget, UiStateMixin):
    def __init__(self, users_dir):
        super().__init__()
        self._user_manager = SimpleNamespace(
            _users_dir=users_dir,
            list_users=lambda: ["alpha", "beta"],
            get_active_user_name=lambda: "alpha",
        )
        self._daily_execution_user_selector = ExecutionUserSelector(
            self._user_manager)
        self._daily_execution_user_selector.resolved_user_changed.connect(
            self._on_daily_execution_user_changed)
        self._workflow_configs = [
            {"id": "a", "name": "a", "parameters": deepcopy(PARAMETERS)}]
        self.workflow_combo = AutoWidthComboBox()
        for cfg in self._workflow_configs:
            self.workflow_combo.addItem(cfg["name"], cfg["id"])
        self._displayed_script_id = "a"
        self._param_panel = QGroupBox(self)
        self._workflow_note_label = QLabel()
        self._reset_params_button = QPushButton("恢复默认参数", self)
        self._reset_params_button.clicked.connect(self._on_reset_task_params)
        self._rebuild_param_panel()

    def _selected_run_env(self):
        return "desktop"

    def _get_selected_flow_config(self):
        selected_id = self.workflow_combo.currentData()
        return next(
            (cfg for cfg in self._workflow_configs if cfg["id"] == selected_id),
            None)

    def _refresh_daily_start_feedback(self):
        pass

    def _save_daily_config(self, **_kwargs):
        pass


@pytest.fixture
def host(qtbot, tmp_path, monkeypatch):
    users_dir = tmp_path / "users"
    for name in ("alpha", "beta"):
        save_user_metadata(User(name), users_dir)

    def forbid_discovery():
        raise AssertionError("恢复默认参数不应触发脚本发现或清理")

    monkeypatch.setattr(
        "lvjiang.workflows.discovery.discover_scripts", forbid_discovery)
    widget = Host(users_dir)
    qtbot.addWidget(widget)
    return widget


def count_field(host) -> QSpinBox:
    return host._param_panel.findChild(QSpinBox, "count")


def test_untouched_preset_keeps_button_disabled(host):
    """参数与预置值一致时按钮禁用：按了没有反应的操作不该亮着。"""
    assert not host._reset_params_button.isHidden()
    assert not host._reset_params_button.isEnabled()


def test_shared_reset_clears_global_params(host):
    """共享模式：清全局参数，面板回到预置值，其他键不受牵连。"""
    set_wf_config("a", {"count": "9"})
    set_wf_config("broken_script", {"valuable": "保留"})
    host._rebuild_param_panel()
    assert host._reset_params_button.isEnabled()
    assert count_field(host).value() == 9

    host._reset_params_button.click()

    assert get_wf_config("a") == {}
    assert get_wf_config("broken_script") == {"valuable": "保留"}
    assert count_field(host).value() == 3
    assert not host._reset_params_button.isEnabled()


def test_independent_reset_writes_preset_instead_of_falling_back_to_global(host):
    """独立模式：把预置值写进该用户的独立层，不跟到被改过的全局值。"""
    users_dir = host._user_manager._users_dir
    set_wf_config("a", {"count": "9"})
    set_user_workflow_params("alpha", "a", {"mode": "full"}, users_dir)
    set_user_workflow_params("beta", "a", {"count": "7"}, users_dir)
    host._rebuild_param_panel()
    assert host._reset_params_button.isEnabled()

    host._reset_params_button.click()

    assert get_user_workflow_params("alpha", "a", users_dir) == {
        "count": 3,
        "mode": "fast",
        "label": "",
    }
    assert count_field(host).value() == 3
    assert not host._reset_params_button.isEnabled()
    # 别的用户的独立参数、以及全局参数都不动
    assert get_user_workflow_params("beta", "a", users_dir) == {"count": "7"}
    assert get_wf_config("a") == {"count": "9"}


def test_editing_param_enables_button_without_rebuild(host):
    """刚改完还没落盘的编辑也算「已修改」，不必等面板重建按钮才亮。"""
    count_field(host).setValue(5)
    assert host._reset_params_button.isEnabled()
    count_field(host).setValue(3)
    assert not host._reset_params_button.isEnabled()


def test_pending_text_edit_is_discarded_instead_of_written_back(host):
    """还没落盘的文本编辑要跟着一起丢弃，不能被面板重建写回刚清掉的归属。"""
    set_wf_config("a", {"count": "9"})
    host._rebuild_param_panel()
    label = host._param_panel.findChild(QLineEdit, "label")
    label.setText("草稿")
    assert host._param_save_timer.isActive()

    host._reset_params_button.click()

    assert get_wf_config("a") == {}
    assert host._param_panel.findChild(QLineEdit, "label").text() == ""
