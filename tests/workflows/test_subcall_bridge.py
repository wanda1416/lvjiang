"""Python 桥 load_subcalls / call_subcall 引擎级测试

覆盖：def 注册与返回值传递、变量隔离、重新加载覆盖先前定义、
未加载过程报错、相对路径经 resolver 解析、真实导航 subcall 可加载。
"""

import pytest

from lvjiang.core.config import load_user_config
from lvjiang.core.config.resolver import SYSTEM_CONFIG_DIR
from lvjiang.core.layout_manager import load_layout_by_name
from lvjiang.workflows.engine.signals import WorkflowUserError
from lvjiang.workflows.grammar import parse_file
from lvjiang.workflows.grammar.ast_nodes import Return
from tests.workflows.conftest import make_engine

WF_WITH_RETURN = '''def add_one($x)
    eval $y = $x + 1
    return $y
end

def fail_proc()
    return -1
end
'''


def test_load_and_call_with_return(tmp_path):
    """加载后可调用，return 值直达 Python 调用方"""
    wf = tmp_path / "demo.wf"
    wf.write_text(WF_WITH_RETURN, encoding="utf-8")
    eng = make_engine()
    eng.load_subcalls(wf)
    assert eng.call_subcall("add_one", [1]) == 2
    # DSL 约定 return < 0 表示错误，值原样返回由调用方判定
    assert eng.call_subcall("fail_proc") == -1


def test_reload_overwrites(tmp_path):
    """重新加载同名过程时，后加载的定义覆盖先加载的"""
    wf = tmp_path / "demo.wf"
    wf.write_text("def get_val()\n    return 1\nend\n", encoding="utf-8")
    eng = make_engine()
    eng.load_subcalls(wf)
    assert eng.call_subcall("get_val") == 1

    # 修改文件内容后重新加载
    wf.write_text("def get_val()\n    return 2\nend\n", encoding="utf-8")
    eng.load_subcalls(wf)
    assert eng.call_subcall("get_val") == 2  # 新定义生效


def test_call_unloaded_raises():
    """未加载的过程直接报错，不走静默降级"""
    eng = make_engine()
    with pytest.raises(ValueError, match="未加载"):
        eng.call_subcall("nope")


def test_missing_relative_file():
    """相对路径找不到文件时给出明确错误"""
    eng = make_engine()
    with pytest.raises(WorkflowUserError, match="找不到子过程文件"):
        eng.load_subcalls("subcall/__definitely_missing__.wf")


def test_variable_isolation(tmp_path):
    """子过程变量作用域隔离：不污染调用方变量表"""
    wf = tmp_path / "iso.wf"
    wf.write_text("def setx()\n    eval $x = 99\nend\n", encoding="utf-8")
    eng = make_engine()
    eng.variables = {"x": 1}
    eng.load_subcalls(wf)
    eng.call_subcall("setx")
    assert eng.variables["x"] == 1


def test_real_nav_subcalls_loadable():
    """真实导航 subcall：相对路径解析 + 真布局/等待参数静态校验通过

    navigator.py 生产路径用的就是这些相对路径，此用例保证路径与
    静态校验口径（命名等待/布局引用）不漂移。
    """
    layout = load_layout_by_name("默认布局")
    assert layout is not None
    eng = make_engine(layout=layout,
                      delay_params=load_user_config().delay_params)
    eng.load_subcalls("subcall/navigation.wf")
    assert "nav_main_to_equip" in eng._procs
    assert "nav_equip_to_tune" in eng._procs
    assert "nav_back_to_main" in eng._procs


def test_nav_subcalls_have_explicit_success_return():
    """除返回背包 tab 的基础过程外，导航过程统一以 0 表示成功。"""
    nav_path = SYSTEM_CONFIG_DIR / "workflows/subcall/navigation.wf"
    program = parse_file(nav_path)

    for name in (
        "nav_main_to_equip",
        "nav_main_to_wallet",
        "nav_main_to_item",
        "nav_main_to_menu",
        "nav_back_to_main",
        "nav_equip_to_tune",
    ):
        final_stmt = program.procs[name].body[-1]
        assert isinstance(final_stmt, Return), f"{name} 缺少显式成功返回值"
        assert final_stmt.value == 0, f"{name} 应以 0 表示成功"
