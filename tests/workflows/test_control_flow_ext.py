"""扩展控制流测试：loop while/until、continue、try/catch

归档自 P0/P1 开发期冒烟测试（scripts/_phase1_smoke.py、_phase2_smoke.py）。
"""
import pytest

from lvjiang.workflows.engine.signals import _ReturnSignal
from lvjiang.workflows.grammar import parse_text
from lvjiang.workflows.grammar.ast_nodes import (
    Continue,
    For,
    If,
    Try,
    UntilLoop,
    WhileLoop,
)
from tests.workflows.conftest import make_engine, run

# ─── loop while / until ───────────────────────────────────

class TestWhileUntil:
    def test_parse_while(self):
        prog = parse_text('eval $x = 0\nloop while $x < 10\n    eval $x = $x + 1\nend\n')
        assert isinstance(prog.body[1], WhileLoop)

    def test_parse_until(self):
        prog = parse_text('loop until $x >= 5\n    eval $x = $x + 1\nend\n')
        assert isinstance(prog.body[0], UntilLoop)

    def test_exec_while(self):
        v = run('loop while $x < 10\n    eval $x = $x + 1\nend\n', {"x": 0.0})
        assert v["x"] == 10.0

    def test_exec_until(self):
        v = run('loop until $x >= 5\n    eval $x = $x + 1\nend\n', {"x": 0.0})
        assert v["x"] == 5.0

    def test_while_break(self):
        code = '''loop while $x < 100
    eval $x = $x + 1
    if $x == 7
        break
    end
end
'''
        v = run(code, {"x": 0.0})
        assert v["x"] == 7.0


# ─── continue ─────────────────────────────────────────────

class TestContinue:
    def test_parse(self):
        prog = parse_text('for i in [1, 2, 3]\n    if $i equals "2"\n        continue\n    end\nend\n')
        for_node = prog.body[0]
        assert isinstance(for_node, For)
        if_node = for_node.body[0]
        assert isinstance(if_node, If)
        assert isinstance(if_node.then_body[0], Continue)

    def test_for_continue_skips(self):
        code = '''for i in [1, 2, 3, 4, 5]
    if $i == 3
        continue
    end
    eval $sum = $sum + $i
end
'''
        v = run(code, {"sum": 0.0})
        # 1+2+4+5 = 12
        assert v["sum"] == 12.0

    def test_nested_continue_inner_only(self):
        """continue 只影响最内层循环，外层迭代不受影响"""
        code = '''loop 3
    eval $outer_count = $outer_count + 1
    loop 3
        if $inner_count == 2
            continue
        end
        eval $inner_count = $inner_count + 1
    end
end
'''
        v = run(code, {"outer_count": 0.0, "inner_count": 0.0})
        assert v["outer_count"] == 3.0


# ─── try / catch ──────────────────────────────────────────

class TestTryCatch:
    def test_parse_with_err_var(self):
        prog = parse_text('try\n    eval $x = 1\ncatch $err\n    log "caught"\nend\n')
        node = prog.body[0]
        assert isinstance(node, Try)
        assert node.err_var == "err"
        assert len(node.body) == 1
        assert len(node.catch_body) == 1

    def test_parse_without_err_var(self):
        prog = parse_text('try\n    eval $x = 1\ncatch\n    log "caught"\nend\n')
        assert prog.body[0].err_var is None

    def test_catch_user_error(self):
        """字符串字段访问触发 WorkflowUserError，被 catch 捕获"""
        code = '''try
    eval $x = $s.not_exist
    eval $touched = "no"
catch $err
    eval $touched = "yes"
end
'''
        v = run(code, {"s": "hello"})
        assert v.get("touched") == "yes"
        assert v.get("err") is not None

    def test_no_error_catch_not_triggered(self):
        code = '''try
    eval $x = 1
catch $err
    eval $caught = 1
end
'''
        v = run(code)
        assert v.get("caught") is None
        assert v.get("x") == 1.0

    def test_catch_without_binding(self):
        code = '''try
    eval $x = $s.not_exist
    eval $touched = "no"
catch
    eval $touched = "yes"
end
'''
        v = run(code, {"s": "hello"})
        assert v.get("touched") == "yes"

    def test_break_passes_through(self):
        code = '''loop 5
    try
        eval $x = $x + 1
        if $x == 2
            break
        end
    catch $err
        eval $x = 999
    end
end
'''
        v = run(code, {"x": 0.0})
        assert v["x"] == 2.0

    def test_return_passes_through(self):
        eng = make_engine()
        eng.variables = {}
        code = '''try
    eval $x = 1
    return
catch $err
    eval $x = 999
end
'''
        with pytest.raises(_ReturnSignal):
            eng._exec_body(parse_text(code).body)
        assert eng.variables.get("x") == 1.0

    def test_goto_passes_through(self):
        code = '''try
    eval $x = 1
    goto skip
catch $err
    eval $x = 999
end
@skip
eval $x = $x + 10
'''
        v = run(code, {"x": 0.0})
        assert v["x"] == 11.0


