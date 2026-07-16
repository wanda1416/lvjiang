"""DSL 解析器验证测试"""

from pathlib import Path
from lvjiang.workflows.parser import parse_file, parse_text, Step, EvalNode, IfNode


def test_existing_wf_files():
    """验证现有 .wf 文件能正常解析"""
    print("=== 验证现有 .wf 文件 ===")
    for wf in ["equip_analysis", "tune_test"]:
        path = Path(f"config/system/workflows/{wf}.wf")
        nodes = parse_file(path)
        print(f"  {path}: {len(nodes)} 条指令 OK")
        for n in nodes:
            assert isinstance(n, Step), f"预期 Step，得到 {type(n)}"


def test_eval():
    """测试 eval 指令"""
    print("\n=== 测试 eval ===")

    # 带赋值
    nodes = parse_text("eval result = is_good_equip(last_scan)")
    assert len(nodes) == 1
    n = nodes[0]
    assert isinstance(n, EvalNode)
    assert n.func_name == "is_good_equip"
    assert n.var_name == "result"
    assert n.func_args == [("var", "last_scan")]
    print(f"  eval 赋值: OK")

    # 不带赋值
    nodes = parse_text("eval is_good_equip(last_scan)")
    n = nodes[0]
    assert isinstance(n, EvalNode)
    assert n.var_name is None
    print(f"  eval 无赋值: OK")

    # 多参数（字面量 + 变量）
    nodes = parse_text('eval r = contains(last_scan, "调律")')
    n = nodes[0]
    assert n.func_name == "contains"
    assert n.func_args == [("var", "last_scan"), ("lit", "调律")]
    print(f"  eval 混合参数: OK")


def test_if_multiline():
    """测试多行 if/else/end"""
    print("\n=== 测试多行 if/else/end ===")
    text = """\
scan [equip_weapon_detail]
if is_good_equip(last_scan)
    collect_as good_equip
    log "好装备"
else
    log "不值得保留"
end
"""
    nodes = parse_text(text)
    assert len(nodes) == 2  # scan + if
    assert isinstance(nodes[0], Step)
    assert isinstance(nodes[1], IfNode)

    if_node = nodes[1]
    assert if_node.condition["func"] == "is_good_equip"
    assert if_node.condition["negated"] is False
    assert len(if_node.consequent) == 2  # collect_as + log
    assert len(if_node.alternative) == 1  # log
    print(f"  if/else/end 结构: OK")


def test_if_not():
    """测试 not 条件"""
    print("\n=== 测试 not 条件 ===")
    nodes = parse_text("if not is_good_equip(last_scan)")
    n = nodes[0]
    assert isinstance(n, IfNode)
    assert n.condition["negated"] is True
    assert n.condition["func"] == "is_good_equip"
    print(f"  not 函数条件: OK")


def test_if_var():
    """测试变量条件"""
    print("\n=== 测试变量条件 ===")
    nodes = parse_text("if result")
    n = nodes[0]
    assert isinstance(n, IfNode)
    assert "var" in n.condition
    assert n.condition["var"] == "result"
    assert n.condition["negated"] is False
    print(f"  变量条件: OK")

    nodes = parse_text("if not result")
    n = nodes[0]
    assert n.condition["negated"] is True
    print(f"  not 变量条件: OK")


def test_nested_if():
    """测试嵌套 if"""
    print("\n=== 测试嵌套 if ===")
    text = """\
if is_good_equip(last_scan)
    if contains(last_scan, "调律")
        log "匹配"
    end
end
"""
    nodes = parse_text(text)
    assert len(nodes) == 1
    outer = nodes[0]
    assert isinstance(outer, IfNode)
    assert len(outer.consequent) == 1
    inner = outer.consequent[0]
    assert isinstance(inner, IfNode)
    assert inner.condition["func"] == "contains"
    print(f"  嵌套 if: OK")


def test_backward_compat():
    """验证所有旧指令仍正常解析"""
    print("\n=== 验证旧指令兼容 ===")
    text = """\
click [bag_equip_detail].[slot_main_weapon]
wait page_refresh_wait
wait 1.5
scan [equip_weapon_detail]
scan [equip_weapon_detail].[affix_gong, affix_shang]
click_match "调律" error "未找到调律按钮"
collect
collect_as good_equip
log "测试消息"
# 这是注释
"""
    nodes = parse_text(text)
    assert len(nodes) == 9  # 注释被过滤
    instructions = [n.instruction for n in nodes]
    assert instructions == [
        "click", "wait", "wait", "scan", "scan",
        "click_match", "collect", "collect_as", "log"
    ]
    print(f"  9 条旧指令全部兼容: OK")


if __name__ == "__main__":
    test_existing_wf_files()
    test_eval()
    test_if_multiline()
    test_if_not()
    test_if_var()
    test_nested_if()
    test_backward_compat()
    print("\nALL PASSED")
