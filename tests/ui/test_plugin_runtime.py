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
