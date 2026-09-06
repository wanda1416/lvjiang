"""太极炸鱼工作流配置回归测试。"""

from pathlib import Path

from lvjiang.workflows.grammar import (
    FuncCall,
    If,
    Literal,
    Log,
    Press,
    Return,
    VarRef,
    parse_text,
)
from lvjiang.workflows.metadata import parse_metadata

WORKFLOW_PATH = (
    Path(__file__).resolve().parents[2]
    / "config/system/workflows/gather_zhayu.wf"
)


def _source() -> str:
    return WORKFLOW_PATH.read_text(encoding="utf-8")


def test_skill_key_is_select_limited_to_one_through_four():
    metadata = parse_metadata(_source())
    parameter = next(
        item for item in metadata["parameters"]
        if item["name"] == "skill_key"
    )

    assert parameter == {
        "name": "skill_key",
        "label": "技能按键",
        "type": "select",
        "default": "1",
        "options": ["1", "2", "3", "4"],
    }


def test_background_guard_precedes_actions_and_desktop_uses_variable_key():
    program = parse_text(_source(), source=str(WORKFLOW_PATH))

    guard_index = next(
        index for index, node in enumerate(program.body)
        if isinstance(node, If)
        and isinstance(node.condition, FuncCall)
        and node.condition.func_name == "is_post"
    )
    action_index = next(
        index for index, node in enumerate(program.body)
        if isinstance(node, If)
        and isinstance(node.condition, FuncCall)
        and node.condition.func_name == "env"
        and any(isinstance(child, Press) for child in node.else_body[0].then_body)
    )
    press = next(
        child
        for child in program.body[action_index].else_body[0].then_body
        if isinstance(child, Press)
    )

    assert guard_index < action_index
    assert isinstance(press.key, VarRef)
    assert press.key.name == "skill_key"
    guard = program.body[guard_index]
    assert any(
        isinstance(node, Log)
        and isinstance(node.message, Literal)
        and "关闭后台模式后重试" in node.message.value
        for node in guard.then_body
    )
    assert any(
        isinstance(node, Return) and node.value == -1
        for node in guard.then_body
    )
