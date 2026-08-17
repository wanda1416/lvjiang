"""DSL 静态引用搜集与 engine 启动期绑定校验测试

collect_refs 遍历 AST（含嵌套体与过程体），搜集全部静态引用（场景 + key）；
engine _execute_dsl 在解析后据此逐条校验引用是否已在当前布局绑定坐标，
未绑定直接抛 WorkflowUserError，不进入执行阶段（取代手写 required_scenes）。
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from lvjiang.core.config.resolver import SYSTEM_CONFIG_DIR
from lvjiang.workflows.engine import WorkflowEngine, WorkflowUserError
from lvjiang.workflows.grammar import parse_file, parse_text
from lvjiang.workflows.workflow_references import collect_refs, collect_scene_keys

# ─── 搜集单元测试 ─────────────────────────────────────────

def _collect(text: str) -> set[str]:
    program = parse_text(text)
    return collect_scene_keys(program.body, program.procs)


def _refs(text: str) -> list:
    program = parse_text(text)
    return collect_refs(program.body, program.procs)


def test_collect_from_click_scan_recognize():
    text = (
        'click [scene_a].[btn]\n'
        'scan [scene_b].[label] as $x\n'
        'recognize [scene_c].[field] as $y\n'
    )
    assert _collect(text) == {"scene_a", "scene_b", "scene_c"}


def test_collect_from_nested_bodies():
    """if / loop / for 嵌套体内的场景引用同样被搜集"""
    text = (
        'loop 2\n'
        '    click [scene_loop].[btn]\n'
        'end\n'
        'if 1 == 1\n'
        '    click [scene_then].[btn]\n'
        'else\n'
        '    click [scene_else].[btn]\n'
        'end\n'
    )
    assert _collect(text) == {"scene_loop", "scene_then", "scene_else"}


def test_collect_from_try_and_while_bodies():
    """try / catch 与条件循环体内的引用也要搜集，否则漏检"""
    text = (
        'try\n'
        '    click [scene_try].[btn]\n'
        'catch $err\n'
        '    click [scene_catch].[btn]\n'
        'end\n'
        'loop while 1 == 1\n'
        '    click [scene_while].[btn]\n'
        'end\n'
        'loop until 1 == 1\n'
        '    click [scene_until].[btn]\n'
        'end\n'
    )
    assert _collect(text) == {
        "scene_try", "scene_catch", "scene_while", "scene_until"}


def test_collect_from_proc_body():
    """def 过程体内的场景引用被搜集（即使未被调用）"""
    text = (
        'def helper()\n'
        '    click [scene_in_proc].[btn]\n'
        'end\n'
        'log "top"\n'
    )
    assert _collect(text) == {"scene_in_proc"}


def test_collect_empty_when_no_scene_ref():
    assert _collect('log "hi"\nwait 0\n') == set()


def test_ref_kind_by_statement():
    """kind 决定该 key 在布局里查哪类对象：click 查区域/坐标点/面板，drag 查方向/区域"""
    refs = _refs(
        'click [s].[btn]\n'
        'drag [s].[menu_up]\n'
        'align [s].[grid]\n'
        'scan [s].[f1, f2] as $x\n'
    )
    got = {(r.key, r.kind) for r in refs}
    assert got == {
        ("btn", "click_target"), ("menu_up", "drag_target"), ("grid", "panel"),
        ("f1", "region"), ("f2", "region"),
    }


def test_dynamic_key_collected_as_scene_only():
    """key 为 $var 时运行时才知道，只记场景不记 key"""
    refs = _refs('eval $k = "btn"\nclick [s].$k\n')
    assert [(r.scene, r.key) for r in refs] == [("s", None)]


def test_ref_line_no_matches_source():
    """行号须与源文件一致 —— 多行续行语句不能把后续行号顶偏"""
    text = (
        'log "1"\n'
        'scan [s].[f1,\n'
        '    f2] as $x\n'
        'click [s].[btn]\n'
    )
    by_key = {r.key: r.line_no for r in _refs(text)}
    assert by_key["f1"] == 2
    assert by_key["btn"] == 4


# ─── 与旧 required_scenes 等价性验证 ──────────────────────

def test_daily_jianghu_matches_legacy_required_scenes():
    """daily_jianghu.wf 搜集结果应与原手写 required_scenes 一致

    waiguan_qingjing 为情境动作落地后新增的依赖。
    """
    wf = SYSTEM_CONFIG_DIR / "workflows" / "daily_jianghu.wf"
    program = parse_file(wf)
    scenes = collect_scene_keys(program.body, program.procs)
    assert scenes == {
        "activity_jianghu", "waiguan_yigui", "waiguan_qingjing",
        "general_action", "game_menu_page", "game_main_page",
        "general_control", "school_main",
        "bag_item_detail", "bag_equip_detail",
    }


# ─── engine 启动期绑定校验（集成） ────────────────────────

def _make_engine(bound_scenes: set[str], *, points=None, arrows=None,
                 panels=None, regions=None) -> WorkflowEngine:
    """构造最小引擎；bound_scenes 中的场景视为绑定了区域 btn，其余为空。

    静态检查只读绑定对象的 key / from_key / to_key，用最小对象充当即可。
    """
    capture = MagicMock()
    capture.get_capture_size.return_value = (1920, 1080)
    layout = MagicMock()
    layout.get_canvas.return_value = MagicMock(
        x_ratio=0, y_ratio=0, w_ratio=1, h_ratio=1)
    # 默认 region 包含完整属性，供运行时使用
    default_region = SimpleNamespace(key="btn", x_ratio=0.0, y_ratio=0.0, w_ratio=0.5, h_ratio=0.5)
    layout.get_scene_regions.side_effect = lambda k: (
        [regions.get(k, default_region)] if k in bound_scenes else []) if regions else (
        [default_region] if k in bound_scenes else [])
    layout.get_scene_points.side_effect = lambda k: list((points or {}).get(k, []))
    layout.get_scene_arrows.side_effect = lambda k: list((arrows or {}).get(k, []))
    layout.get_scene_panels.side_effect = lambda k: list((panels or {}).get(k, []))
    return WorkflowEngine(
        capture=capture, ocr=MagicMock(), input_ctrl=MagicMock(),
        layout=layout, input_sim=MagicMock(), delay_params={},
    )


def _write_wf(tmp_path, text: str, name: str = "t.wf"):
    wf = tmp_path / name
    wf.write_text(text, encoding="utf-8")
    return wf


def test_missing_scene_raises_before_execution(tmp_path):
    """引用未绑定场景：加载即报错，不进入执行阶段"""
    wf = _write_wf(tmp_path, (
        'def helper()\n'
        '    click [game_main_page].[btn]\n'
        'end\n'
        'log "start"\n'
    ))
    engine = _make_engine(bound_scenes=set())
    with pytest.raises(WorkflowUserError, match="场景未绑定任何坐标"):
        engine.execute(wf)


def test_bound_scene_passes_validation(tmp_path):
    """场景已绑定坐标：校验通过，正常执行到结束"""
    wf = _write_wf(tmp_path, (
        'def helper()\n'
        '    click [game_main_page].[btn]\n'
        'end\n'
        'log "ok"\n'
    ))
    engine = _make_engine(bound_scenes={"game_main_page"})
    engine.execute(wf)  # 不抛异常（helper 未被调用，仅校验不执行）


def test_missing_key_raises_with_file_and_line(tmp_path):
    """场景绑了别的区域、但脚本引用的 key 不存在（如把中文名当 key 写）"""
    wf = _write_wf(tmp_path, (
        'log "start"\n'
        'click [game_main_page].[返回]\n'
    ))
    engine = _make_engine(bound_scenes={"game_main_page"})
    with pytest.raises(WorkflowUserError) as ei:
        engine.execute(wf)
    msg = str(ei.value)
    assert "返回" in msg
    assert "t.wf:2" in msg


def test_missing_key_in_imported_proc_reports_that_file(tmp_path):
    """import 进来的 def 体报错须报它自己的文件名，否则定位到错文件"""
    _write_wf(tmp_path, (
        'def helper()\n'
        '    click [game_main_page].[nope]\n'
        'end\n'
    ), name="lib.wf")
    wf = _write_wf(tmp_path, (
        'import "lib.wf"\n'
        'log "start"\n'
    ))
    engine = _make_engine(bound_scenes={"game_main_page"})
    with pytest.raises(WorkflowUserError) as ei:
        engine.execute(wf)
    assert "lib.wf:2" in str(ei.value)


def test_drag_key_checked_against_arrows_and_regions(tmp_path):
    """drag 查的是方向/区域，绑了 region 就算绑定"""
    wf = _write_wf(tmp_path, 'drag [game_main_page].[btn]\n')
    engine = _make_engine(bound_scenes={"game_main_page"})
    # btn 作为 region 已绑定，静态检查应通过
    engine.execute(wf)


def test_drag_key_unbound_raises(tmp_path):
    """drag 查的是方向/区域，都没绑才报错"""
    wf = _write_wf(tmp_path, 'drag [game_main_page].[unknown]\n')
    engine = _make_engine(bound_scenes={"game_main_page"})
    with pytest.raises(WorkflowUserError, match="方向/区域未绑定"):
        engine.execute(wf)


def test_drag_with_bound_arrow_passes(tmp_path):
    wf = _write_wf(tmp_path, 'log "ok"\n')
    arrow = SimpleNamespace(key="menu_up", from_key="p1", to_key=None)
    engine = _make_engine(
        bound_scenes={"game_main_page"},
        arrows={"game_main_page": [arrow]},
        points={"game_main_page": [SimpleNamespace(key="p1")]},
    )
    engine.execute(wf)


def test_arrow_missing_from_point_raises(tmp_path):
    """方向绑了、但起点坐标点没绑：运行时同样点不出来，提前拦住"""
    wf = _write_wf(tmp_path, 'drag [game_main_page].[menu_up]\n')
    arrow = SimpleNamespace(key="menu_up", from_key="p1", to_key=None)
    engine = _make_engine(
        bound_scenes={"game_main_page"},
        arrows={"game_main_page": [arrow]},
    )
    with pytest.raises(WorkflowUserError, match="起点坐标点未绑定"):
        engine.execute(wf)


def test_panel_key_checked_against_panels(tmp_path):
    """align / panel 索引查的是面板"""
    wf = _write_wf(tmp_path, 'align [game_main_page].[grid]\n')
    engine = _make_engine(bound_scenes={"game_main_page"})
    with pytest.raises(WorkflowUserError, match="面板未绑定"):
        engine.execute(wf)


def test_dynamic_key_skips_key_check(tmp_path):
    """key 为变量：静态只能校验到场景一级，不误报"""
    wf = _write_wf(tmp_path, (
        'eval $k = "whatever"\n'
        'log "ok"\n'
        'if 1 == 2\n'
        '    click [game_main_page].$k\n'
        'end\n'
    ))
    engine = _make_engine(bound_scenes={"game_main_page"})
    engine.execute(wf)
