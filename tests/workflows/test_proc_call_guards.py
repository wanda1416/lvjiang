"""过程调用的边界保护：嵌套深度与实参个数

两条都是「脚本写错时给出可行动报错」的契约：
- 递归未收敛过去打到 Python 的 RecursionError，栈帧全是引擎内部，看不出是哪个 .wf；
- 实参个数不符过去被静默吞掉（多传丢弃、少传读成 null），脚本拿到的参数不对却跑得下去，
  而 LSP 诊断早就按个数报错——运行时必须同口径。
"""

import pytest

from lvjiang.workflows.engine import data_ops
from lvjiang.workflows.engine.signals import WorkflowUserError
from tests.workflows.conftest import run


class TestCallDepthGuard:
    def test_mutual_recursion_reports_script_error(self, monkeypatch):
        monkeypatch.setattr(data_ops, "_MAX_CALL_DEPTH", 4)
        code = '''def a($n)
    call b($n)
end
def b($n)
    call a($n)
end
call a(1)
'''
        with pytest.raises(WorkflowUserError, match="嵌套"):
            run(code)

    def test_shallow_calls_unaffected(self):
        code = '''def leaf()
    return 1
end
def mid()
    call $v = leaf()
    return $v + 1
end
call $r = mid()
'''
        assert run(code)["r"] == 2


class TestCallArgCount:
    """多写实参是静默丢弃的脚本错误；少写实参是既有约定，不能收紧。

    库里大量 ``call is_in_main_page()`` 就是靠「形参缺失时读作 null」在跑，
    只有"多出来的实参被无声丢掉"才是纯粹的写错。
    """

    def test_extra_argument_is_rejected(self):
        code = '''def f($a)
    return $a
end
call f(1, 2)
'''
        with pytest.raises(WorkflowUserError, match="最多接受 1 个实参"):
            run(code)

    def test_missing_argument_still_reads_as_null(self):
        code = '''def f($a, $b)
    return $a
end
call $r = f(1)
'''
        assert run(code)["r"] == 1

    def test_matching_arguments_still_work(self):
        code = '''def f($a, $b)
    return $a + $b
end
call $r = f(1, 2)
'''
        assert run(code)["r"] == 3
