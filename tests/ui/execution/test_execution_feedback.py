"""阻止状态漂移、提示错页及空选择时误启动；不验证样式实现细节。"""
from types import SimpleNamespace

from PyQt6.QtWidgets import QPushButton, QTabWidget, QWidget

from lvjiang.core.batch.config import BatchConfigItem
from lvjiang.ui.batch.batch_tab import BatchTab
from lvjiang.ui.execution.feedback import (
    ExecutionFeedback,
    clear_start_failures,
    report_start_failure,
)
from lvjiang.ui.execution.hotkeys import hotkey_label
from lvjiang.ui.main.execution_access import guarded_launch
from lvjiang.ui.main.plugin_runtime import PluginRuntime
from lvjiang.ui.main.run_control import RunControlMixin
from tests.ui.fake_plugin_page import FAKE_SCOPE, FakePluginPage


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
        # 真实 BatchTab 开始前会把防抖中的生命周期参数落盘；本用例只验提示文案，
        # 假对象按"无需落盘"处理即可（否则要连 _params_save_timer 一起造假）。
        _flush_lifecycle_params=lambda *args, **kwargs: None,
    )
    plugin = FakePluginPage(host)
    return host, batch, plugin


def refresh(host, batch, plugin, state):
    RunControlMixin._refresh_run_button(host)
    BatchTab._refresh_run_button(batch, state)
    plugin.on_automation_state(state)


def test_host_and_plugin_pages_share_connection_feedback_and_recover_without_old_errors(qapp):
    host, batch, plugin = controls()
    buttons = [host.btn_run_workflow, batch._btn_run, plugin.btn_run]
    feedbacks = [host._daily_start_feedback, batch._start_feedback, plugin.execution_feedback]
    host._backend_ready = lambda: False
    refresh(host, batch, plugin, "not_ready")
    assert {button.text() for button in buttons} == {"未定位"}
    assert all(button.isEnabled() for button in buttons)
    assert all("定位" in feedback.text() for feedback in feedbacks)
    host._backend = "adb"
    refresh(host, batch, plugin, "not_ready")
    assert {button.text() for button in buttons} == {"未连接"}
    host._backend_ready = lambda: True
    refresh(host, batch, plugin, "idle")
    assert all(not feedback.text() for feedback in feedbacks)
    assert all("开始" in button.text() for button in buttons)
    host._plan_allows_backend = lambda: False
    refresh(host, batch, plugin, "plan_unsupported")
    assert {button.text() for button in buttons} == {"方案不支持"}
    assert len({feedback.text() for feedback in feedbacks}) == 1
    host._plan_allows_backend = lambda: True
    host.start_denied_label = lambda: "需激活 Lv1"
    refresh(host, batch, plugin, "start_denied")
    assert {button.text() for button in buttons} == {"需激活 Lv1"}


def test_empty_choices_are_explained_without_affecting_other_pages(qapp):
    host, batch, plugin = controls()
    host._workflow_configs = []
    host._get_selected_flow_config = lambda: None
    batch._script_order = []
    plugin.username = ""
    refresh(host, batch, plugin, "idle")
    assert "脚本" in host._daily_start_feedback.text()
    assert "勾选" in batch._start_feedback.text()
    assert "执行用户" in plugin.execution_feedback.text()
    batch._script_order = ["example"]
    BatchTab._refresh_run_button(batch, "idle")
    assert not batch._start_feedback.text()
    assert "脚本" in host._daily_start_feedback.text()
    assert "执行用户" in plugin.execution_feedback.text()
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
    page.execution_scope = FAKE_SCOPE
    page.execution_feedback = ExecutionFeedback(page)
    tabs.addTab(QWidget(), "其他页面")
    tabs.addTab(page, "示例插件")
    host = SimpleNamespace(_left_tabs=tabs, _daily_start_feedback=ExecutionFeedback())
    report_start_failure(host, "启动检查失败", FAKE_SCOPE)
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
    page.execution_scope = FAKE_SCOPE
    page.execution_feedback = ExecutionFeedback(page)
    tabs.addTab(page, "示例插件")
    messages = []
    host = SimpleNamespace(
        _left_tabs=tabs, _daily_start_feedback=ExecutionFeedback(), _running=False,
        _user_manager=SimpleNamespace(get_active_user_name=lambda: "example"),
        _session_manager=SimpleNamespace(_users_dir=tmp_path),
        log_text=SimpleNamespace(append=messages.append),
        append_log=messages.append,
    )

    def deny(*_args):
        raise AccessDeniedError("执行用户被占用")

    monkeypatch.setattr("lvjiang.ui.main.execution_access.acquire_user", deny)

    @guarded_launch
    def launch(self, impl_name, flow_name, configure):
        raise AssertionError("授权拒绝后不应执行启动")

    launch(host, impl_name=FAKE_SCOPE, flow_name="示例插件任务", configure=None)
    assert "被占用" in page.execution_feedback.text()
    assert not host._daily_start_feedback.text()
    assert messages
