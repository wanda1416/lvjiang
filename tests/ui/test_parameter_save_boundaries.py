"""连续编辑不扫描脚本；切换与启动保存旧归属，不覆盖外部更新。"""
from copy import deepcopy
from types import SimpleNamespace

import pytest
from PyQt6.QtWidgets import QFormLayout, QGroupBox, QLabel, QLineEdit, QSpinBox, QWidget

from lvjiang.core.config.wf_configs import (
    get_wf_config,
    set_wf_config,
    update_wf_config,
)
from lvjiang.core.user_config import (
    User,
    get_user_workflow_params,
    save_user_metadata,
    set_user_workflow_params,
)
from lvjiang.ui.combo_box import AutoWidthComboBox
from lvjiang.ui.execution_feedback import ExecutionFeedback
from lvjiang.ui.execution_user_selector import ExecutionUserSelector
from lvjiang.ui.main.run_control import RunControlMixin
from lvjiang.ui.main.ui_state import UiStateMixin
from lvjiang.ui.main.window import MainWindow

PARAMETERS = [
    {"name": "text", "type": "text", "default": ""},
    {"name": "count", "type": "number", "default": 3},
    {"name": "mobile", "type": "text", "env": ["android"], "default": ""},
]


class Host(QWidget, UiStateMixin, RunControlMixin):
    _running = False

    def __init__(self, users_dir):
        super().__init__()
        self._user_manager = SimpleNamespace(
            _users_dir=users_dir, list_users=lambda: ["alpha", "beta"],
            get_active_user_name=lambda: "alpha")
        self._daily_execution_user_selector = ExecutionUserSelector(self._user_manager)
        self._daily_execution_user_selector.resolved_user_changed.connect(self._on_daily_execution_user_changed)
        self._workflow_configs = [
            {"id": sid, "name": sid, "parameters": deepcopy(PARAMETERS)} for sid in ("a", "b")]
        self.workflow_combo = AutoWidthComboBox()
        for cfg in self._workflow_configs:
            self.workflow_combo.addItem(cfg["name"], cfg["id"])
        self._displayed_script_id = "a"
        self._param_panel = QGroupBox(self)
        self._param_layout = QFormLayout(self._param_panel)
        self._workflow_note_label = QLabel()
        self._rebuild_param_panel()
        self.workflow_combo.currentIndexChanged.connect(self._on_workflow_combo_changed)

    def _selected_run_env(self):
        return "desktop"

    def _refresh_daily_start_feedback(self):
        pass

    def _save_daily_config(self):
        pass


@pytest.fixture
def host(qtbot, tmp_path, monkeypatch):
    users_dir = tmp_path / "users"
    for name in ("alpha", "beta"):
        save_user_metadata(User(name), users_dir)
    def forbid_discovery():
        raise AssertionError("参数编辑不应触发脚本发现或清理")
    monkeypatch.setattr("lvjiang.workflows.discovery.discover_scripts", forbid_discovery)
    widget = Host(users_dir)
    qtbot.addWidget(widget)
    return widget


def text_field(host):
    return host._param_panel.findChild(QLineEdit, "text")


def test_continuous_text_saves_once_and_preserves_external_fields(host, monkeypatch):
    set_wf_config("broken_script", {"valuable": "保留"})
    calls = []
    def record(sid, patch):
        calls.append(deepcopy(patch))
        return update_wf_config(sid, patch)
    monkeypatch.setattr("lvjiang.core.config.wf_configs.update_wf_config", record)
    field = text_field(host)
    for value in ("a", "ab", "abc"):
        field.setText(value)
    assert host._param_save_timer.isActive()
    assert calls == []
    update_wf_config("a", {"count": "9", "mobile": "另一个环境", "foreign": "外部修改"})
    host._param_save_timer.timeout.emit()
    assert calls == [{"text": "abc"}]
    assert not host._param_save_timer.isActive()
    assert get_wf_config("a") == {"text": "abc", "count": "9", "mobile": "另一个环境", "foreign": "外部修改"}
    assert get_wf_config("broken_script") == {"valuable": "保留"}
    host._save_displayed_params()
    assert len(calls) == 1


def test_script_and_user_switch_flush_old_context(host):
    text_field(host).setText("共享草稿")
    host.workflow_combo.setCurrentIndex(1)
    assert get_wf_config("a") == {"text": "共享草稿"}
    assert get_wf_config("b") == {}
    users_dir = host._user_manager._users_dir
    set_user_workflow_params("alpha", "b", {"text": "甲原值", "count": "4"}, users_dir)
    set_user_workflow_params("beta", "b", {"text": "乙原值"}, users_dir)
    host._rebuild_param_panel()
    text_field(host).setText("甲草稿")
    set_user_workflow_params("alpha", "b", {"text": "甲原值", "count": "8", "mobile": "保留"}, users_dir)
    combo = host._daily_execution_user_selector.combo
    combo.setCurrentIndex(combo.findData("beta"))
    assert get_user_workflow_params("alpha", "b", users_dir) == {"text": "甲草稿", "count": "8", "mobile": "保留"}
    assert text_field(host).text() == "乙原值"
    assert get_user_workflow_params("beta", "b", users_dir) == {"text": "乙原值"}


