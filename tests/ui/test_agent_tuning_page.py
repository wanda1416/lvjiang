"""Changing the Agent UI context must not mix users' generated plans."""
from types import SimpleNamespace

import pytest
from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QWidget

from lvjiang.apps.yysls.ui.tuning.agent_page import AgentTuningPage


class Host(QWidget):
    user_changed = pyqtSignal(str)

    @property
    def plugin_runtime(self):
        from lvjiang.ui.main.plugin_runtime import PluginRuntime
        return PluginRuntime(self)


def test_page_service_connection_survives_user_changes_without_packaging(qtbot, tmp_path, monkeypatch, free_tcp_port):
    import asyncio
    import json
    import shutil
    from pathlib import Path

    from lvjiang import constants
    from lvjiang.apps.yysls.ui.tuning import agent_page

    root = Path(__file__).parents[2]
    from lvjiang.core.agent_docs import DOCUMENT_DIRECTORIES
    for directory in DOCUMENT_DIRECTORIES:
        shutil.copytree(root / "docs" / directory, tmp_path / "docs" / directory)
    monkeypatch.setattr(constants, "PROJECT_ROOT", tmp_path)
    from lvjiang.core import agent_connection
    monkeypatch.setattr(agent_connection, "settings_path", lambda _root: tmp_path / "mcp-settings.json")
    from lvjiang.core.agent_settings import AgentSettings, AgentTarget
    monkeypatch.setattr(agent_page, "load_agent_settings", lambda: AgentSettings((
        AgentTarget("example", "Agent 示例", str(tmp_path / "workbuddy/mcp.json")),)))
    licensed = [True]
    monkeypatch.setattr(agent_page, "has_agent_access", lambda: licensed[0])
    host = Host()
    active = ["user_a"]
    host.user_manager = SimpleNamespace(list_users=lambda: ["user_a", "user_b"],
                                        get_active_user_name=lambda: active[0])
    target = SimpleNamespace(id="target_a", display_name="执行目标", kind="window", ready=True)
    host._execution_targets = SimpleNamespace(all=lambda: [target], active_target_id=target.id)
    host._run_manager = SimpleNamespace(run_for_target=lambda _id: None)
    page = AgentTuningPage(host)
    qtbot.addWidget(host)
    qtbot.addWidget(page)
    assert not page.export.isEnabled()
    assert not page.stop_service.isEnabled()
    page.port.setValue(free_tcp_port)
    page._start_service()
    assert page.server is not None, page.status.text()
    try:
        assert page.export.isEnabled()
        connection = page.server.connection_config()
        assert page.service.get_capabilities()["current_user"] == "user_a"
        assert page.service.list_users()["users"] == ["user_a", "user_b"]
        resource = list(asyncio.run(page.server.mcp.read_resource("lvjiang://docs/tool-schema")))[0]
        tools = json.loads(resource.content)
        assert "list_users" in {tool["name"] for tool in tools}
        dsl_resource = list(asyncio.run(page.server.mcp.read_resource(
            "lvjiang://docs/30-architecture:32-grammar:01-basics")))[0]
        assert "语法" in dsl_resource.content

        active[0] = "user_b"
        host.user_changed.emit("user_b")
        assert page.service.get_capabilities()["current_user"] == "user_b"
        assert page.server.connection_config() == connection
        assert page.service.list_plans("user_a")["plans"]
        assert page.service.list_plans("user_b")["plans"]
        licensed[0] = False
        page._refresh_state()
        assert not page.start_service.isEnabled()
        assert page.stop_service.isEnabled()
        assert "Lv1" in page.entitlement.text()
        page._stop_service()
        with pytest.raises(PermissionError, match="服务已关闭"):
            page.service.list_plans("user_b")
        page._start_service()
        assert "Lv1" in page.status.text()
    finally:
        page._shutdown()
        page.server._thread.join(timeout=5)
        assert not page.server.running


