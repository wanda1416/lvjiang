"""控制流循环补充测试

补充 test_control_flow_ext.py 未覆盖的分支：
- for 迭代动态变量列表
- for 迭代函数调用结果
- for_range 异常分支
- loop until + continue 交互
- loop 次数变量引用
"""

from tests.workflows.conftest import run


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
