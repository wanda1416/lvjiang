"""自动购买心法工作流的背包心得预处理合同。"""

from dataclasses import fields, is_dataclass
from pathlib import Path

import pytest

from lvjiang.core.task_params import parameters_for_values
from lvjiang.workflows.builtins._registry import _FUNCTION_REGISTRY
from lvjiang.workflows.engine.signals import _ReturnSignal
from lvjiang.workflows.grammar import parse_file, parse_text
from lvjiang.workflows.grammar.ast_nodes import (
    Break,
    CallProc,
    PanelGridDrag,
    Recognize,
    Scan,
    VarRef,
    WaitStable,
    WhileLoop,
)
from lvjiang.workflows.metadata import parse_metadata_file
from tests.workflows.conftest import make_engine

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
        "label": "最多打开数量",
        # 只在打开背包心法时才用得上，关着时整行不展示
        "require": "$open_bag_xinfa",
        "type": "number",
        "default": 5,
        "min": 1,
    }]
    assert [item["name"] for item in parameters_for_values(
        metadata["parameters"], {"open_bag_xinfa": False})] == ["open_bag_xinfa"]


def test_bag_xinfa_limit_stops_repeated_item_and_continues_purchase(monkeypatch):
    """道具未消耗、同一格反复命中时，上限仍生效，并继续原心法置换流程。"""
    program = parse_file(_WORKFLOW)
    engine = make_engine(run_env="desktop")
    engine.variables.update({"open_bag_xinfa": True, "max_xinfa_boxes": 1})
    engine._procs = dict(program.procs)
    helpers = _WORKFLOW.parent / "subcall/result_records.wf"
    engine._procs.update(parse_file(helpers).procs)
    engine._procs.update(parse_text(
        'def declare_profiles($keys)\nreturn 0\nend\n'
        'def nav_main_to_item()\nreturn 0\nend\n'
        'def nav_back_to_main()\nreturn 0\nend\n'
        'def nav_main_to_menu()\nreturn 0\nend\n'
        'def sync_weekly_remaining($raw, $key, $last, $total, $label, $source)\n'
        'return 0\nend\n').procs)
    monkeypatch.setitem(_FUNCTION_REGISTRY, "profile_get", lambda key: 0)
    monkeypatch.setitem(_FUNCTION_REGISTRY, "panel_rows", lambda *args: 3)
    hits = iter([{"row": 1, "col": 1}, {"row": 1, "col": 1}, None, None])
    clicks = []

    def scan(node):
        key = node.fields[0].value
        engine.variables[node.target.name] = {
            "peiyang": "培养", "exchange": "置换",
            "stock_of_week": {"stock_of_week": "600/600"},
        }.get(key, "")

    monkeypatch.setattr(engine, "_exec_scan", scan)
    monkeypatch.setattr(engine, "_exec_recognize",
                        lambda node: engine.variables.__setitem__(node.target.name, next(hits)))
    monkeypatch.setattr(engine, "_exec_find",
                        lambda node: engine.variables.__setitem__(node.var_name, "打开"))
    monkeypatch.setattr(engine, "_exec_click", lambda node: clicks.append(node.target))
    monkeypatch.setattr(engine, "_exec_wait", lambda node: None)
    monkeypatch.setattr(engine, "_exec_wait_stable", lambda node: None)
    monkeypatch.setattr(engine, "_exec_drag", lambda node: None)
    with pytest.raises(_ReturnSignal) as returned:
        engine._exec_body(program.body)
    assert returned.value.value == -1  # 保留原正常收尾返回值
    result = engine.output["purchase_xinfa"]
    assert result["counts"]["box_open_attempts"] == 1
    assert result["status"] == "processed"
    assert any(getattr(target, "entity", None) == "exchange" for target in clicks)


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


def test_weekly_stock_is_scanned_before_and_after_purchase_loop():
    """周库存只在循环入口和统一出口各同步一次，退出分支不得绕过末次扫描。"""
    program = parse_file(_WORKFLOW)
    stock_scans = [
        node for node in program.body
        if isinstance(node, Scan)
        and node.fields
        and node.fields[0].value == "stock_of_week"
    ]
    sync_calls = [
        node for node in program.body
        if isinstance(node, CallProc)
        and node.name == "sync_weekly_remaining"
    ]
    loops = [node for node in program.body if isinstance(node, WhileLoop)]

    assert len(stock_scans) == 2
    assert len(sync_calls) == 2
    assert len(loops) == 1
    assert sum(isinstance(node, Break) for node in _walk(loops[0].body)) == 2
