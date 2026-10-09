"""情境保存询问与未保存退出提示分别取消、确认，避免卡住后续任务。"""

import pytest

from lvjiang.core.config.resolver import SYSTEM_CONFIG_DIR
from lvjiang.workflows.grammar import parse_text
from tests.workflows.conftest import make_engine


@pytest.mark.parametrize(
    ("prompt", "in_appearance"),
    [("当前方案未保存，是否确认退出?", True), ("", False)],
)
def test_qingjing_handles_unsaved_exit_before_leaving_layers(
    monkeypatch, prompt, in_appearance,
):
    source = (SYSTEM_CONFIG_DIR / "workflows/daily_jianghu.wf").read_text(
        encoding="utf-8",
    )
    program = parse_text(source)
    engine = make_engine(run_env="desktop")
    engine._procs = program.procs
    trace = []
    texts = {
        ("appearance_main", "chuanda"): "",
        ("general_control", "cancel"): "取消",
        ("appearance_main", "save"): "保存",
        ("general_control", "label"): prompt,
        ("appearance_main", "edit_qingjing"): "编辑情境" if in_appearance else "",
    }

    def scan(node):
        key = node.scene.scene, node.fields[0].value
        text = texts[key]
        engine.variables[node.target.name] = (
            text if node.by.target.value in text else ""
        )
        trace.append(("scan", *key))

    monkeypatch.setattr(engine, "_exec_scan", scan)
    monkeypatch.setattr(
        engine, "_exec_click",
        lambda node: trace.append(("click", node.target.scene, node.target.entity)),
    )
    monkeypatch.setattr(
        engine, "_exec_wait",
        lambda node: trace.append(("wait", node.delay.value)),
    )
    monkeypatch.setattr(engine, "_exec_wait_stable", lambda node: None)
    engine._run_proc(program.procs["action_qingjing"], ["card_1"])

    exit_index = trace.index(("click", "appearance_main", "back"))
    assert trace.index(("click", "general_control", "cancel")) < exit_index
    assert trace[exit_index + 1:exit_index + 3] == [
        ("wait", "page_refresh"), ("scan", "general_control", "label"),
    ]
    next_index = exit_index + 3
    if prompt:
        assert trace[next_index:next_index + 2] == [
            ("click", "general_control", "confirm"), ("wait", "page_refresh"),
        ]
        next_index += 2
    else:
        assert ("click", "general_control", "confirm") not in trace
    assert trace[next_index] == ("scan", "appearance_main", "edit_qingjing")
    assert trace.count(("click", "appearance_main", "back")) == 1 + in_appearance
    assert trace[-4:] == [
        ("click", "activity_jianghu", "qingjing_back"), ("wait", "step_interval"),
        ("click", "activity_jianghu", "overlay_back"), ("wait", "step_interval"),
    ]
