"""内置函数实参 / 过程实参的字面量常数支持

两处实参位（grammar 的 ``arg`` 与 ``call_arg``）曾经各写各的分支，
只认字符串 / 数字 / 变量 / 字段访问。后果是 ``check_env(["android"])``
这种连自身 docstring 都在示范的调用在实参位解析不过，只能先 eval 到
变量再传。这里锁住「两处能力一致，且都支持完整字面量」。
"""

import pytest

from lvjiang.workflows.engine.signals import WorkflowUserError
from lvjiang.workflows.grammar import parse_text
from tests.workflows.conftest import make_engine


class TestLiteralArgsRuntime:
    """字面量实参要在调用方作用域求值后，以 Python 原生值注入形参。"""

    def test_proc_receives_resolved_values(self):
        program = parse_text('''eval $v = "V"
call $out = echo(["a", $v], {"k": [1, null]}, true, false, null, [])
def echo($lst, $dct, $t, $f, $n, $empty)
    eval $r = {"lst": $lst, "dct": $dct, "t": $t,
               "f": $f, "n": $n, "empty": $empty}
    return $r
end
''')
        eng = make_engine()
        eng._procs.update(program.procs)
        eng._exec_body(program.body)

        assert eng.variables["out"] == {
            "lst": ["a", "V"],
            "dct": {"k": [1, None]},
            "t": True,
            "f": False,
            "n": None,
            "empty": [],
        }

    def test_builtin_receives_list_literal(self):
        """check_env(["..."]) 直接可用——本次改动的起因用例。"""
        eng = make_engine()
        eng.run_env = "android"
        eng._exec_body(parse_text('eval check_env(["android", "desktop"])\n').body)

    def test_builtin_list_literal_still_rejects_bad_env(self):
        eng = make_engine()
        eng.run_env = ""
        with pytest.raises(WorkflowUserError, match="check_env"):
            eng._exec_body(
                parse_text('eval check_env(["android", "desktop"])\n').body)
