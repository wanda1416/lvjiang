"""Changing the Agent UI context must not mix users' generated plans."""
from types import SimpleNamespace

import pytest
from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QWidget

from lvjiang.apps.yysls.ui.tuning.agent_page import AgentTuningPage


class Host(QWidget):
    user_changed = pyqtSignal(str)


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
        _finish_task_run=lambda *_args, **kwargs: saved.append(kwargs["status"]),
        _auto_save_session=lambda *_args: pytest.fail("Neither path may save a partial session"),
    )
    RunControlMixin._on_workflow_finished.__wrapped__(host, "test_run")
    assert run.metadata["terminal_state"] == expected
    assert saved == [expected]


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
