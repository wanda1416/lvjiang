"""press DSL 集成测试

覆盖：
- press "KEY" 完整按键（PRESS 模式）
- press "KEY" hold N 长按（HOLD 模式）
- press "KEY" down / press "KEY" up 显式时序
- 组合键序列（CTRL down → C → CTRL up）
- 错误状态（重复 down / 未 down 就 up / hold ≤ 0）
- 异常清理（execute finally release_all）
"""


import pytest

from lvjiang.workflows.engine.signals import WorkflowUserError
from lvjiang.workflows.grammar import parse_text
from tests.workflows.conftest import make_engine


def _run(code: str):
    """执行 DSL 并返回 (engine, input_mock)"""
    eng = make_engine()
    program = parse_text(code)
    eng._procs = dict(program.procs)
    eng._exec_body(program.body)
    return eng, eng._input


class TestPressMode:
    def test_basic_press(self):
        """press "M" — 完整按键（down + up）"""
        eng, inp = _run('press "M"\n')
        assert inp.key_down.call_count == 1
        assert inp.key_up.call_count == 1
        inp.key_down.assert_called_with("M")
        inp.key_up.assert_called_with("M")

    def test_press_normalizes_key(self):
        """press "escape" — 键名标准化（escape → ESC）"""
        eng, inp = _run('press "escape"\n')
        inp.key_down.assert_called_with("ESC")
        inp.key_up.assert_called_with("ESC")

    def test_hold(self):
        """press "W" hold 0.01 — 按住指定时长后释放"""
        eng, inp = _run('press "W" hold 0.01\n')
        assert inp.key_down.call_count == 1
        assert inp.key_up.call_count == 1
        inp.key_down.assert_called_with("W")

    def test_down(self):
        """press "SHIFT" down — 仅按下"""
        eng, inp = _run('press "SHIFT" down\n')
        assert inp.key_down.call_count == 1
        assert inp.key_up.call_count == 0

    def test_up(self):
        """press "SHIFT" up — 仅释放"""
        eng, inp = _run('press "SHIFT" down\npress "SHIFT" up\n')
        assert inp.key_down.call_count == 1
        assert inp.key_up.call_count == 1

    def test_up_without_down_raises(self):
        """press "X" up（未先 down）→ 报错"""
        with pytest.raises(WorkflowUserError, match="not pressed"):
            _run('press "X" up\n')


class TestComboKeys:
    def test_ctrl_c_combo(self):
        """组合键：CTRL down → C → CTRL up"""
        code = '''press "CTRL" down
press "C"
press "CTRL" up
'''
        eng, inp = _run(code)
        # CTRL down, C down, C up, CTRL up
        assert inp.key_down.call_count == 2
        assert inp.key_up.call_count == 2
        # 调用顺序：CTRL down → C down → C up → CTRL up
        assert inp.key_down.call_args_list[0].args[0] == "CTRL"
        assert inp.key_down.call_args_list[1].args[0] == "C"
        assert inp.key_up.call_args_list[0].args[0] == "C"
        assert inp.key_up.call_args_list[1].args[0] == "CTRL"


class TestErrorStates:
    def test_duplicate_down_raises(self):
        """press "W" down 两次 → 报错"""
        code = '''press "W" down
press "W" down
'''
        with pytest.raises(WorkflowUserError, match="already pressed"):
            _run(code)

    def test_up_without_down_raises(self):
        """press "W" up（未 down）→ 报错"""
        with pytest.raises(WorkflowUserError, match="not pressed"):
            _run('press "W" up\n')

    def test_hold_zero_raises(self):
        """press "W" hold 0 → 报错（时长必须 > 0）"""
        with pytest.raises(WorkflowUserError, match="> 0"):
            _run('press "W" hold 0\n')

    def test_hold_negative_raises(self):
        """press "W" hold -1 → 报错"""
        with pytest.raises(WorkflowUserError, match="> 0"):
            _run('press "W" hold -1\n')


