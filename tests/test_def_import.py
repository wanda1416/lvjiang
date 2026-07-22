"""测试 def / import / call proc 语法解析"""

import sys
sys.path.insert(0, str(__import__('pathlib').Path(__file__).parent.parent))

from lvjiang.workflows.grammar.parser import parse_text
from lvjiang.workflows.grammar.ast_nodes import Import, ProcDef, CallProc


def test_basic_def():
    """基本 def 定义"""
    text = """
def greet($name)
    log $name
end
"""
    prog = parse_text(text)
    assert len(prog.procs) == 1, f"Expected 1 proc, got {len(prog.procs)}"
    assert "greet" in prog.procs
    proc = prog.procs["greet"]
    assert proc.params == ["name"], f"Expected ['name'], got {proc.params}"
    assert len(proc.body) == 1
    print("PASS: test_basic_def")


def test_def_no_params():
    """无参数 def"""
    text = """
def do_something()
    log "hello"
end
"""
    prog = parse_text(text)
    assert len(prog.procs) == 1
    proc = prog.procs["do_something"]
    assert proc.params == []
    print("PASS: test_def_no_params")


def test_def_multi_params():
    """多参数 def"""
    text = """
def process($row, $col, $scene)
    log $row
end
"""
    prog = parse_text(text)
    proc = prog.procs["process"]
    assert proc.params == ["row", "col", "scene"], f"Got {proc.params}"
    print("PASS: test_def_multi_params")


def test_call_proc():
    """call proc 调用"""
    text = """
call greet("world")
call process(1, $col, "scene")
"""
    prog = parse_text(text)
    assert len(prog.body) == 2
    assert isinstance(prog.body[0], CallProc)
    assert prog.body[0].name == "greet"
    assert prog.body[1].name == "process"
    assert len(prog.body[1].args) == 3
    print("PASS: test_call_proc")


def test_call_no_args():
    """无参数 call"""
    text = """
call do_something()
"""
    prog = parse_text(text)
    assert len(prog.body) == 1
    assert isinstance(prog.body[0], CallProc)
    assert prog.body[0].name == "do_something"
    assert prog.body[0].args == []
    print("PASS: test_call_no_args")


def test_import():
    """import 语句"""
    text = """
import "subcall/utils.wf"
import "subcall/nav.wf"
"""
    prog = parse_text(text)
    assert len(prog.imports) == 2
    assert prog.imports[0].path == "subcall/utils.wf"
    assert prog.imports[1].path == "subcall/nav.wf"
    print("PASS: test_import")


def test_mixed():
    """混合 import + def + body"""
    text = """
import "subcall/utils.wf"

def local_proc($x)
    log $x
end

call local_proc("hello")
call utils_func(1, 2)
"""
    prog = parse_text(text)
    assert len(prog.imports) == 1
    assert len(prog.procs) == 1
    assert "local_proc" in prog.procs
    assert len(prog.body) == 2
    assert isinstance(prog.body[0], CallProc)
    assert isinstance(prog.body[1], CallProc)
    print("PASS: test_mixed")


def test_def_with_return():
    """def 内包含 return"""
    text = """
def check($val)
    if $val is_empty
        return
    end
    log $val
end
"""
    prog = parse_text(text)
    proc = prog.procs["check"]
    assert len(proc.body) == 2  # if + log
    print("PASS: test_def_with_return")


if __name__ == "__main__":
    test_basic_def()
    test_def_no_params()
    test_def_multi_params()
    test_call_proc()
    test_call_no_args()
    test_import()
    test_mixed()
    test_def_with_return()
    print("\nAll tests passed!")
