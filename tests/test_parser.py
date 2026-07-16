"""DSL 解析器验证测试"""

from pathlib import Path
from lvjiang.workflows.parser import parse_file, parse_text
from lvjiang.workflows.ast_nodes import (
    Program, Click, Wait, Scan, ClickMatch, Collect, Log, Eval,
    If, Contains, FieldAccess, VarRef, Literal,
)


def test_existing_wf_files():
    """验证现有 .wf 文件能正常解析"""
    print("=== 验证现有 .wf 文件 ===")
    for wf in ["equip_analysis", "single_tuning"]:
        path = Path(f"config/system/workflows/{wf}.wf")
        program = parse_file(path)
        print(f"  {path}: {len(program.body)} 条指令 OK")
        assert isinstance(program, Program)


def test_scan_as_required():
    """测试 scan 必须带 as"""
    print("\n=== 测试 scan as ===")

    # 带 as 的 scan
    program = parse_text("scan [scene1].[field1, field2] as [result]")
    assert len(program.body) == 1
    n = program.body[0]
    assert isinstance(n, Scan)
    assert n.target.name == "result"
    print("  scan as: OK")

    # 不带 as 的 scan 应该解析失败
    try:
        parse_text("scan [scene1]")
        assert False, "应该解析失败"
    except Exception:
        print("  scan 无 as 正确报错: OK")


def test_click_match_new_syntax():
    """测试新 click_match 语法"""
    print("\n=== 测试 click_match 新语法 ===")

    program = parse_text('click_match [scene1].[result] "调律" error "未找到"')
    assert len(program.body) == 1
    n = program.body[0]
    assert isinstance(n, ClickMatch)
    assert n.scene.name == "scene1"
    assert n.var.name == "result"
    assert n.text.value == "调律"
    assert n.error_msg.value == "未找到"
    print("  click_match [scene].[var] text: OK")


def test_collect_as_dict():
    """测试 collect 语法"""
    print("\n=== 测试 collect ===")

    # collect [var]
    program = parse_text("collect [result]")
    n = program.body[0]
    assert isinstance(n, Collect)
    assert n.source.name == "result"
    assert n.alias is None
    print("  collect [var]: OK")

    # collect [var] as [label]
    program = parse_text("collect [result] as [label]")
    n = program.body[0]
    assert isinstance(n, Collect)
    assert n.source.name == "result"
    assert n.alias == "label"
    print("  collect [var] as [label]: OK")


def test_if_with_scan_as():
    """测试 if 条件与 scan as 配合"""
    print("\n=== 测试 if + scan as ===")
    text = """\
scan [equip_weapon_detail] as [result]
if [result].field1 contains "调律"
    collect [result] as [good]
    log "好装备"
end
"""
    program = parse_text(text)
    assert len(program.body) == 2  # scan + if
    assert isinstance(program.body[0], Scan)
    assert isinstance(program.body[1], If)

    if_node = program.body[1]
    assert isinstance(if_node.condition, Contains)
    assert len(if_node.then_body) == 2  # collect + log
    print("  if + scan as: OK")


def test_eval_with_var():
    """测试 eval 使用显式变量"""
    print("\n=== 测试 eval ===")

    program = parse_text("eval result = is_good_equip(scan_result)")
    assert len(program.body) == 1
    n = program.body[0]
    assert isinstance(n, Eval)
    assert n.func_name == "is_good_equip"
    assert n.target == "result"
    print("  eval with var: OK")


def test_full_workflow():
    """测试完整工作流片段"""
    print("\n=== 测试完整工作流 ===")
    text = """\
scan [scene1].[field1] as [scan1]
if not [scan1].field1 contains "关键词"
    log "未找到"
    return
end
click_match [scene1].[scan1] "按钮" error "未找到按钮"
collect [scan1] as [output]
"""
    program = parse_text(text)
    assert len(program.body) == 4  # scan + if + click_match + collect
    print("  完整工作流: OK")


if __name__ == "__main__":
    test_existing_wf_files()
    test_scan_as_required()
    test_click_match_new_syntax()
    test_collect_as_dict()
    test_if_with_scan_as()
    test_eval_with_var()
    test_full_workflow()
    print("\nALL PASSED")
