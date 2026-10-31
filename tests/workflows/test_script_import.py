"""外置 Python 脚本（``import "xxx.py"``）的加载语义与安全边界。

只允许 config/local：系统层可被在线下发，允许它带 .py 等于开放远程代码执行。
"""

from pathlib import Path

import pytest

from lvjiang.workflows.builtins import list_functions
from lvjiang.workflows.engine.signals import WorkflowUserError
from tests.workflows.conftest import make_engine

DOUBLE_SCRIPT = """\
from lvjiang.workflows.builtins import builtin_func


@builtin_func("double_it")
def double_it(value):
    return value * 2
"""

STOP_PROBE_SCRIPT = """\
from lvjiang.workflows.builtins import builtin_func


@builtin_func("stop_requested")
def stop_requested(_engine):
    return _engine.should_stop()
"""


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _local_wf_root(wf_root: Path) -> Path:
    """wf_root fixture 指向 system/workflows；本地层是它的兄弟目录。"""
    root = wf_root.parent.parent / "local" / "workflows"
    root.mkdir(parents=True, exist_ok=True)
    return root


def test_local_script_function_is_usable(wf_root):
    """local 层脚本注册的函数可直接在 DSL 表达式里调用。"""
    local = _local_wf_root(wf_root)
    _write(local / "tools" / "helpers.py", DOUBLE_SCRIPT)
    wf = _write(local / "main.wf", 'import "tools/helpers.py"\n$v = double_it(21)\n')

    engine = make_engine()
    engine._execute_dsl(wf)

    assert engine.variables["v"] == 42


def test_system_script_is_rejected(wf_root):
    """系统层出现 .py 立刻报错，不给"忽略继续"的降级。"""
    _write(wf_root / "helpers.py", DOUBLE_SCRIPT)
    wf = _write(wf_root / "main.wf", 'import "helpers.py"\n')

    with pytest.raises(WorkflowUserError, match="本地配置层"):
        make_engine()._load_and_validate(wf.resolve())


def test_local_script_shadows_system_copy(wf_root):
    """同名脚本两层都有时用 local 副本，不因 system 存在而报错。"""
    _write(wf_root / "helpers.py", DOUBLE_SCRIPT)
    local = _local_wf_root(wf_root)
    _write(local / "helpers.py", """\
from lvjiang.workflows.builtins import builtin_func


@builtin_func("double_it")
def double_it(_value):
    return 7
""")
    wf = _write(local / "main.wf", 'import "helpers.py"\n$v = double_it(21)\n')

    engine = make_engine()
    engine._execute_dsl(wf)

    assert engine.variables["v"] == 7


def test_script_path_cannot_escape_workflows(wf_root):
    """import 路径仍受既有沙盒约束，不能借 .. 穿出 workflows 根。"""
    wf = _write(wf_root / "main.wf", 'import "../evil.py"\n')

    with pytest.raises(WorkflowUserError, match="import 路径"):
        make_engine()._load_and_validate(wf.resolve())


def test_script_function_conflicts_with_builtin(wf_root):
    """脚本函数与内置函数重名时报错，不静默覆盖内置能力。"""
    builtin_name = list_functions()[0]
    local = _local_wf_root(wf_root)
    _write(local / "clash.py", f"""\
from lvjiang.workflows.builtins import builtin_func


@builtin_func("{builtin_name}")
def clash():
    return 1
""")
    wf = _write(local / "main.wf", 'import "clash.py"\n')

    with pytest.raises(WorkflowUserError, match="与内置函数重名"):
        make_engine()._execute_dsl(wf)


def test_undecorated_function_is_not_callable(wf_root):
    """没有 @builtin_func 的函数不进命名空间，调用它等于未知函数。"""
    local = _local_wf_root(wf_root)
    _write(local / "plain.py", "def hidden():\n    return 1\n")
    wf = _write(local / "main.wf", 'import "plain.py"\n$v = hidden()\n')

    with pytest.raises(ValueError, match="未知内置函数"):
        make_engine()._execute_dsl(wf)


