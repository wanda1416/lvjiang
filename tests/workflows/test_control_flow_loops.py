"""控制流循环补充测试

补充 test_control_flow_ext.py 未覆盖的分支：
- for 迭代动态变量列表
- for 迭代函数调用结果
- for_range 异常分支
- loop until + continue 交互
- loop 次数变量引用
"""

from lvjiang.workflows.engine import control_flow
from lvjiang.workflows.grammar import parse_text
from tests.workflows.conftest import make_engine, run


class TestForDynamicIterable:
    def test_for_iterates_list_variable(self):
        """for $x in $list_var 迭代动态列表"""
        code = '''for item in $items
    eval $sum = $sum + $item
end
'''
        v = run(code, {"items": [1, 2, 3], "sum": 0})
        assert v["sum"] == 6

    def test_for_undefined_variable_skipped(self):
        """for 迭代未定义变量时跳过"""
        code = '''for item in $missing
    eval $count = $count + 1
end
'''
        v = run(code, {"count": 0})
        assert v["count"] == 0

    def test_for_non_list_variable_skipped(self):
        """for 迭代非列表变量时跳过"""
        code = '''for item in $not_a_list
    eval $count = $count + 1
end
'''
        v = run(code, {"not_a_list": "hello", "count": 0})
        assert v["count"] == 0


class TestForRangeEdgeCases:
    def test_for_range_invalid_values_skipped(self):
        """for_range 起止值非数值时跳过"""
        code = '''for i in [$start...$end]
    eval $count = $count + 1
end
'''
        v = run(code, {"start": "abc", "end": "def", "count": 0})
        assert v["count"] == 0

    def test_for_range_start_greater_than_end(self):
        """起点大于终点时与 range() 一样空转，不进入循环体。"""
        code = '''for i in [10...5]
    eval $count = $count + 1
end
'''
        v = run(code, {"count": 0})
        assert v["count"] == 0


class TestLoopUntilContinue:
    def test_until_continue_skips_condition_check(self):
        """loop until 中 continue 跳过条件检查进入下一轮"""
        code = '''loop until $x >= 5
    eval $x = $x + 1
    if $x == 3
        continue
    end
    eval $marked = $marked + 1
end
'''
        v = run(code, {"x": 0, "marked": 0})
        assert v["x"] == 5
        # x=3 时 continue，不执行 marked+1，所以 marked = 4 而非 5
        assert v["marked"] == 4


class TestLoopCountVariable:
    def test_loop_with_variable_count(self):
        """loop $count 使用变量控制次数"""
        code = '''loop $times
    eval $sum = $sum + 1
end
'''
        v = run(code, {"times": 5, "sum": 0})
        assert v["sum"] == 5


class TestLoopSafetyGuard:
    """条件循环的迭代上限必须对 continue 路径同样生效。"""

    def test_while_continue_cannot_bypass_guard(self, monkeypatch):
        monkeypatch.setattr(control_flow, "_MAX_LOOP_ITERATIONS", 5)
        code = '''loop while true
    eval $n = $n + 1
    continue
end
'''
        assert run(code, {"n": 0})["n"] == 5

    def test_until_continue_cannot_bypass_guard(self, monkeypatch):
        monkeypatch.setattr(control_flow, "_MAX_LOOP_ITERATIONS", 5)
        code = '''loop until false
    eval $n = $n + 1
    continue
end
'''
        assert run(code, {"n": 0})["n"] == 5


class TestUnboundedLoopsCapability:
    """``#% requires: [unbounded_loops]`` 解除条件循环的迭代上限。"""

    CODE = (
        "eval $n = 0\n"
        "loop while true\n"
        "    eval $n = $n + 1\n"
        "    if $n >= 20 -> break\n"
        "end\n"
        "return $n\n"
    )

    def _execute(self, wf_root, header: str):
        path = wf_root / "probe.wf"
        path.write_text(header + self.CODE, encoding="utf-8")
        engine = make_engine()
        engine.execute(path)
        return engine

    def test_capability_removes_iteration_limit(self, wf_root, monkeypatch):
        monkeypatch.setattr(control_flow, "_MAX_LOOP_ITERATIONS", 5)
        engine = self._execute(wf_root, "#% requires: [unbounded_loops]\n")
        assert engine._loop_limit == 0
        assert engine.return_value == 20

    def test_scripts_without_capability_keep_the_default_cap(self, wf_root, monkeypatch):
        monkeypatch.setattr(control_flow, "_MAX_LOOP_ITERATIONS", 5)
        engine = self._execute(wf_root, "")
        assert engine._loop_limit == 5
        assert engine.return_value == 5


class TestSingleLineGuard:
    """`if <条件> -> <语句>` 是块式 if 的行内写法，真假行为必须一致。"""

    def test_action_runs_only_when_condition_true(self):
        code = 'if $ready -> eval $hit = 1\n'
        assert run(code, {"ready": 1})["hit"] == 1
        assert "hit" not in run(code, {"ready": 0})

    def test_guard_only_skips_its_own_action(self):
        """条件为假只跳过那一条语句，后面的语句照常执行。"""
        code = 'if not $ready -> eval $stopped = 1\neval $after = 1\n'
        v = run(code, {"ready": 0})
        assert v["stopped"] == 1 and v["after"] == 1

    def test_guard_return_exits_proc(self):
        """守卫里的 return 与块式 if 里的 return 一样退出过程。"""
        code = '''def probe($ready)
    if $ready -> return 1
    return 0
end
call $a = probe(1)
call $b = probe(0)
'''
        v = run(code)
        assert v["a"] == 1
        assert v["b"] == 0

    def test_guard_matches_block_if_result(self):
        inline = run('if $ready -> eval $hit = 1\n', {"ready": 1})
        block = run('if $ready\n    eval $hit = 1\nend\n', {"ready": 1})
        assert inline["hit"] == block["hit"]


class TestGotoSafetyGuard:
    """goto 回跳必须有次数上限，否则 `@a ... goto a` 只能靠用户点停止收场。

    上限约束的是**回跳次数**（与循环迭代上限共用 ``_loop_limit``），所以块体
    实际执行 ``limit + 1`` 次：首次进入 + limit 次回跳；第 limit+1 次回跳被拦下。
    """

    CODE = '''@loop
    eval $n = $n + 1
    goto loop
'''

    def test_goto_loop_stops_at_limit(self, monkeypatch):
        monkeypatch.setattr(control_flow, "_MAX_LOOP_ITERATIONS", 5)
        assert run(self.CODE, {"n": 0})["n"] == 6

    def test_goto_loop_keeps_default_cap(self):
        """上限未解除时同样生效。"""
        engine = make_engine()
        engine.variables = {"n": 0}
        engine._loop_limit = 7
        engine._exec_body(parse_text(self.CODE).body)
        assert engine.variables["n"] == 8

    def test_bounded_goto_still_works(self):
        """有限次回跳的既有写法不受影响。"""
        code = '''eval $n = 0
@again
eval $n = $n + 1
if $n < 3
    goto again
end
'''
        assert run(code, {"n": 0})["n"] == 3
