"""阻止状态漂移、提示错页及空选择时误启动；不验证样式实现细节。"""
from types import SimpleNamespace

from PyQt6.QtWidgets import QPushButton, QTabWidget, QWidget

from lvjiang.apps.yysls.ui.tuning.tuning_tab import TuningTab
from lvjiang.core.batch_config import BatchConfigItem
from lvjiang.ui.batch.batch_tab import BatchTab
from lvjiang.ui.execution_feedback import (
    ExecutionFeedback,
    clear_start_failures,
    report_start_failure,
)
from lvjiang.ui.hotkeys import hotkey_label
from lvjiang.ui.main.execution_access import guarded_launch
from lvjiang.ui.main.plugin_runtime import PluginRuntime
from lvjiang.ui.main.run_control import RunControlMixin


def controls():
    host = SimpleNamespace(
        _user_config=SimpleNamespace(hotkeys=SimpleNamespace(start="F9", stop="F10", pause="F11")),
        _running=False, is_running=False, _run_state="idle", _current_run_context=None,
        _backend="desktop", _backend_ready=lambda: True, _plan_allows_backend=lambda: True,
        _current_execution_target=lambda: SimpleNamespace(kind="windows"),
        start_denied_label=lambda: "", _hotkey_label=hotkey_label,
        btn_run_workflow=QPushButton(), btn_pause_resume=QPushButton(),
        _daily_start_feedback=ExecutionFeedback(),
        automation_state_changed=SimpleNamespace(emit=lambda _state: None),
        _emit_concurrency_changed=lambda: None, _sync_projected_context_locks=lambda: None,
        findChildren=lambda _kind: [],
    )
    host.plugin_runtime = PluginRuntime(host)
    host._refresh_pause_button = lambda: RunControlMixin._refresh_pause_button(host)
    batch = SimpleNamespace(
        _host=host, _btn_run=QPushButton(), _btn_pause_resume=QPushButton(),
        _start_feedback=ExecutionFeedback(), _item=BatchConfigItem(name="测试配置"),
        _script_order=["example"], _user_order=["example"],
    )
    tuning = SimpleNamespace(
        _host=host, btn_run_tuning=QPushButton(), btn_pause_resume=QPushButton(),
        _status_label=ExecutionFeedback(), _find_progress_widget=lambda: None,
        _execution_user_selector=SimpleNamespace(resolve_username=lambda: "example"),
    )
    return host, batch, tuning


def refresh(host, batch, tuning, state):
    RunControlMixin._refresh_run_button(host)
    BatchTab._refresh_run_button(batch, state)
    TuningTab._on_automation_state(tuning, state)


def test_three_pages_share_connection_feedback_and_recover_without_old_errors(qapp):
    host, batch, tuning = controls()
    buttons = [host.btn_run_workflow, batch._btn_run, tuning.btn_run_tuning]
    feedbacks = [host._daily_start_feedback, batch._start_feedback, tuning._status_label]
    host._backend_ready = lambda: False
    refresh(host, batch, tuning, "not_ready")
    assert {button.text() for button in buttons} == {"未定位"}
    assert all(button.isEnabled() for button in buttons)
    assert all("定位" in feedback.text() for feedback in feedbacks)
    host._backend = "adb"
    refresh(host, batch, tuning, "not_ready")
    assert {button.text() for button in buttons} == {"未连接"}
    host._backend_ready = lambda: True
    refresh(host, batch, tuning, "idle")
    assert all(not feedback.text() for feedback in feedbacks)
    assert all("开始" in button.text() for button in buttons)
    host._plan_allows_backend = lambda: False
    refresh(host, batch, tuning, "plan_unsupported")
    assert {button.text() for button in buttons} == {"方案不支持"}
    assert len({feedback.text() for feedback in feedbacks}) == 1
    host._plan_allows_backend = lambda: True
    host.start_denied_label = lambda: "需激活 Lv1"
    refresh(host, batch, tuning, "start_denied")
    assert {button.text() for button in buttons} == {"需激活 Lv1"}


def test_empty_choices_are_explained_without_affecting_other_pages(qapp):
    host, batch, tuning = controls()
    host._workflow_configs = []
    host._get_selected_flow_config = lambda: None
    batch._script_order = []
    tuning._execution_user_selector.resolve_username = lambda: ""
    refresh(host, batch, tuning, "idle")
    assert "脚本" in host._daily_start_feedback.text()
    assert "勾选" in batch._start_feedback.text()
    assert "执行用户" in tuning._status_label.text()
    batch._script_order = ["example"]
    BatchTab._refresh_run_button(batch, "idle")
    assert not batch._start_feedback.text()
    assert "脚本" in host._daily_start_feedback.text()
    assert "执行用户" in tuning._status_label.text()
    messages = []
    batch._host.append_log = messages.append
    batch._get_enabled_usernames = lambda: []
    batch._checked_scripts = lambda: []
    batch._show_start_error = lambda message: BatchTab._show_start_error(batch, message)
    BatchTab._start_batch(batch)
    assert "单元" in batch._start_feedback.text()
    assert messages


def test_failure_is_routed_to_owner_and_survives_availability_refresh(qtbot):
    tabs = QTabWidget()
    qtbot.addWidget(tabs)
    page = QWidget()
    page.execution_scope = "auto_tuning"
    page.execution_feedback = ExecutionFeedback(page)
    tabs.addTab(QWidget(), "其他页面")
    tabs.addTab(page, "调律")
    host = SimpleNamespace(_left_tabs=tabs, _daily_start_feedback=ExecutionFeedback())
    report_start_failure(host, "启动检查失败", "auto_tuning")
    assert page.execution_feedback.text() == "启动检查失败"
    assert not host._daily_start_feedback.text()
    page.execution_feedback.set_reason("请选择有效用户")
    assert page.execution_feedback.text() == "启动检查失败"
    clear_start_failures(host)
    assert page.execution_feedback.text() == "请选择有效用户"


def test_keyword_plugin_launch_rejection_reports_to_owner(qtbot, monkeypatch, tmp_path):
    from lvjiang.core.access import AccessDeniedError
    tabs = QTabWidget()
    qtbot.addWidget(tabs)
    page = QWidget()
    page.execution_scope = "auto_tuning"
    page.execution_feedback = ExecutionFeedback(page)
    tabs.addTab(page, "调律")
    messages = []
    host = SimpleNamespace(
        _left_tabs=tabs, _daily_start_feedback=ExecutionFeedback(), _running=False,
        _user_manager=SimpleNamespace(get_active_user_name=lambda: "example"),
        _session_manager=SimpleNamespace(_users_dir=tmp_path),
        log_text=SimpleNamespace(append=messages.append),
    )

    def deny(*_args):
        raise AccessDeniedError("执行用户被占用")

    monkeypatch.setattr("lvjiang.ui.main.execution_access.acquire_user", deny)

    @guarded_launch
    def launch(self, impl_name, flow_name, configure):
        raise AssertionError("授权拒绝后不应执行启动")

    launch(host, impl_name="auto_tuning", flow_name="调律", configure=None)
    assert "被占用" in page.execution_feedback.text()
    assert not host._daily_start_feedback.text()
    assert messages
