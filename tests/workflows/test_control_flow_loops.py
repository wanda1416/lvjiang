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
        """for_range 起始值大于结束值时跳过"""
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