def test_script_is_reloaded_between_runs(wf_root):
    """改完脚本再执行即生效，不需要重启。"""
    local = _local_wf_root(wf_root)
    script = local / "answer.py"
    _write(script, """\
from lvjiang.workflows.builtins import builtin_func


@builtin_func("answer")
def answer():
    return 1
""")
    wf = _write(local / "main.wf", 'import "answer.py"\n$v = answer()\n')
    engine = make_engine()

    engine._execute_dsl(wf)
    assert engine.variables["v"] == 1

    _write(script, """\
from lvjiang.workflows.builtins import builtin_func


@builtin_func("answer")
def answer():
    return 2
""")
    engine._execute_dsl(wf)
    assert engine.variables["v"] == 2


def test_engine_injection_exposes_stop_check(wf_root):
    """首参 _engine 注入后，脚本可以在长循环里查停止请求。"""
    local = _local_wf_root(wf_root)
    _write(local / "probe.py", STOP_PROBE_SCRIPT)
    wf = _write(local / "main.wf", 'import "probe.py"\n')
    state = {"stop": False}
    engine = make_engine(stop_check=lambda: state["stop"])

    engine._load_and_validate(wf.resolve())
    engine._load_script_modules()

    fn = engine.get_script_function("stop_requested")
    assert fn is not None
    assert fn(engine) is False
    state["stop"] = True
    assert fn(engine) is True


def test_nested_wf_import_propagates_script(wf_root):
    """.wf 的 import 链深处引入的脚本同样生效。"""
    local = _local_wf_root(wf_root)
    _write(local / "tools" / "helpers.py", DOUBLE_SCRIPT)
    _write(
        local / "sub.wf",
        'import "tools/helpers.py"\ndef sub()\n    return 1\nend\n',
    )
    wf = _write(local / "main.wf", 'import "sub.wf"\n$v = double_it(4)\n')

    engine = make_engine()
    engine._execute_dsl(wf)

    assert engine.variables["v"] == 8


def test_script_syntax_error_is_reported_as_user_error(wf_root):
    """脚本语法错误按用户可读错误报出，不甩 traceback。"""
    local = _local_wf_root(wf_root)
    _write(local / "broken.py", "def broken(:\n")
    wf = _write(local / "main.wf", 'import "broken.py"\n')

    with pytest.raises(WorkflowUserError, match="外部脚本执行失败"):
        make_engine()._execute_dsl(wf)


def test_script_functions_do_not_leak_between_tasks(wf_root):
    """批处理里相邻任务互相隔离：前一个任务 import 的函数在下一个任务不可见。"""
    local = _local_wf_root(wf_root)
    _write(local / "tools" / "helpers.py", DOUBLE_SCRIPT)
    with_script = _write(
        local / "with.wf", 'import "tools/helpers.py"\n$v = double_it(2)\n')
    without_script = _write(local / "without.wf", '$v = double_it(2)\n')

    engine = make_engine()
    engine.execute(with_script)
    assert engine.variables["v"] == 4
    assert engine.script_function_names() == []

    with pytest.raises(ValueError, match="未知内置函数"):
        engine.execute(without_script)


def test_script_functions_are_released_after_failed_task(wf_root):
    """任务中途失败时函数同样销毁，不给下一次执行留残骸。"""
    local = _local_wf_root(wf_root)
    _write(local / "tools" / "helpers.py", DOUBLE_SCRIPT)
    wf = _write(
        local / "boom.wf",
        'import "tools/helpers.py"\n$v = double_it(2)\n$bad = missing_func()\n')

    engine = make_engine()
    with pytest.raises(ValueError, match="未知内置函数"):
        engine.execute(wf)

    assert engine.script_function_names() == []


def test_script_never_touches_global_registry(wf_root):
    """脚本函数只进引擎命名空间：两个引擎各自持有，全局表里查不到。"""
    from lvjiang.workflows.builtins import get_function as global_get

    local = _local_wf_root(wf_root)
    _write(local / "tools" / "helpers.py", DOUBLE_SCRIPT)
    wf = _write(local / "main.wf", 'import "tools/helpers.py"\n$v = double_it(3)\n')

    first, second = make_engine(), make_engine()
    first.execute(wf)
    second.execute(wf)

    assert first.variables["v"] == 6
    assert second.variables["v"] == 6
    assert global_get("double_it") is None