class TestCleanup:
    def test_registry_created_after_press(self):
        """press 指令执行后 registry 被懒创建"""
        eng, inp = _run('press "M"\n')
        assert eng._key_registry is not None

    def test_down_keys_tracked_in_registry(self):
        """down 模式的键被 registry 跟踪"""
        eng, inp = _run('press "W" down\n')
        assert eng._key_registry.is_pressed("W")

    def test_basic_press_cleans_up(self):
        """PRESS 模式执行后 registry 无残留按键"""
        eng, inp = _run('press "M"\n')
        assert not eng._key_registry.is_pressed("M")

    def test_manual_release_all_after_down(self):
        """down 模式后手动 release_all 释放所有键"""
        eng, inp = _run('press "W" down\npress "SHIFT" down\n')
        assert eng._key_registry.is_pressed("W")
        assert eng._key_registry.is_pressed("SHIFT")
        # 模拟 execute() finally 的清理行为
        eng._key_registry.release_all()
        assert not eng._key_registry.is_pressed("W")
        assert not eng._key_registry.is_pressed("SHIFT")
        # backend 被调用了 2 次 key_up
        assert inp.key_up.call_count == 2

    def test_release_all_idempotent_via_engine(self):
        """通过引擎的 release_all 幂等性"""
        eng, inp = _run('press "W" down\n')
        eng._key_registry.release_all()
        eng._key_registry.release_all()  # 第二次不报错
        assert inp.key_up.call_count == 1  # 只释放一次

    def test_execute_finally_releases_all_keys(self, tmp_path):
        """execute() 的 finally 块自动释放所有残留按键"""
        wf = tmp_path / "test.wf"
        wf.write_text('press "W" down\npress "SHIFT" down\n')
        eng = make_engine()
        eng.execute(str(wf))
        # execute 结束后所有键应被释放
        assert not eng._key_registry.is_pressed("W")
        assert not eng._key_registry.is_pressed("SHIFT")
        # backend 被调用了 2 次 key_up
        assert eng._input.key_up.call_count == 2


class TestPressVariableKey:
    """press $var — 按键名来自变量。

    引擎侧 _exec_press 一直用 _resolve 取值、也早有「按键变量未定义」的
    报错，只是语法与 parser 之前只收 STRING，变量写法根本进不来。
    典型场景：面板里扫出「看报」在第几列，再按对应数字键进入。
    """

    @staticmethod
    def _run_with(code: str, variables: dict):
        eng = make_engine()
        eng.variables = dict(variables)
        program = parse_text(code)
        eng._procs = dict(program.procs)
        eng._exec_body(program.body)
        return eng._input

    def test_variable_key_presses_that_key(self):
        inp = self._run_with('press $fc\n', {"fc": "3"})
        assert inp.method_calls == [("key_down", ("3",), {}),
                                    ("key_up", ("3",), {})]

    def test_numeric_variable_is_accepted(self):
        """扫描算出来的列号是 int，不强制调用方先转字符串。"""
        inp = self._run_with('press $fc\n', {"fc": 3})
        assert inp.method_calls == [("key_down", ("3",), {}),
                                    ("key_up", ("3",), {})]

    def test_variable_key_is_normalized(self):
        inp = self._run_with('press $k\n', {"k": "shift"})
        assert inp.method_calls == [("key_down", ("SHIFT",), {}),
                                    ("key_up", ("SHIFT",), {})]

    def test_variable_key_supports_hold(self):
        inp = self._run_with('press $k hold 0.01\n', {"k": "W"})
        assert [c[0] for c in inp.method_calls] == ["key_down", "key_up"]

    def test_variable_key_supports_down_up(self):
        inp = self._run_with('press $k down\npress $k up\n', {"k": "CTRL"})
        assert inp.method_calls == [("key_down", ("CTRL",), {}),
                                    ("key_up", ("CTRL",), {})]

    def test_undefined_variable_raises_user_error(self):
        with pytest.raises(WorkflowUserError, match="按键变量未定义"):
            self._run_with('press $nope\n', {})

    def test_string_literal_still_works(self):
        inp = self._run_with('press "3"\n', {})
        assert inp.method_calls == [("key_down", ("3",), {}),
                                    ("key_up", ("3",), {})]
