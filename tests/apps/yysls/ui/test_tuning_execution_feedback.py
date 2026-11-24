"""调律页与日常、批量页共享执行反馈：状态漂移、错页提示、空选择误启动。

宿主契约由 ``tests/ui/execution/test_execution_feedback.py`` 用假插件页覆盖；
这里用真实的 ``TuningTab`` 验证调律页遵守同一契约。
"""
from types import SimpleNamespace

from PyQt6.QtWidgets import QPushButton

from lvjiang.apps.yysls.ui.tuning.tuning_tab import TuningTab
from lvjiang.core.batch.config import BatchConfigItem
from lvjiang.ui.batch.batch_tab import BatchTab
from lvjiang.ui.execution.feedback import ExecutionFeedback
from lvjiang.ui.execution.hotkeys import hotkey_label
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
        # 真实 BatchTab 开始前会把防抖中的生命周期参数落盘；本用例只验提示文案，
        # 假对象按"无需落盘"处理即可（否则要连 _params_save_timer 一起造假）。
        _flush_lifecycle_params=lambda *args, **kwargs: None,
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