@pytest.mark.parametrize("stopped, expected", [(False, "failed"), (True, "interrupted")])
def test_scan_failure_and_user_stop_have_distinct_terminal_states(stopped, expected):
    import threading

    from lvjiang.apps.yysls.ui.tuning.agent_page import AgentTaskBridge
    from lvjiang.ui.main.run_control import RunControlMixin, WorkflowWorker

    worker = WorkflowWorker("scan_all_loadouts", lambda: None)
    worker.result_or_exception = {}
    stop = threading.Event()
    if stopped:
        stop.set()
    run = SimpleNamespace(worker=worker, engine=SimpleNamespace(return_value=-1),
                          stop_event=stop, metadata={})
    saved = []
    host = SimpleNamespace(
        _run_manager=SimpleNamespace(run=lambda _id: run), log_text=[],
        _save_workflow_result=lambda *_args, **_kwargs: None,
        _finish_task_run=lambda *_args, **kwargs: saved.append(kwargs),
        _auto_save_session=lambda *_args: pytest.fail("Neither path may save a partial session"),
    )
    RunControlMixin._on_workflow_finished.__wrapped__(host, "test_run")
    assert run.metadata["terminal_state"] == expected
    assert saved[0]["status"] == expected
    feedback = AgentTaskBridge._feedback(run)
    if stopped:
        assert "error" not in feedback
    else:
        assert feedback["error"] == saved[0]["error_message"]
        assert "扫描失败" in feedback["error"]


def test_bridge_selects_requested_connected_target_and_reports_busy_at_call_time(qtbot, monkeypatch):
    from lvjiang.apps.yysls.core.agent import launch
    from lvjiang.apps.yysls.ui.tuning.agent_page import AgentTaskBridge
    from lvjiang.apps.yysls.ui.tuning.tuning_tab import TuningTab

    host = Host()
    qtbot.addWidget(host)
    host.user_manager = SimpleNamespace(get_active_user_name=lambda: "user_a", list_users=lambda: ["user_a", "user_b"])
    targets = {key: SimpleNamespace(id=key, display_name=key, kind="adb", ready=True)
               for key in ("target_a", "target_b")}
    registry = SimpleNamespace(active_target_id="target_a", get=targets.get, all=lambda: list(targets.values()))
    registry.select = lambda key: setattr(registry, "active_target_id", key)
    host._execution_targets = registry
    busy = [True]
    host._run_manager = SimpleNamespace(
        can_start=lambda **_args: SimpleNamespace(allowed=not busy[0], reason="执行目标正在运行任务"),
        run_for_target=lambda key: object() if busy[0] and key == "target_b" else None)
    preserved = []
    host._capture_launch_draft = preserved.append
    host._restore_active_target_view = lambda: None
    host._backend_ready = lambda: True
    host._plan_allows_backend = lambda: True
    monkeypatch.setattr(launch, "prepare_tuning", lambda _config: None)
    monkeypatch.setattr(TuningTab, "_missing_tuning_output_fields", lambda: [])
    bridge = AgentTaskBridge(host)
    assert len(bridge.call("targets", {})["targets"]) == 2
    with pytest.raises(ValueError, match="正在运行"):
        bridge.call("validate", {"user": "user_b", "target_id": "target_b", "config": {}})
    assert registry.active_target_id == "target_a"
    assert not preserved
    busy[0] = False
    assert bridge.call("validate", {"user": "user_b", "target_id": "target_b", "config": {}})["ready"]
    assert registry.active_target_id == "target_b"
    assert preserved == ["target_a"]
    assert host.user_manager.get_active_user_name() == "user_a"
    targets["target_b"].ready = False
    with pytest.raises(ValueError, match="未连接"):
        bridge.call("resolve_target", {"target_id": "target_b"})
    bridge.close()


