"""图色内置函数（builtins/vision）引擎级测试

验证 DSL → CoordRef → 画布像素换算 → color_ops 的整条链：
- CoordRef（[scene].[region] 求值）与 FoundRegion 都能作入参
- 画布裁剪（canvas x/y/w/h 非全屏时坐标仍对）
- 归一化距离参数按画布高换算
- 返回类型：标量 / dict / FoundRegion 列表（可 click）
"""
from __future__ import annotations

from unittest.mock import MagicMock

import numpy as np
import pytest

from lvjiang.core.coord_types import CircleCoordRef, RectCoordRef
from lvjiang.core.layout_models import FoundRegion, Region
from lvjiang.workflows.builtins import get_function
from tests.workflows.conftest import make_engine


def _bgr(rgb):
    return rgb[2], rgb[1], rgb[0]


def _frame(w=200, h=100, rgb=(0, 0, 0)) -> np.ndarray:
    img = np.zeros((h, w, 3), dtype=np.uint8)
    img[:] = _bgr(rgb)
    return img


def _engine_with(frame: np.ndarray, canvas=(0, 0, 1, 1)):
    eng = make_engine()
    eng._capture.capture.return_value = frame
    eng._capture.get_capture_size.return_value = (frame.shape[1], frame.shape[0])
    eng._layout.get_canvas.return_value = MagicMock(
        x_ratio=canvas[0], y_ratio=canvas[1], w_ratio=canvas[2], h_ratio=canvas[3])
    return eng


def _call(name, eng, *args):
    return get_function(name)(eng, *args)


# ─── 基本链路 ───────────────────────────────────────────

def test_pixel_and_bright_on_center():
    frame = _frame()
    frame[50, 100] = _bgr((10, 20, 30))
    eng = _engine_with(frame)
    ref = RectCoordRef(cx=0.5, cy=0.5, w=0.0, h=0.0)
    assert _call("pixel", eng, ref) == [10, 20, 30]
    assert _call("bright", eng, ref) == 60


def test_color_ratio_two_forms():
    frame = _frame()
    frame[:, :100] = _bgr((46, 204, 113))  # 左半 #2ecc71
    eng = _engine_with(frame)
    whole = RectCoordRef(cx=0.5, cy=0.5, w=1.0, h=1.0)
    assert _call("color_ratio", eng, whole, "#2ecc71", 10) == pytest.approx(0.5)
    assert _call("color_ratio", eng, whole, "#005a28", "#5affa0") == pytest.approx(0.5)
    left = RectCoordRef(cx=0.25, cy=0.5, w=0.5, h=1.0)
    assert _call("color_ratio", eng, left, "#2ecc71", 10) == pytest.approx(1.0)


def test_color_ratio_requires_coord_ref():
    eng = _engine_with(_frame())
    with pytest.raises(ValueError, match="color_ratio"):
        _call("color_ratio", eng, "not-a-ref", "#ffffff", 10)


def test_found_region_accepted_as_input():
    frame = _frame()
    frame[20:40, 20:60] = _bgr((255, 255, 255))
    eng = _engine_with(frame)
    fr = FoundRegion(x_ratio=0.1, y_ratio=0.2, w_ratio=0.2, h_ratio=0.2)
    assert _call("color_ratio", eng, fr, "#ffffff", 0) == pytest.approx(1.0)


def test_region_to_coord_ref_roundtrip():
    """布局 Region.to_coord_ref() 产出的 RectCoordRef 是 DSL 里 [scene].[region] 的真实形态"""
    frame = _frame()
    frame[0:50, 0:100] = _bgr((200, 0, 0))
    eng = _engine_with(frame)
    ref = Region(key="tl", x_ratio=0.0, y_ratio=0.0, w_ratio=0.5, h_ratio=0.5).to_coord_ref()
    assert _call("color_ratio", eng, ref, "#c80000", 0) == pytest.approx(1.0)


# ─── 画布裁剪 ───────────────────────────────────────────

def test_canvas_crop_shifts_coordinates():
    """画布是截图中部 50%：归一化 (0.5,0.5) 应落到截图 (0.5,0.5)，而非左上象限"""
    frame = _frame(w=200, h=100)
    frame[25:75, 50:150] = _bgr((0, 0, 255))     # 画布区域整片蓝
    frame[0:25, :] = _bgr((255, 0, 0))            # 画布外红
    eng = _engine_with(frame, canvas=(0.25, 0.25, 0.5, 0.5))
    whole = RectCoordRef(cx=0.5, cy=0.5, w=1.0, h=1.0)
    assert _call("color_ratio", eng, whole, "#0000ff", 0) == pytest.approx(1.0)
    assert _call("color_ratio", eng, whole, "#ff0000", 0) == 0.0


def test_capture_failure_raises():
    eng = _engine_with(_frame())
    eng._capture.capture.return_value = None
    with pytest.raises(ValueError, match="截图失败"):
        _call("bright", eng, RectCoordRef(cx=0.5, cy=0.5))


# ─── bright_segs ────────────────────────────────────────

