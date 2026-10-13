"""插件控制运行时保留断线停止、人工提示和目标草稿的安全边界。"""
from types import SimpleNamespace

import pytest

from lvjiang.ui.main.plugin_runtime import PluginRuntime


def test_pending_prompt_blocks_resume_but_disconnected_target_can_stop():
    calls = []
    dialog = SimpleNamespace(text=lambda: "请确认")
    run = SimpleNamespace(target_id="phone", state="paused",
                          ui_helper=SimpleNamespace(_active_dialog=dialog))
    target = SimpleNamespace(id="phone", display_name="phone", kind="adb", ready=True)
    host = SimpleNamespace(
        _execution_targets=SimpleNamespace(active_target_id="phone", get=lambda _: target),
        _run_manager=SimpleNamespace(run=lambda _: run, run_for_target=lambda _: run),
        _request_stop=lambda **_: calls.append("stop"),
        _on_pause_resume=lambda: calls.append("resume"),
    )
    runtime = PluginRuntime(host)
    assert runtime.pending_prompt("run") == "请确认"
    with pytest.raises(ValueError, match="人工提示"):
        runtime.control_run("run", "resume")
    assert not calls
    run.ui_helper._active_dialog = None
    runtime.control_run("run", "resume")
    target.ready = False
    with pytest.raises(ValueError, match="未连接"):
        runtime.control_run("run", "pause")
    runtime.control_run("run", "stop")
    assert calls == ["resume", "stop"]


def test_prepare_start_rejects_unavailable_backend_or_plan():
    ready = [False]
    allowed = [True]
    target = SimpleNamespace(id="phone", display_name="phone", kind="adb", ready=True)
    host = SimpleNamespace(
        _execution_targets=SimpleNamespace(active_target_id="phone", get=lambda _: target),
        _run_manager=SimpleNamespace(
            run_for_target=lambda _: None,
            can_start=lambda **_: SimpleNamespace(allowed=True)),
        _backend_ready=lambda: ready[0],
        _plan_allows_backend=lambda: allowed[0],
    )
    runtime = PluginRuntime(host)
    with pytest.raises(ValueError, match="当前连接方案或执行环境不可用"):
        runtime.prepare_start("phone", "tester")
    ready[0] = True
    allowed[0] = False
    with pytest.raises(ValueError, match="当前连接方案或执行环境不可用"):
        runtime.prepare_start("phone", "tester")
    allowed[0] = True
    runtime.prepare_start("phone", "tester")