# ─── call $var = proc() 返回值绑定 ─────────────────────────

class TestCallReturnValue:
    """call $var = proc() 语法：捕获过程返回值"""

    def test_parse_call_assign(self):
        """解析 call $result = proc() 语法"""
        from lvjiang.workflows.grammar.ast_nodes import CallProc
        prog = parse_text('def get_value()\n    return 42\nend\ncall $result = get_value()\n')
        # def 进入 procs，body 只有 call 语句
        call_stmt = prog.body[0]
        assert isinstance(call_stmt, CallProc)
        assert call_stmt.name == "get_value"
        assert call_stmt.result_var == "result"

    def test_exec_return_value(self):
        """return 42 返回值被 call $var = proc() 捕获"""
        code = '''def get_value()
    return 42
end
call $result = get_value()
'''
        v = run(code)
        assert v["result"] == 42.0

    def test_exec_return_string(self):
        """return "hello" 返回字符串"""
        code = '''def greet()
    return "hello"
end
call $msg = greet()
'''
        v = run(code)
        assert v["msg"] == "hello"

    def test_exec_return_variable(self):
        """return $x 返回变量值"""
        code = '''def compute($x)
    eval $y = $x * 2
    return $y
end
call $result = compute(21)
'''
        v = run(code)
        assert v["result"] == 42.0

    def test_exec_return_none(self):
        """无 return 值时返回 None"""
        code = '''def no_return()
    eval $x = 1
end
call $result = no_return()
'''
        v = run(code)
        assert v["result"] is None

    def test_exec_early_return(self):
        """提前 return 也能返回值"""
        code = '''def early($flag)
    if $flag
        return "early"
    end
    return "late"
end
call $r1 = early(1)
call $r2 = early(0)
'''
        v = run(code)
        assert v["r1"] == "early"
        assert v["r2"] == "late"

    def test_call_without_result_var(self):
        """不绑定返回值时也正常工作"""
        code = '''def do_something()
    return 42
end
call do_something()
'''
        v = run(code)
        # 不绑定返回值，不应有异常
        assert "result" not in v

    def test_return_bool(self):
        """return true / false 返回布尔值"""
        code = '''def check($x)
    if $x
        return true
    end
    return false
end
call $r1 = check(1)
call $r2 = check(0)
'''
        v = run(code)
        assert v["r1"] is True
        assert v["r2"] is False

    def test_return_null(self):
        """return null 返回 None"""
        code = '''def give_null()
    return null
end
call $r = give_null()
'''
        v = run(code)
        assert v["r"] is None

    def test_return_list(self):
        """return [1, 2, 3] 返回列表"""
        code = '''def get_list()
    return [1, 2, 3]
end
call $r = get_list()
'''
        v = run(code)
        assert v["r"] == [1.0, 2.0, 3.0]

    def test_return_dict(self):
        """return {"key": "value"} 返回字典"""
        code = '''def get_dict()
    return {"name": "test", "val": 42}
end
call $r = get_dict()
'''
        v = run(code)
        assert v["r"]["name"] == "test"
        assert v["r"]["val"] == 42.0

    def test_return_arith_expr(self):
        """return $x + 1 返回算术表达式结果"""
        code = '''def add_one($x)
    return $x + 1
end
call $r = add_one(41)
'''
        v = run(code)
        assert v["r"] == 42.0

    def test_nested_call_return(self):
        """嵌套调用：outer 调用 inner 并返回其值"""
        code = '''def inner()
    return "from_inner"
end
def outer()
    call $v = inner()
    return $v
end
call $r = outer()
'''
        v = run(code)
        assert v["r"] == "from_inner"