def test_bright_segs_scans_center_row_of_rect():
    frame = _frame(w=200, h=100)
    for a, b in ((10, 20), (40, 50), (70, 80), (100, 110)):
        frame[48:52, a:b] = _bgr((255, 255, 255))
    eng = _engine_with(frame)
    bar = RectCoordRef(cx=0.5, cy=0.5, w=1.0, h=0.04)
    assert _call("bright_segs", eng, bar, 300, 150) == 4
    # 扫描行偏离亮块 → 0
    off = RectCoordRef(cx=0.5, cy=0.9, w=1.0, h=0.04)
    assert _call("bright_segs", eng, off, 300, 150) == 0


# ─── color_vec ──────────────────────────────────────────

def test_color_vec_returns_dict_and_radius_in_canvas_height():
    frame = _frame(w=200, h=100)
    frame[10:30, 99:102] = _bgr((0, 220, 0))   # 中心正上方，距离 20–40px = 0.2–0.4 画布高
    eng = _engine_with(frame)
    area = RectCoordRef(cx=0.5, cy=0.5, w=1.0, h=1.0)
    center = CircleCoordRef(cx=0.5, cy=0.5, r=0.0)
    v = _call("color_vec", eng, area, center, 120, 255, 40)
    assert v["count"] > 0 and v["deg"] == pytest.approx(0.0, abs=1.0)
    # 环带 0.5–0.9 画布高（50–90px）不含那条绿 → null
    assert _call("color_vec", eng, area, center, 120, 255, 40, 1, 0.5, 0.9) is None
    # center 省略时用 ref 自身中心
    assert _call("color_vec", eng, area, None, 120, 255, 40)["count"] == v["count"]


# ─── find_icons ─────────────────────────────────────────

def test_find_icons_returns_clickable_found_regions_sorted():
    frame = _frame(w=200, h=100, rgb=(30, 30, 30))
    frame[10:50, 10:50] = _bgr((40, 220, 60))     # 40×40
    frame[60:84, 100:124] = _bgr((40, 220, 60))   # 24×24
    eng = _engine_with(frame)
    whole = RectCoordRef(cx=0.5, cy=0.5, w=1.0, h=1.0)
    # min_area 默认 ≈ (0.0072×100)² < 1px → 两块都在；min_bbox 0
    icons = _call("find_icons", eng, whole, 1, 150, 35, 60, 190)
    assert len(icons) == 2
    assert all(isinstance(i, FoundRegion) for i in icons)
    big = icons[0]
    assert big.center_ratios() == (pytest.approx(29.5 / 200), pytest.approx(29.5 / 100))
    assert big.w_ratio == pytest.approx(0.2) and big.h_ratio == pytest.approx(0.4)
    # min_bbox = 0.3 画布高 = 30px → 只剩大块
    assert len(_call("find_icons", eng, whole, 1, 150, 35, 60, 190, 0.0, 0.3)) == 1
    # 空结果是空列表（DSL 里 is_empty 为真）
    assert _call("find_icons", eng, whole, 2, 150, 35) == []


# ─── find_multi_color ───────────────────────────────────

def test_find_multi_color_offsets_in_canvas_width():
    frame = _frame(w=200, h=100)
    frame[10, 10] = _bgr((255, 200, 0))
    frame[10, 14] = _bgr((255, 255, 255))   # dx = 4px = 0.02 画布宽
    frame[16, 10] = _bgr((50, 50, 50))      # dy = 6px = 0.03 画布宽
    eng = _engine_with(frame)
    whole = RectCoordRef(cx=0.5, cy=0.5, w=1.0, h=1.0)
    hit = _call("find_multi_color", eng, whole, "#ffc800", [[0.02, 0, "#ffffff"], [0, 0.03, "#323232"]], 12)
    assert isinstance(hit, FoundRegion)
    assert hit.center_ratios() == (pytest.approx(10 / 200), pytest.approx(10 / 100))
    assert _call("find_multi_color", eng, whole, "#ffc800", [[0.02, 0, "#000000"]], 12) == ""
    with pytest.raises(ValueError, match="偏移点"):
        _call("find_multi_color", eng, whole, "#ffc800", [[1, 2]], 12)


# ─── DSL 端到端 ─────────────────────────────────────────

def test_dsl_eval_and_condition():
    from lvjiang.workflows.grammar import parse_text

    frame = _frame(w=200, h=100)
    frame[:, 100:] = _bgr((46, 204, 113))
    eng = _engine_with(frame)
    region = Region(key="depart", x_ratio=0.5, y_ratio=0.0, w_ratio=0.5, h_ratio=1.0)
    eng._layout.get_scene_regions.return_value = [region]
    eng._layout.get_scene_points.return_value = []
    eng._layout.get_scene_panels.return_value = []
    code = (
        "$btn = [lobby].[depart]\n"
        "$r = color_ratio($btn, \"#2ecc71\", 20)\n"
        "if $r >= 0.9\n"
        "    $state = \"lobby\"\n"
        "else\n"
        "    $state = \"other\"\n"
        "end\n"
    )
    program = parse_text(code)
    eng._procs = dict(program.procs)
    eng._exec_body(program.body)
    assert eng.variables["r"] == pytest.approx(1.0)
    assert eng.variables["state"] == "lobby"
