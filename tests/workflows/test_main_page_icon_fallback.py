"""主页判定：文本通道全灭时，按环境用唯一指定的主页图标兜底。

防止的真实回归：「限时」等入口文字在缩放、低对比、过场帧下会被 OCR 整批
漏读，``is_in_main_page()`` 因此返回 0，导航与批量任务把明明还在主页的画面
判成未知页面（误报日志 + 反复 pause）。图标模板是独立识别通道，文字漏读时
仍应确认主页；但只认本端指定的那一个图标（安卓 ditu / 桌面 dongzuo），
不整场景扫描——否则以后新增的模板绑定会被算进主页判据，跨场景重复的图标
还会直接造成误判。
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from lvjiang.core.layout_models import Region, TemplateBinding
from lvjiang.core.recognizers import template_locator as tl
from lvjiang.workflows.errors import WorkflowUserError
from lvjiang.workflows.grammar import parse_text
from tests.workflows.conftest import make_engine

_ROOT = Path(__file__).parents[2] / "config" / "system" / "workflows"
_SIZE = (640, 360)


def _icon(shape: str = "circle", size: int = 40) -> np.ndarray:
    """有结构的合成图标（非纯色，TM_CCOEFF_NORMED 才有意义）。

    匹配走灰度，所以两种图标必须是**结构**不同，靠颜色区分不了。
    """
    img = np.zeros((size, size, 3), dtype=np.uint8)
    if shape == "circle":
        cv2.circle(img, (size // 2, size // 2), size // 3, (60, 220, 60), -1)
        cv2.rectangle(img, (size // 4, size // 4), (size // 2, size // 2),
                      (255, 255, 255), -1)
    else:  # bars
        for i in range(3):
            y = size // 4 + i * (size // 6)
            cv2.rectangle(img, (2, y), (size - 3, y + 3), (230, 230, 230), -1)
    cv2.line(img, (0, size - 1), (size - 1, 0), (0, 0, 255), 2)
    return img


def _frame(icon: np.ndarray | None, at=(300, 120)) -> np.ndarray:
    rng = np.random.default_rng(7)
    frame = rng.integers(0, 60, size=(_SIZE[1], _SIZE[0], 3), dtype=np.uint8)
    if icon is not None:
        h, w = icon.shape[:2]
        frame[at[1]:at[1] + h, at[0]:at[0] + w] = icon
    return frame


def _binding(name: str) -> TemplateBinding:
    return TemplateBinding(name, 0.8, _SIZE[0], _SIZE[1])


def _engine(
    run_env: str,
    frame: np.ndarray,
    *,
    main_regions: list[Region],
    bag_text: str = "",
):
    engine = make_engine(run_env=run_env)
    engine._procs.update(parse_text(
        (_ROOT / "subcall" / "page_detection.wf").read_text(encoding="utf-8")).procs)
    engine._capture.capture.return_value = frame
    engine._capture.get_capture_size.return_value = _SIZE
    text_regions = [Region(key, 0.0, 0.0, 0.1, 0.1) for key in
                    ("menu", "huodong", "wulinlu", "xianshi")]
    regions = {
        "game_main_page": text_regions + main_regions,
        "bag_detail": [
            Region("zhizao", 0.0, 0.0, 0.1, 0.1),
            Region("zhengli", 0.1, 0.0, 0.1, 0.1),
            Region("sub_baoguo", 0.2, 0.0, 0.1, 0.1),
        ],
    }
    engine._layout.get_scene_regions.side_effect = (
        lambda scene: regions.get(scene, []))
    # 文本通道一律读不到；需要构造背包现场时让每个框都读到「整理」
    # （「整理」不在主页入口词表里，因此不会让主页文本判据命中）
    engine._ocr.recognize.side_effect = (
        lambda crop, **kw: ([SimpleNamespace(text=bag_text, confidence=1.0)]
                            if bag_text else []))
    return engine


def _in_main_page(engine) -> int:
    engine._exec_body(parse_text("call $in_main = is_in_main_page()\n").body)
    return engine.variables["in_main"]


def _store(tmp_path, monkeypatch, **icons: np.ndarray) -> None:
    for name, icon in icons.items():
        cv2.imwrite(str(tmp_path / f"{name}.png"), icon)
    monkeypatch.setattr(tl, "_STORE", tl.TemplateStore(tmp_path))


@pytest.mark.parametrize(("run_env", "region_key"), [
    ("android", "ditu"),
    ("desktop", "dongzuo"),
])
def test_icon_template_confirms_main_page_when_text_is_missed(
    tmp_path, monkeypatch, run_env, region_key,
):
    icon = _icon()
    _store(tmp_path, monkeypatch, icon=icon)
    engine = _engine(
        run_env, _frame(icon),
        main_regions=[Region(region_key, 0.0, 0.0, 1.0, 1.0,
                             template=_binding("icon"))],
    )

    assert _in_main_page(engine) == 1


def test_missing_binding_is_a_config_error_not_a_silent_miss(tmp_path, monkeypatch):
    """显式指定的区域必须绑定模板：没绑定是配置错误，不能静默当成「不在主页」。"""
    icon = _icon()
    _store(tmp_path, monkeypatch, icon=icon)
    engine = _engine(
        "android", _frame(icon),
        main_regions=[Region("ditu", 0.0, 0.0, 1.0, 1.0)],
    )

    with pytest.raises(WorkflowUserError, match="未绑定模板"):
        _in_main_page(engine)


def test_android_ignores_desktop_icon_even_when_bound(tmp_path, monkeypatch):
    """只扫本端指定的区域：画面上命中别的已绑定图标不算主页。"""
    _store(tmp_path, monkeypatch,
           android_icon=_icon("circle"), desktop_icon=_icon("bars"))
    engine = _engine(
        "android", _frame(_icon("bars")),
        main_regions=[
            Region("ditu", 0.0, 0.0, 1.0, 1.0, template=_binding("android_icon")),
            Region("dongzuo", 0.0, 0.0, 1.0, 1.0, template=_binding("desktop_icon")),
        ],
    )

    assert _in_main_page(engine) == 0


def test_icon_fallback_still_excludes_bag_page(tmp_path, monkeypatch):
    """图标在背包页也能命中时，背包页不得被当成主页。"""
    icon = _icon()
    _store(tmp_path, monkeypatch, icon=icon)
    engine = _engine(
        "android", _frame(icon),
        main_regions=[Region("ditu", 0.0, 0.0, 1.0, 1.0,
                             template=_binding("icon"))],
        bag_text="整理",
    )

    assert _in_main_page(engine) == 0
