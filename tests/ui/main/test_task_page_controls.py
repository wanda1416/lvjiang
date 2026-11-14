"""任务页只能控制当前目标上由本页启动的任务，避免跨页误停。"""
from types import SimpleNamespace

from PyQt6.QtWidgets import QPushButton

from lvjiang.ui.batch.batch_tab import BatchTab
from lvjiang.ui.execution.hotkeys import hotkey_label
from lvjiang.ui.main.plugin_runtime import PluginRuntime
from lvjiang.ui.main.run_control import RunControlMixin
from lvjiang.workflows import implementations
from tests.ui.fake_plugin_page import FAKE_SCOPE, FakePluginPage


class _ExternalWorkflow:
    """模拟另一个（外置）插件的代码工作流：占用文案由类自己声明。"""

    RUNNING_LABEL = "外置任务运行中"


class _UndeclaredWorkflow:
    """没有声明运行文案的代码工作流：宿主回落到通用文案。"""


def test_task_pages_disable_foreign_run_controls_and_restore_after_finish(qapp, monkeypatch):
    monkeypatch.setitem(
        implementations._REGISTRY, FAKE_SCOPE, "tests.ui.fake_plugin_page.FakeWorkflow")
    monkeypatch.setitem(
        implementations._REGISTRY, "external_task", f"{__name__}._ExternalWorkflow")
    monkeypatch.setitem(
        implementations._REGISTRY, "undeclared_task", f"{__name__}._UndeclaredWorkflow")
    host = SimpleNamespace(
        _user_config=SimpleNamespace(hotkeys=SimpleNamespace(
            start="F9", stop="F10", pause="F11")),
        _current_run_context=None, _run_state="idle", _running=False,
        btn_run_workflow=QPushButton(), btn_pause_resume=QPushButton(),
        workflow_group_combo=QPushButton(), btn_refresh_workflows=QPushButton(),
        btn_load_workflow=QPushButton(),
        _hotkey_label=hotkey_label, _backend_ready=lambda: True,
        _plan_allows_backend=lambda: True, start_denied_label=lambda: "",
        automation_state_changed=SimpleNamespace(emit=lambda state: None),
        _emit_concurrency_changed=lambda: None,
        _sync_projected_context_locks=lambda: None,
        findChildren=lambda cls: [],
    )
    batch = SimpleNamespace(
        _host=host, _btn_run=QPushButton(), _btn_pause_resume=QPushButton())
    host.plugin_runtime = PluginRuntime(host)
    plugin = FakePluginPage(host)
    host._refresh_pause_button = lambda: RunControlMixin._refresh_pause_button(host)
    buttons = (
        (host.btn_run_workflow, host.btn_pause_resume),
        (batch._btn_run, batch._btn_pause_resume),
        (plugin.btn_run, plugin.btn_pause_resume),
    )
    clicks = []
    for start, pause in buttons:
        start.clicked.connect(lambda: clicks.append("stop"))
        pause.clicked.connect(lambda: clicks.append("pause"))

    def refresh(scope, state, owner):
        host._current_run_context = (
            SimpleNamespace(name="很长的具体任务名称" * 20, metadata={"execution_scope": scope})
            if scope else None)
        host._running = bool(scope)
        host._run_state = state
        RunControlMixin._refresh_run_button(host)
        for control in (host.workflow_group_combo, host.btn_refresh_workflows, host.btn_load_workflow):
            assert control.isEnabled() == (not bool(scope))
        BatchTab._refresh_run_button(batch, state)
        plugin.on_automation_state(state)
        for index, (start, pause) in enumerate(buttons):
            if scope and index != owner:
                assert start.text() == {
                    "daily": "日常任务运行中",
                    "batch": "批量任务运行中",
                    FAKE_SCOPE: "示例任务运行中",
                    "external_task": "外置任务运行中",
                    "undeclared_task": "任务运行中",
                }[scope]
                assert not start.isEnabled() and not pause.isEnabled()
                start.click()
                pause.click()
                assert not clicks
            elif scope:
                assert "停止" in start.text()
                assert start.isEnabled() == (state != "stopping")
                assert pause.isEnabled() == (state in ("running", "paused"))
            else:
                assert start.isEnabled() and not pause.isEnabled()

    refresh(FAKE_SCOPE, "running", 2)
    refresh(FAKE_SCOPE, "paused", 2)
    refresh(FAKE_SCOPE, "stopping", 2)
    refresh("batch", "running", 1)
    refresh("daily", "running", 0)
    refresh("external_task", "running", -1)
    refresh("undeclared_task", "running", -1)
    refresh("", "idle", -1)
