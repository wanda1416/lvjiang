"""find … by image 模板定位测试

1. 语法：by image 解析为 match_mode="image"；scan/recognize 拒绝
2. 定位器：合成帧里贴模板 → 命中坐标；录制分辨率不同 → 自适配缩放命中
3. 引擎：find 产出 FoundRegion（画布归一化，可 click）；where 作分数门槛；未命中 ""
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import cv2
import numpy as np
import pytest

from lvjiang.core.layout_models import FoundRegion, Region
from lvjiang.core.recognizers import template_locator as tl
from lvjiang.workflows.engine.signals import WorkflowUserError
from lvjiang.workflows.grammar import Find, parse_text
from lvjiang.workflows.grammar.ast_nodes import Literal
from tests.workflows.conftest import make_engine

# ─── 语法 ───────────────────────────────────────────────

def test_find_by_image_parses():
    prog = parse_text('find as $icon by image "extract_icon" where confidence >= 0.7\n')
    node = prog.body[0]
    assert isinstance(node, Find)
    assert node.by.match_mode == "image"
    assert isinstance(node.by.target, Literal) and node.by.target.value == "extract_icon"
    assert node.where.min_confidence.value == 0.7


def test_find_by_image_with_area():
    node = parse_text('find [map].[canvas] as $icon by image "extract_icon"\n').body[0]
    assert node.search_scene == "map" and node.search_region == "canvas"
    assert node.by.match_mode == "image"


@pytest.mark.parametrize("code", [
    'scan [s].[a] as $v by image "x"\n',
    'scan [s].[p][0][0] as $v by image "x"\n',
    'recognize [s].[a] as $v by image "x"\n',
    'recognize [s].[p][0][0] as $v by image "x"\n',
])
def test_scan_recognize_reject_by_image(code):
    # transformer 里抛的 WorkflowUserError 会被 lark 包成 VisitError（与 'full by' 的既有行为一致）
    from lark.exceptions import VisitError

    with pytest.raises((WorkflowUserError, VisitError)) as ei:
        parse_text(code)
    err = ei.value.orig_exc if isinstance(ei.value, VisitError) else ei.value
    assert isinstance(err, WorkflowUserError) and "by image" in str(err)


# ─── 定位器 ─────────────────────────────────────────────

def _icon(size=40) -> np.ndarray:
    """一个有结构的合成图标（非纯色，CCOEFF 才有意义）"""
    img = np.zeros((size, size, 3), dtype=np.uint8)
    cv2.circle(img, (size // 2, size // 2), size // 3, (40, 220, 60), -1)
    cv2.rectangle(img, (size // 4, size // 4), (size // 2, size // 2), (255, 255, 255), -1)
    cv2.line(img, (0, 0), (size - 1, size - 1), (0, 0, 255), 2)
    return img


def _frame_with_icon(icon: np.ndarray, at=(300, 120), size=(640, 360)) -> np.ndarray:
    rng = np.random.default_rng(7)
    frame = rng.integers(0, 60, size=(size[1], size[0], 3), dtype=np.uint8)
    h, w = icon.shape[:2]
    frame[at[1]:at[1] + h, at[0]:at[0] + w] = icon
    return frame


def _store(tmp_path: Path, name: str, icon: np.ndarray, record_w: int | None = None) -> tl.TemplateStore:
    cv2.imwrite(str(tmp_path / f"{name}.png"), icon)
    if record_w:
        (tmp_path / f"{name}.json").write_text(json.dumps({"recordW": record_w, "recordH": 0}))
    return tl.TemplateStore(tmp_path)


def test_locate_exact(tmp_path):
    icon = _icon(40)
    frame = _frame_with_icon(icon, at=(300, 120))
    tpl = _store(tmp_path, "ico", icon).get("ico")
    hit = tl.locate(frame, tpl, 0, 0, 639, 359, scales=[1.0], min_score=0.8)
    assert hit is not None
    assert hit.score > 0.95
    assert (hit.cx, hit.cy) == (pytest.approx(300 + 19.5), pytest.approx(120 + 19.5))
    assert (hit.w, hit.h) == (40, 40)


def test_locate_respects_search_region_and_min_score(tmp_path):
    icon = _icon(40)
    frame = _frame_with_icon(icon, at=(300, 120))
    tpl = _store(tmp_path, "ico", icon).get("ico")
    # 搜索区域不含图标 → 分数低 → None
    assert tl.locate(frame, tpl, 0, 0, 200, 100, scales=[1.0], min_score=0.8) is None
    # 区域比模板还小 → 该尺度跳过 → None
    assert tl.locate(frame, tpl, 0, 0, 10, 10, scales=[1.0], min_score=0.0) is None


def test_locate_scale_adaptation(tmp_path):
    """模板按 2× 宽录制（80px），当前帧里图标 40px：adaptive_scales(640, 1280) 含 0.5 → 命中"""
    icon40 = _icon(40)
    icon80 = cv2.resize(icon40, (80, 80), interpolation=cv2.INTER_LINEAR)
    frame = _frame_with_icon(icon40, at=(300, 120))
    tpl = _store(tmp_path, "big", icon80, record_w=1280).get("big")
    assert tpl.record_w == 1280
    scales = tl.adaptive_scales(640, tpl.record_w)
    assert 0.5 in scales and 1.0 in scales
    hit = tl.locate(frame, tpl, 0, 0, 639, 359, scales=scales, min_score=0.8)
    assert hit is not None and hit.scale == pytest.approx(0.5)
    assert (hit.cx, hit.cy) == (pytest.approx(319.5, abs=1), pytest.approx(139.5, abs=1))
    # 只试 1.0 则尺寸不对 → 低分
    miss = tl.locate(frame, tpl, 0, 0, 639, 359, scales=[1.0], min_score=0.8)
    assert miss is None


def test_adaptive_scales_near_one():
    assert tl.adaptive_scales(1000, 1010) == [0.9, 1.0, 1.1]
    assert tl.adaptive_scales(1000, 0) == [0.9, 1.0, 1.1]


def test_store_missing_and_cache(tmp_path):
    store = tl.TemplateStore(tmp_path)
    assert store.get("nope") is None
    assert store.get("late") is None          # 此时文件不存在 → miss 被缓存
    cv2.imwrite(str(tmp_path / "late.png"), _icon(20))
    assert store.get("late") is None          # 仍是缓存的 miss
    store.invalidate()
    assert store.get("late") is not None
    assert store.get("late.png") is store.get("late")


def test_alpha_template_composited_on_black(tmp_path):
    rgba = np.zeros((20, 20, 4), dtype=np.uint8)
    rgba[..., :3] = 255
    rgba[..., 3] = 0
    rgba[5:15, 5:15, 3] = 255
    cv2.imwrite(str(tmp_path / "a.png"), rgba)
    tpl = tl.TemplateStore(tmp_path).get("a")
    assert tpl.gray[0, 0] == 0 and tpl.gray[10, 10] == 255


# ─── 引擎 ───────────────────────────────────────────────

def _engine_with(frame: np.ndarray, canvas=(0, 0, 1, 1)):
    eng = make_engine()
    eng._capture.capture.return_value = frame
    eng._capture.get_capture_size.return_value = (frame.shape[1], frame.shape[0])
    eng._layout.get_canvas.return_value = MagicMock(
        x_ratio=canvas[0], y_ratio=canvas[1], w_ratio=canvas[2], h_ratio=canvas[3])
    return eng


def _run(eng, code: str):
    program = parse_text(code)
    eng._procs = dict(program.procs)
    eng._exec_body(program.body)
    return eng.variables


@pytest.fixture
def synthetic_store(tmp_path, monkeypatch):
    icon = _icon(40)
    store = _store(tmp_path, "ico", icon)
    monkeypatch.setattr(tl, "_STORE", store)
    return icon


def test_engine_find_image_full_canvas(synthetic_store):
    frame = _frame_with_icon(synthetic_store, at=(300, 120), size=(640, 360))
    eng = _engine_with(frame)
    v = _run(eng, 'find as $hit by image "ico"\n')
    hit = v["hit"]
    assert isinstance(hit, FoundRegion) and hit.text == "ico"
    cx, cy = hit.center_ratios()
    assert cx == pytest.approx(319.5 / 640, abs=1e-3) and cy == pytest.approx(139.5 / 360, abs=1e-3)
    assert hit.w_ratio == pytest.approx(40 / 640) and hit.h_ratio == pytest.approx(40 / 360)


def test_engine_find_image_canvas_offset(synthetic_store):
    """画布是截图右下 50%：命中坐标要相对画布归一化"""
    frame = _frame_with_icon(synthetic_store, at=(400, 200), size=(640, 360))
    eng = _engine_with(frame, canvas=(0.5, 0.5, 0.5, 0.5))
    hit = _run(eng, 'find as $hit by image "ico"\n')["hit"]
    cx, cy = hit.center_ratios()
    assert cx == pytest.approx((419.5 - 320) / 320, abs=1e-3)
    assert cy == pytest.approx((219.5 - 180) / 180, abs=1e-3)


def test_engine_find_image_in_area_and_miss(synthetic_store):
    frame = _frame_with_icon(synthetic_store, at=(300, 120), size=(640, 360))
    eng = _engine_with(frame)
    right = Region(key="right", x_ratio=0.4, y_ratio=0.0, w_ratio=0.6, h_ratio=1.0)
    left = Region(key="left", x_ratio=0.0, y_ratio=0.0, w_ratio=0.4, h_ratio=1.0)
    eng._layout.get_scene_regions.return_value = [right, left]
    v = _run(eng, 'find [s].[right] as $a by image "ico"\nfind [s].[left] as $b by image "ico"\n')
    assert isinstance(v["a"], FoundRegion)
    assert v["b"] == ""


def test_engine_find_image_where_threshold(synthetic_store, tmp_path):
    """精确贴图分数恰为 1.0，先把图标模糊到 (0.8, 0.999) 之间，再验 where 门槛两侧"""
    frame = _frame_with_icon(synthetic_store, at=(300, 120), size=(640, 360))
    frame[120:160, 300:340] = cv2.GaussianBlur(frame[120:160, 300:340], (5, 5), 1.5)
    tpl = tl.TemplateStore(tmp_path).get("ico")
    probe = tl.locate(frame, tpl, 0, 0, 639, 359, scales=[1.0], min_score=0.0)
    assert 0.8 < probe.score < 0.999, probe.score
    eng = _engine_with(frame)
    v = _run(eng, 'find as $loose by image "ico"\nfind as $strict by image "ico" where confidence >= 0.999\n')
    assert isinstance(v["loose"], FoundRegion)
    assert v["strict"] == ""


def test_engine_find_image_missing_template_raises(synthetic_store):
    eng = _engine_with(_frame_with_icon(synthetic_store))
    with pytest.raises(ValueError, match="模板 nope 不存在"):
        _run(eng, 'find as $x by image "nope"\n')


def test_engine_find_image_then_click(synthetic_store):
    frame = _frame_with_icon(synthetic_store, at=(300, 120), size=(640, 360))
    eng = _engine_with(frame)
    eng._input_sim.region_jitter_ratio = 0.0   # make_engine 的 input_sim 是 MagicMock，抖动会把坐标算成 mock
    _run(eng, 'find as $hit by image "ico"\nclick $hit\n')
    eng._input.click_screen.assert_called_once()
    x, y = eng._input.click_screen.call_args.args[:2]
    # 命中中心 (319.5, 139.5) → 截图像素
    assert abs(x - 319.5) <= 1 and abs(y - 139.5) <= 1, (x, y)