def test_text_dependency_updates_before_save_and_checkbox_flushes_immediately(host):
    from PyQt6.QtWidgets import QCheckBox
    host._workflow_configs[0]["parameters"] += [
        {"name": "detail", "type": "text", "default": "", "require": '$text == "show"'},
        {"name": "enabled", "type": "bool", "default": False},
    ]
    host._rebuild_param_panel()
    detail = host._param_panel.findChild(QLineEdit, "detail")
    assert detail.isHidden()
    text_field(host).setText("show")
    assert not detail.isHidden()
    assert get_wf_config("a") == {}
    host._param_panel.findChild(QCheckBox, "enabled").setChecked(True)
    assert get_wf_config("a") == {"text": "show", "enabled": True}
    assert not host._param_save_timer.isActive()


def test_numeric_input_and_immediate_batch_start_flush_before_validation(host):
    spin = host._param_panel.findChild(QSpinBox, "count")
    spin.lineEdit().setText("12")
    text_field(host).setText("批量最新参数")
    observations = []
    host._backend_ready = lambda: observations.append(get_wf_config("a")) or False
    host._active_execution_target = lambda: None
    host._log_append = lambda _message: None
    assert not MainWindow.run_batch(host, SimpleNamespace(scripts=[]))
    assert observations == [{"text": "批量最新参数", "count": "12"}]
    assert not host._param_save_timer.isActive()


def test_temporary_script_parameters_roundtrip_without_disk_write(host):
    cfg = {"id": "__loaded__:example", "name": "临时脚本", "parameters": deepcopy(PARAMETERS)}
    host._workflow_configs.append(cfg)
    host.workflow_combo.addItem(cfg["name"], cfg["id"])
    host.workflow_combo.setCurrentIndex(host.workflow_combo.findData(cfg["id"]))
    text_field(host).setText("临时内容")
    host.workflow_combo.setCurrentIndex(0)
    assert cfg["_saved_params"] == {"text": "临时内容"}
    assert get_wf_config(cfg["id"]) == {}
    host.workflow_combo.setCurrentIndex(host.workflow_combo.findData(cfg["id"]))
    assert text_field(host).text() == "临时内容"


def test_parameter_source_switch_flushes_shared_edit_before_copy(host):
    text_field(host).setText("切换前共享编辑")
    host._on_independent_params_toggled(True)
    assert get_wf_config("a") == {"text": "切换前共享编辑"}
    assert get_user_workflow_params("alpha", "a", host._user_manager._users_dir)["text"] == "切换前共享编辑"
    assert host._displayed_param_is_user_override


def test_save_failure_keeps_dirty_values_and_blocks_batch_until_retry(host, monkeypatch):
    host._daily_start_feedback = ExecutionFeedback(host)
    text_field(host).setText("待保存")
    def deny(*_args):
        raise PermissionError("配置文件不可写")
    with monkeypatch.context() as blocked:
        blocked.setattr("lvjiang.core.config.wf_configs.update_wf_config", deny)
        host._param_save_timer.timeout.emit()
        assert "保存失败" in host._daily_start_feedback.text()
        assert get_wf_config("a") == {}
        logs = []
        host._log_append = logs.append
        host._backend_ready = lambda: pytest.fail("保存失败时不应进入启动流程")
        assert not MainWindow.run_batch(host, SimpleNamespace(scripts=[]))
        assert "未启动" in logs[-1]
    host._save_displayed_params()
    assert get_wf_config("a") == {"text": "待保存"}
    assert not host._daily_start_feedback.text()


def test_projected_running_snapshot_never_overwrites_current_configuration(host):
    update_wf_config("a", {"text": "当前配置"})
    host._rebuild_param_panel()
    host._running = True
    host._apply_launch_draft_parameters({"text": "运行时历史快照"})
    host._param_panel.setEnabled(False)
    host._save_displayed_params()
    host._running = False
    host._param_panel.setEnabled(True)
    host._save_displayed_params()
    assert get_wf_config("a") == {"text": "当前配置"}
    text_field(host).setText("新的编辑")
    host._save_displayed_params()
    assert get_wf_config("a") == {"text": "新的编辑"}