def test_menu_dialog_autostarts_persisted_service_and_close_keeps_it_running(qtbot, tmp_path, monkeypatch, free_tcp_port):
    from lvjiang import constants
    from lvjiang.apps.yysls.ui.tuning import agent_page
    from lvjiang.core import access, agent_connection
    from lvjiang.core.agent_connection import AgentConnection

    monkeypatch.setattr(constants, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(agent_connection, "settings_path", lambda _root: tmp_path / "connection.json")
    from lvjiang.core.agent_settings import AgentSettings, AgentTarget
    monkeypatch.setattr(agent_page, "load_agent_settings", lambda: AgentSettings((
        AgentTarget("example", "Agent 示例", str(tmp_path / "workbuddy.json")),)))
    monkeypatch.setattr(agent_page, "has_agent_access", lambda: True)
    monkeypatch.setattr(agent_page.AgentDocuments, "list_docs", lambda _self: {"documents": []})
    setting = AgentConnection(port=free_tcp_port, enabled=True)
    setting.save(tmp_path)
    host = Host()
    qtbot.addWidget(host)
    dialog = agent_page.AgentTuningDialog(host)
    qtbot.addWidget(dialog)
    page = dialog.page
    try:
        qtbot.waitUntil(lambda: page.server is not None and page.server.running)
        first = page.server.connection_config()
        assert (tmp_path / "workbuddy.json").is_file()
        dialog.show()
        dialog.close()
        assert page.server.running
        page._shutdown()
        page.server._thread.join(timeout=5)
        assert AgentConnection.load(tmp_path).enabled
        again = agent_page.AgentTuningDialog(host)
        qtbot.addWidget(again)
        page = again.page
        qtbot.waitUntil(lambda: page.server is not None and page.server.running)
        assert page.server.connection_config() == first
        page._stop_service()
        assert not AgentConnection.load(tmp_path).enabled
        monkeypatch.setattr(access, "is_readonly", lambda: True)
        monkeypatch.setattr(agent_page, "is_readonly", lambda: True)
        readonly = agent_page.AgentTuningDialog(host)
        qtbot.addWidget(readonly)
        readonly.page._start_service()
        assert readonly.page.server is None
        assert not readonly.page.start_service.isEnabled()
    finally:
        page._shutdown()
        if page.server:
            page.server._thread.join(timeout=5)


def test_start_retry_returns_original_task_before_busy_or_offline_checks(qtbot, tmp_path, monkeypatch):
    from PyQt6.QtCore import QObject

    from lvjiang.apps.yysls.core.agent.service import AgentService
    from lvjiang.apps.yysls.ui.tuning import tuning_tab
    from lvjiang.apps.yysls.ui.tuning.agent_page import AgentTaskBridge

    class Worker(QObject):
        finished = pyqtSignal()

        def isRunning(self):
            return True

    worker = Worker()
    run = SimpleNamespace(task_run_id='task-one', state='running', worker=worker)
    launches = []
    target = SimpleNamespace(id='target-one', display_name='target-one', kind='adb', ready=True)
    host = Host()
    qtbot.addWidget(host)
    host.user_manager = SimpleNamespace(list_users=lambda: ['test_user'], get_active_user_name=lambda: 'test_user')
    host._execution_targets = SimpleNamespace(get=lambda _id: target, active_target_id=target.id)
    host._run_manager = SimpleNamespace(run=lambda _id: run if launches else None,
                                       run_for_target=lambda _id: run if launches else None)
    host._current_run_context = None

    class Tab(QWidget):
        def _start_tuning(self, **kwargs):
            launches.append(kwargs)
            host._current_run_context = run

    page = Tab()
    qtbot.addWidget(page)
    monkeypatch.setattr(tuning_tab, 'TuningTab', Tab)
    host._left_tabs = SimpleNamespace(count=lambda: 1, widget=lambda _index: page)
    bridge = AgentTaskBridge(host)
    service = AgentService(tmp_path, dispatch=bridge.call)
    bridge.service = service
    service.set_enabled(True)
    validations = []

    def validate(*args):
        validations.append(args)
        return {'ready': True, 'target_id': target.id, 'config': {}, 'smart_state': {}}

    monkeypatch.setattr(service, 'validate_auto_tuning', validate)
    first = service.start_auto_tuning('test_user', 'result-one', 'request-one')
    target.ready = False
    host._execution_targets.active_target_id = 'different-target'
    assert service.start_auto_tuning('test_user', 'result-one', 'request-one') == first
    assert len(launches) == len(validations) == 1
    with pytest.raises(ValueError, match='不同内容'):
        service.start_auto_tuning('test_user', 'result-two', 'request-one')
    # A new bridge simulates reconnection/restart: persistent requests also avoid replay.
    restored = AgentTaskBridge(host)
    restored.service = service
    service.dispatch = restored.call
    assert service.start_auto_tuning('test_user', 'result-one', 'request-one')['task_id'] == first['task_id']
    assert len(launches) == 1
    monkeypatch.setattr(service, 'validate_auto_tuning', lambda *_args: {
        'ready': False, 'smart_tuning': {'reason': '指定方案未就绪',
                                        'plans': [{'reason': '缺少基础属性'}]}})
    with pytest.raises(ValueError, match='缺少基础属性'):
        service.start_auto_tuning('test_user', 'result-one', 'new-request')
    assert len(launches) == 1


def test_generated_result_ui_starts_selected_user_result_without_saving_defaults(qtbot, monkeypatch):
    from lvjiang.apps.yysls.ui.tuning import agent_results

    monkeypatch.setattr(agent_results, 'has_agent_access', lambda: True)
    started = []
    records = {user: {'id': user + '-result', 'user': user, 'name': '生成方案',
                      'created_at': '2026-10-11', 'goal': '培养首饰', 'plan_ids': ['p'],
                      'plan_targets': {'p': {'playstyle': '目标玩法'}}, 'suggestions': []}
               for user in ('user_a', 'user_b')}
    service = SimpleNamespace(
        _ensure_enabled=lambda: None, list_users=lambda: {'users': list(records)},
        list_generated_tuning=lambda user: {'results': [records[user]]},
        prepare_generated_tuning=lambda user, result_id: {
            'ready': True, 'smart_state': {'plans': {'p': {'name': '备战方案', 'playstyle': '目标玩法'}}},
            'smart_tuning': {'required': True, 'plans': [{'plan_id': 'p', 'status': 'ready'}]}},
        start_auto_tuning=lambda *args: started.append(args) or {'task_id': 'task'})
    dialog = agent_results.AgentResultsDialog(service)
    qtbot.addWidget(dialog)
    dialog.refresh()
    dialog.results.setCurrentIndex(1)
    assert '目标玩法' in dialog.details.toPlainText()
    assert not started
    dialog._start()
    assert started[0][:2] == ('user_b', 'user_b-result')
    assert records['user_b']['plan_targets']['p']['playstyle'] == '目标玩法'
    monkeypatch.setattr(agent_results, 'has_agent_access', lambda: False)
    dialog._start()
    assert len(started) == 1
    assert 'Lv1' in dialog.status.text()


def test_generated_result_preview_uses_evaluator_scope_and_preserves_refresh_errors(qtbot, monkeypatch):
    from lvjiang.apps.yysls.ui.tuning import agent_results

    monkeypatch.setattr(agent_results, 'has_agent_access', lambda: True)
    record = {'id': 'result', 'user': 'test_user', 'name': '生成方案',
              'created_at': '2026-10-11', 'goal': '培养首饰', 'plan_ids': [], 'suggestions': []}
    prepared = {
        'ready': True,
        'smart_state': {'plans': {
            'pve': {'name': '可用方案', 'playstyle': '目标玩法'},
            'pvp': {'name': '竞技方案'},
            'other': {'name': '其他流派方案'},
        }},
        'smart_tuning': {'required': True, 'plans': [
            {'plan_id': 'pve', 'status': 'ready'},
            {'plan_id': 'pvp', 'status': 'skipped', 'plan_name': '竞技方案',
             'rule_name': '首饰规则', 'reason': 'PVP 方案不参与智能调律'},
        ]},
    }
    service = SimpleNamespace(
        _ensure_enabled=lambda: None, list_users=lambda: {'users': ['test_user']},
        list_generated_tuning=lambda user: {'results': [record]},
        prepare_generated_tuning=lambda *_args: prepared)
    dialog = agent_results.AgentResultsDialog(service)
    qtbot.addWidget(dialog)
    dialog.refresh()
    participating, excluded = dialog.details.toPlainText().split('未参与的方案或规则及原因：')
    assert '可用方案' in participating and '目标玩法' in participating
    assert '竞技方案' not in participating and '其他流派方案' not in participating
    assert 'PVP 方案不参与智能调律' in excluded
    assert '其他流派方案：未匹配本次规则玩法' in excluded
    assert dialog.start.isEnabled()

    def unreadable(*_args):
        raise ValueError('备战数据无法读取')

    service.prepare_generated_tuning = unreadable
    dialog.refresh()
    assert dialog.status.text() == '备战数据无法读取'
    assert not dialog.details.toPlainText()
    assert not dialog.start.isEnabled()
    # A successful retry clears the old error, not the newly raised one.
    service.prepare_generated_tuning = lambda *_args: prepared
    dialog.refresh()
    assert not dialog.status.text()
    assert dialog.start.isEnabled()
    # Explicitly ordinary tuning must not pretend any plan gets smart protection.
    prepared['smart_tuning'] = {'required': False, 'plans': []}
    dialog.refresh()
    assert '未启用毕业率判定' in dialog.details.toPlainText()
    assert '实际参与智能判定的方案' not in dialog.details.toPlainText()


def test_task_feedback_preserves_progress_and_failure(qtbot):
    from lvjiang.apps.yysls.ui.tuning.agent_page import AgentTaskBridge
    from lvjiang.apps.yysls.ui.tuning.progress_hub import TuningProgressHub

    hub = TuningProgressHub()
    update = {'phase': 'material', 'message': '材料不足'}
    hub.operation_updated.emit(update)
    update['message'] = 'later mutation'
    run = SimpleNamespace(engine=SimpleNamespace(_progress_hub=hub),
                          worker=SimpleNamespace(result_or_exception=ValueError('目标离线')))
    feedback = AgentTaskBridge._feedback(run)
    assert feedback['progress']['operation_updated']['message'] == '材料不足'
    assert feedback['error'] == '目标离线'
