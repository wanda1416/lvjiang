"""DSL 静态场景搜集与 engine 启动期场景绑定校验测试

collect_scene_keys 遍历 AST（含嵌套体与过程体），搜集全部静态场景引用；
engine _execute_dsl 在解析后据此校验场景是否已在当前布局绑定坐标，
未绑定直接抛 WorkflowUserError，不进入执行阶段（取代手写 required_scenes）。
"""

from unittest.mock import MagicMock

import pytest

from lvjiang.config import DelayConfig
from lvjiang.constants import SYSTEM_WORKFLOWS_DIR
from lvjiang.workflows.engine import WorkflowEngine, WorkflowUserError
from lvjiang.workflows.grammar import parse_file, parse_text
from lvjiang.workflows.scene_scan import collect_scene_keys

# ─── collect_scene_keys 单元测试 ──────────────────────────

def _collect(text: str) -> set[str]:
    program = parse_text(text)
    return collect_scene_keys(program.body, program.procs)


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


# ─── 与旧 required_scenes 等价性验证 ──────────────────────

def test_activity_jianghu_matches_legacy_required_scenes():
    """activity_jianghu.wf 搜集结果应与原手写 required_scenes 一致"""
    wf = SYSTEM_WORKFLOWS_DIR / "activity_jianghu.wf"
    program = parse_file(wf)
    scenes = collect_scene_keys(program.body, program.procs)
    assert scenes == {
        "activity_jianghu", "ui_waiguan_yigui", "action_control",
        "game_menu_page", "game_main_page", "general_control", "ui_school_main",
    }


# ─── engine 启动期场景绑定校验（集成） ────────────────────

def _make_engine(bound_scenes: set[str]) -> WorkflowEngine:
    """构造最小引擎；bound_scenes 中的场景视为已绑定区域，其余为空。"""
    capture = MagicMock()
    capture.get_capture_size.return_value = (1920, 1080)
    layout = MagicMock()
    layout.get_canvas.return_value = MagicMock(
        x_ratio=0, y_ratio=0, w_ratio=1, h_ratio=1)
    layout.get_scene_regions.side_effect = lambda k: {"btn": (0, 0, 1, 1)} if k in bound_scenes else {}
    layout.get_scene_points.side_effect = lambda k: {}
    layout.get_scene_arrows.side_effect = lambda k: {}
    layout.get_scene_panels.side_effect = lambda k: {}
    return WorkflowEngine(
        capture=capture, ocr=MagicMock(), input_ctrl=MagicMock(),
        layout=layout, delay_config=DelayConfig(),
    )


def _write_wf(tmp_path, text: str):
    wf = tmp_path / "t.wf"
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
    with pytest.raises(WorkflowUserError, match="未绑定坐标"):
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
