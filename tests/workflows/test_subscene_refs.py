from unittest.mock import MagicMock

from lvjiang.core.layout_models import Region
from lvjiang.workflows.engine import actions, data_ops
from lvjiang.workflows.grammar import (
    Click,
    Recognize,
    Scan,
    SubsceneEntityRef,
    parse_text,
)
from lvjiang.workflows.static_check import check_refs
from lvjiang.workflows.workflow_references import collect_refs

from .conftest import make_engine


def test_three_segment_subscene_targets_parse_for_runtime_operations():
    program = parse_text(
        "click [parent].[card1].[label]\n"
        "scan [parent].[card1].[label] as $text\n"
        "recognize [parent].[card1].[icon] as $icon\n"
    )

    assert isinstance(program.body[0], Click)
    assert program.body[0].target == SubsceneEntityRef("parent", "card1", "label")
    assert isinstance(program.body[1], Scan)
    assert program.body[1].scene == SubsceneEntityRef("parent", "card1", "label")
    assert isinstance(program.body[2], Recognize)
    assert program.body[2].scene == SubsceneEntityRef("parent", "card1", "icon")


def test_subscene_static_reference_retains_instance_key():
    program = parse_text("click [parent].[card1].[label]\n")
    refs = collect_refs(program.body, program.procs)

    assert len(refs) == 1
    assert refs[0].scene == "parent"
    assert refs[0].reference == "card1"
    assert refs[0].key == "label"


def test_subscene_scan_uses_child_scene_for_text_definition(monkeypatch):
    """组合坐标在父画布中，但 is_text 等实体定义必须从子场景读取。"""
    region = Region("label", 0.1, 0.2, 0.3, 0.4)
    monkeypatch.setattr(
        data_ops, "resolve_subscene_target_scene",
        lambda parent, reference: "child")
    monkeypatch.setattr(
        data_ops, "resolve_subscene_region",
        lambda layout, parent, reference, entity: region)
    engine = make_engine()
    engine.variables["card"] = "card_2"
    workflow = MagicMock()
    workflow.ocr_scene.return_value = {"label": "换装"}
    engine._workflow = workflow
    node = parse_text(
        "scan [parent].$card.[label] as $result\n").body[0]

    engine._exec_scan(node)

    assert engine.variables["result"] == {"label": "换装"}
    workflow.ocr_scene.assert_called_once_with(
        "child", ["label"], min_confidence=None,
        regions_override=[region])


def test_subscene_scan_by_uses_child_scene_definition(monkeypatch):
    region = Region("label", 0.1, 0.2, 0.3, 0.4)
    monkeypatch.setattr(
        data_ops, "resolve_subscene_target_scene",
        lambda parent, reference: "child")
    monkeypatch.setattr(
        data_ops, "resolve_subscene_region",
        lambda layout, parent, reference, entity: region)
    engine = make_engine()
    workflow = MagicMock()
    workflow.ocr_scene_by.return_value = "label"
    engine._workflow = workflow
    node = parse_text(
        'scan [parent].[card_2].[label] as $hit by contains "换装"\n'
    ).body[0]

    engine._exec_scan(node)

    workflow.ocr_scene_by.assert_called_once_with(
        "child", ["label"], "换装", "contains",
        min_confidence=None, regions_override=[region])


def test_subscene_recognize_variants_use_child_scene_definition(monkeypatch):
    region = Region("icon", 0.1, 0.2, 0.3, 0.4)
    monkeypatch.setattr(
        data_ops, "resolve_subscene_target_scene",
        lambda parent, reference: "child")
    monkeypatch.setattr(
        data_ops, "resolve_subscene_region",
        lambda layout, parent, reference, entity: region)
    cases = [
        ("recognize [parent].[card].[icon] as $result\n",
         "recognize_references", ({"icon": "物品"}, {"icon": region})),
        ("recognize [parent].[card].[icon] as rich $result\n",
         "recognize_references_rich", ({"icon": {"type": "物品"}}, {"icon": region})),
        ('recognize [parent].[card].[icon] as $result by contains "物品"\n',
         "recognize_references_by", "icon"),
    ]

    for source, method_name, return_value in cases:
        engine = make_engine()
        workflow = MagicMock()
        getattr(workflow, method_name).return_value = return_value
        engine._workflow = workflow

        engine._exec_recognize(parse_text(source).body[0])

        assert getattr(workflow, method_name).call_args.args[0] == "child"
        assert getattr(workflow, method_name).call_args.kwargs[
            "regions_override"] == [region]


def test_move_and_scroll_subscene_targets_execute(monkeypatch):
    """两条语法都复用 click_target，三段引用也必须有对应执行分支。"""
    region = Region("label", 0.1, 0.2, 0.3, 0.4)
    monkeypatch.setattr(
        actions, "resolve_subscene_entity",
        lambda layout, parent, reference, entity: region)
    engine = make_engine()
    engine.variables["card"] = "card_2"
    workflow = MagicMock()
    workflow._region_to_screen.return_value = (321, 654)
    engine._workflow = workflow

    engine._exec_body(parse_text(
        "click [parent].$card.[label]\n"
        "move to [parent].$card.[label] duration 0.2\n"
        "scroll [parent].$card.[label] down 3\n"
    ).body)

    engine._input.click_screen.assert_called_once_with(
        321, 654, "parent/card_2/label", button="left")
    engine._input.move_screen.assert_called_once_with(
        321, 654, "parent/card_2/label", duration=0.2)
    engine._input.scroll_screen.assert_called_once_with(
        321, 654, "down", 3, "parent/card_2/label", interval=None)


def test_move_and_scroll_subscene_targets_are_statically_collected():
    program = parse_text(
        "move to [parent].[card_1].[label]\n"
        "scroll [parent].[card_2].[label] down\n"
    )

    refs = collect_refs(program.body, program.procs)

    assert [(ref.reference, ref.key, ref.kind) for ref in refs] == [
        ("card_1", "label", "move_target"),
        ("card_2", "label", "scroll_target"),
    ]


def test_dynamic_subscene_reference_keeps_parent_level_static_check():
    program = parse_text(
        "click [parent].$card.[label]\n"
        "scan [parent].$card.[label] as $text\n"
    )
    refs = collect_refs(program.body, program.procs)

    assert len(refs) == 2
    assert all(ref.is_subscene for ref in refs)
    assert all(ref.scene == "parent" for ref in refs)
    assert all(ref.reference is None for ref in refs)
    assert all(ref.key == "label" for ref in refs)

    layout = MagicMock()
    layout.get_scene_subscene_refs.return_value = []
    problems = check_refs(refs, layout)
    assert len(problems) == 2
    assert all("子场景引用" in problem.reason for problem in problems)
