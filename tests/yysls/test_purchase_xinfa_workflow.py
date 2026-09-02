"""自动购买心法工作流的背包心得预处理合同。"""

from dataclasses import fields, is_dataclass
from pathlib import Path

from lvjiang.workflows.grammar import parse_file
from lvjiang.workflows.grammar.ast_nodes import (
    Break,
    If,
    PanelGridDrag,
    Recognize,
    Scan,
    VarRef,
    WaitStable,
    WhileLoop,
)
from lvjiang.workflows.metadata import parse_metadata_file

_WORKFLOW = (
    Path(__file__).parents[2]
    / "config/system/workflows/purchase_xinfa.wf"
)


def _walk(value):
    if isinstance(value, (list, tuple)):
        for item in value:
            yield from _walk(item)
        return
    if not is_dataclass(value):
        return
    yield value
    for field in fields(value):
        child = getattr(value, field.name)
        if isinstance(child, (list, tuple)) or is_dataclass(child):
            yield from _walk(child)


def test_open_bag_xinfa_parameter_is_opt_in():
    metadata = parse_metadata_file(_WORKFLOW)

    assert metadata["parameters"] == [{
        "name": "open_bag_xinfa",
        "label": "打开背包全部心法",
        "type": "bool",
        "default": False,
    }, {
        "name": "max_xinfa_boxes",
        "label": "最多打开心法心得数",
        "type": "number",
        "default": 5,
        "min": 1,
    }]


def test_bag_xinfa_hit_branch_is_bounded():
    """命中分支必须封顶：道具没被真正消耗时不能无限重扫同一个格子。

    默认上限由 max_xinfa_boxes 参数给出（见上一个用例），这里只钉结构：
    唯一的扫描循环里必须有一个以该参数为界、命中即 break 的守卫。
    """
    program = parse_file(_WORKFLOW)
    nodes = list(_walk(program.procs["open_all_bag_xinfa"].body))

    loops = [node for node in nodes if isinstance(node, WhileLoop)]
    assert len(loops) == 1

    guards = [
        node for node in _walk(loops[0].body)
        if isinstance(node, If)
        and VarRef("max_xinfa_boxes") in list(_walk(node.condition))
        and any(isinstance(inner, Break) for inner in node.then_body)
    ]
    assert len(guards) == 1


def test_bag_xinfa_uses_short_circuit_recognition_and_one_scroll_path():
    program = parse_file(_WORKFLOW)
    nodes = list(_walk(program.procs["open_all_bag_xinfa"].body))

    recognizes = [node for node in nodes if isinstance(node, Recognize)]
    assert len(recognizes) == 1
    recognize = recognizes[0]
    assert recognize.by is not None
    assert recognize.by.match_mode == "equals"
    assert recognize.by.target.value == "心法心得"
    assert recognize.by.full is False
    assert recognize.rich is False
    assert recognize.group.value == "普通道具"
    assert recognize.where.min_confidence.value == 0.6

    drags = [node for node in nodes if isinstance(node, PanelGridDrag)]
    assert len(drags) == 1
    assert drags[0].scene == "bag_item_detail"
    assert drags[0].panel == "bag_grid"
    assert drags[0].direction == "up"
    assert drags[0].distance == VarRef("scroll_rows")


def test_bag_xinfa_checks_specific_confirm_and_watches_menu_area():
    program = parse_file(_WORKFLOW)
    nodes = list(_walk(program.procs["open_all_bag_xinfa"].body))

    confirm_scans = [
        node for node in nodes
        if isinstance(node, Scan)
        and node.fields
        and node.fields[0].value == "confirm"
    ]
    assert len(confirm_scans) == 1
    assert confirm_scans[0].scene.scene == "training_xinfa"

    stable_waits = [node for node in nodes if isinstance(node, WaitStable)]
    assert stable_waits
    assert all(wait.area.scene == "general_control" for wait in stable_waits)
    assert all(wait.area.entity == "menu_area" for wait in stable_waits)
