"""Python 桥 load_subcalls / call_subcall 引擎级测试

覆盖：def 注册与返回值传递、变量隔离、重新加载覆盖先前定义、
未加载过程报错、相对路径经 resolver 解析、真实导航 subcall 可加载。
"""

import pytest

from lvjiang.core.config import load_user_config
from lvjiang.core.layout.manager import load_layout_by_key
from lvjiang.core.paths import get_config_system_dir
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
    layout = load_layout_by_key("android")
    assert layout is not None
    eng = make_engine(layout=layout,
                      delay_params=load_user_config().delay_params)
    eng.load_subcalls("subcall/navigation.wf")
    assert "nav_main_to_equip" in eng._procs
    assert "nav_equip_to_tune" in eng._procs
    assert "nav_back_to_main" in eng._procs
    assert "is_in_equip_tune_page" in eng._procs


def test_nav_subcalls_have_explicit_success_return():
    """除返回背包 tab 的基础过程外，导航过程统一以 0 表示成功。"""
    nav_path = get_config_system_dir() / "workflows/subcall/navigation.wf"
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


class _StubRegion:
    """只有 key / template / disabled 三个静态检查会用到的字段"""

    def __init__(self, key: str, template=None):
        self.key = key
        self.template = template
        self.disabled = False


class _StubLayout:
    """只摆本端那一侧的主页图标：另一端是「有坐标、没绑模板」的占位。

    真实布局就是这样：安卓的 ``ditu`` 绑了模板、``dongzuo`` 只有坐标，
    桌面反之。占位区域刻意保留，用来放另一端的标定结果。
    """

    def __init__(self, regions: dict[str, list]):
        self._regions = regions

    def get_scene_regions(self, scene):
        return self._regions.get(scene, [])

    def get_scene_points(self, scene):
        return []

    def get_scene_arrows(self, scene):
        return []

    def get_scene_panels(self, scene):
        return []


WF_ENV_BRANCHED_ICON = '''def main_page_icon()
    eval $icon = ""
    if env("android")
        scan [game_main_page].[ditu] as $icon by image
    else if env("desktop")
        scan [game_main_page].[dongzuo] as $icon by image
    end
    return to_flag($icon)
end

call $ok = main_page_icon()
'''


def test_load_subcalls_prunes_other_side_env_branch(tmp_path):
    """load_subcalls 的引用校验按本端裁 ``env()`` 分支。

    真实事故：安卓批量里「购买心法心得」的可执行性检查全灭，理由是
    ``page_detection.wf:53 [game_main_page].[dongzuo] — 区域未绑定模板``。
    桌面那颗图标只在本端绑了模板，安卓布局里同 key 的区域是纯坐标占位，
    而 load_subcalls 校验时没把 run_env 传下去，于是「本端永远不执行」
    的分支也被算进校验，把整批任务挡在门外。
    """
    from lvjiang.core.layout.models import TemplateBinding

    layout = _StubLayout({
        "game_main_page": [
            _StubRegion("ditu", TemplateBinding(
                "android/game_main_page/ditu", 0.8, 2048, 920)),
            _StubRegion("dongzuo"),
        ],
    })
    wf = tmp_path / "icon_probe.wf"
    wf.write_text(WF_ENV_BRANCHED_ICON, encoding="utf-8")

    # 本端分支只碰 ditu：校验必须通过（回归点）
    make_engine(layout=layout, run_env="android").load_subcalls(wf)

    # 反证：环境未知时两路都查，另一端的占位区域确实会让校验失败，
    # 说明上面那条通过不是靠用例太松，而是靠 run_env 裁掉了无关分支。
    with pytest.raises(WorkflowUserError, match="dongzuo"):
        make_engine(layout=layout, run_env="").load_subcalls(wf)


def test_tune_navigation_uses_stable_page_label_detector():
    """调律页身份不能依赖会在满词条时变成「定音」的动作按钮。"""
    workflows = get_config_system_dir() / "workflows/subcall"
    navigation = (workflows / "navigation.wf").read_text(encoding="utf-8")
    detection = (workflows / "page_detection.wf").read_text(encoding="utf-8")

    assert navigation.count("is_in_equip_tune_page()") == 2
    assert '[equip_tune_detail].[tune_btn]' not in navigation
    assert (
        'scan [equip_tune_detail].[tune_label] as $found '
        'by contains "装备调律"'
    ) in detection
