"""测试算术运算符语法解析"""
from lvjiang.workflows.grammar import parse_text, ArithOp
from lvjiang.workflows.grammar.ast_nodes import Eval, EvalFieldChainAssign, If, GreaterThan


def test(name, code):
    try:
        prog = parse_text(code)
        print(f"  OK: {name} -> {prog.body[0]}")
        return prog
    except Exception as e:
        print(f"  FAIL: {name} -> {e}")
        return None


print("=== 算术表达式解析测试 ===")

# Test 1: 简单算术
test("eval $x = 1 + 2", 'eval $x = 1 + 2\n')

# Test 2: 变量参与算术
test("eval $x = $a + $b * 2", 'eval $x = $a + $b * 2\n')

# Test 3: 括号改优先级
test("eval $x = (1 + 2) * 3", 'eval $x = (1 + 2) * 3\n')

# Test 4: 浮点除法
test("eval $x = $a / 2", 'eval $x = $a / 2\n')

# Test 5: 条件中使用算术
test("if $a > $b + 1", 'if $a > $b + 1\n    log "yes"\nend\n')

# Test 6: 字段赋值 + 算术
test("eval $d.k = $a + 1", 'eval $d.k = $a + 1\n')

# Test 7: 负数（向后兼容）
test("eval $x = -5", 'eval $x = -5\n')

# Test 8: 负数参与运算
test("eval $x = -5 + 3", 'eval $x = -5 + 3\n')

# Test 9: 复杂嵌套
test("eval $x = ($a + $b) * ($c - 1)", 'eval $x = ($a + $b) * ($c - 1)\n')

# Test 10: 函数调用参与算术
test("eval $x = add(1, 2) + $a", 'eval $x = add(1, 2) + $a\n')

# Test 11: 比较两侧都是表达式
test("if $a + 1 == $b * 2", 'if $a + 1 == $b * 2\n    log "eq"\nend\n')

# Test 12: 隐式 eval + 算术
test("$x = $a + 1", '$x = $a + 1\n')

print("\n=== 引擎求值测试 ===")

from unittest.mock import MagicMock
from lvjiang.workflows.engine import WorkflowEngine


def make_engine():
    """创建一个最小化的引擎实例用于求值测试"""
    capture = MagicMock()
    capture.get_capture_size.return_value = (1920, 1080)
    ocr = MagicMock()
    input_ctrl = MagicMock()
    layout = MagicMock()
    layout.get_canvas.return_value = MagicMock(x_ratio=0, y_ratio=0, w_ratio=1, h_ratio=1)
    delay_config = MagicMock()
    engine = WorkflowEngine(
        capture=capture, ocr=ocr, input_ctrl=input_ctrl,
        layout=layout, delay_config=delay_config,
    )
    return engine


def run_eval(code, variables, expected):
    """解析并执行 eval，检查变量值"""
    engine = make_engine()
    engine.variables = dict(variables)
    try:
        prog = parse_text(code)
        for stmt in prog.body:
            engine._exec_stmt(stmt)
        actual = engine.variables.get("x")
        if actual == expected:
            print(f"  OK: {code.strip()} with {variables} -> x={actual}")
        else:
            print(f"  FAIL: {code.strip()} -> expected {expected}, got {actual}")
    except Exception as e:
        print(f"  ERROR: {code.strip()} -> {e}")


# 基础运算
run_eval("eval $x = 1 + 2", {}, 3.0)
run_eval("eval $x = 10 - 3", {}, 7.0)
run_eval("eval $x = 4 * 5", {}, 20.0)
run_eval("eval $x = 10 / 3", {}, 10/3)  # 浮点除
run_eval("eval $x = 10 / 0", {}, 0.0)  # 除 0

# 变量参与
run_eval("eval $x = $a + $b", {"a": 10, "b": 20}, 30.0)
run_eval("eval $x = $a * 2 + 1", {"a": 5}, 11.0)

# 括号改优先级
run_eval("eval $x = (1 + 2) * 3", {}, 9.0)
run_eval("eval $x = ($a + $b) * ($c - 1)", {"a": 2, "b": 3, "c": 4}, 15.0)

# 负数
run_eval("eval $x = -5 + 3", {}, -2.0)
run_eval("eval $x = $a - 10", {"a": 3}, -7.0)

# 复杂嵌套
run_eval("eval $x = ($a + 1) * ($b - 2) / 2", {"a": 4, "b": 6}, 10.0)

# 条件测试
def run_condition(code, variables, expected):
    engine = make_engine()
    engine.variables = dict(variables)
    try:
        prog = parse_text(code)
        if_node = prog.body[0]
        result = engine._eval_condition(if_node.condition)
        if result == expected:
            print(f"  OK: {code.strip()} with {variables} -> {result}")
        else:
            print(f"  FAIL: {code.strip()} -> expected {expected}, got {result}")
    except Exception as e:
        print(f"  ERROR: {code.strip()} -> {e}")


run_condition('if $a > $b + 1\n    log "yes"\nend\n', {"a": 10, "b": 5}, True)
run_condition('if $a > $b + 1\n    log "yes"\nend\n', {"a": 5, "b": 10}, False)
run_condition('if $a + 1 == $b * 2\n    log "eq"\nend\n', {"a": 9, "b": 5}, True)
run_condition('if $a + 1 == $b * 2\n    log "eq"\nend\n', {"a": 8, "b": 5}, False)

print("\n=== 全部测试完成 ===")
