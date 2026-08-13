"""操作层绑定校验单元测试

覆盖「点击 / 拖拽的目标在当前布局未绑定」与「等待参数未定义」两类路径：
过去只记一条 ERROR 就往下走，操作静默空转，后续步骤会在错误的页面上
继续点；现在一律抛错中断。
"""

import pytest

from lvjiang.config import DelayParam, InputSimConfig
from lvjiang.core.scene_registry import Arrow, CanvasConfig, Point, Region
from lvjiang.workflows.base.actions import _ActionMixin
from lvjiang.workflows.base.coords import _CoordMixin

SCENE = "activity_jianghu"


class _FakeLayout:
    def __init__(self, regions=None, points=None, arrows=None):
        self._regions = regions or []
        self._points = points or []
        self._arrows = arrows or []

    def get_canvas(self):
        return CanvasConfig()

    def get_scene_regions(self, scene_key):
        return list(self._regions) if scene_key == SCENE else []

    def get_scene_points(self, scene_key):
        return list(self._points) if scene_key == SCENE else []

    def get_scene_arrows(self, scene_key):
        return list(self._arrows) if scene_key == SCENE else []


class _FakeCapture:
    def __init__(self, size=(1000, 500)):
        self._size = size

    def get_capture_size(self):
        return self._size


class _FakeInput:
    def __init__(self):
        self.clicks = []
        self.drags = []

    def click_screen(self, x, y, tag):
        self.clicks.append((x, y, tag))

    def drag_screen(self, x1, y1, x2, y2, tag, duration=None, hold=None):
        self.drags.append((x1, y1, x2, y2, tag))


class _Actor(_ActionMixin, _CoordMixin):
    """把操作与坐标换算两个 Mixin 拼成可独立实例化的最小对象"""

    def __init__(self, layout, capture_size=(1000, 500), delay_params=None, input_sim=None):
        self._layout = layout
        self._capture = _FakeCapture(capture_size)
        self._input = _FakeInput()
        self._delay_params = delay_params or {}
        self._input_sim = input_sim or InputSimConfig()
        self._window_left = 0
        self._window_top = 0

    def _stop_check(self):
        return True


def _region(key):
    return Region(key=key, x_ratio=0.0, y_ratio=0.0, w_ratio=0.5, h_ratio=0.5)


# ─── 点击：目标未绑定必须报错，不能静默空转 ────────────────────

def test_click_region_unbound_raises():
    actor = _Actor(_FakeLayout(regions=[_region("btn_ok")]))
    with pytest.raises(ValueError, match="label_1"):
        actor.click_region(SCENE, "label_1")
    assert actor._input.clicks == []


def test_click_region_bound_still_clicks():
    actor = _Actor(_FakeLayout(regions=[_region("btn_ok")]))
    actor.click_region(SCENE, "btn_ok")
    assert len(actor._input.clicks) == 1


def test_click_any_unbound_raises():
    """region 和 point 都查不到才报错"""
    actor = _Actor(_FakeLayout(regions=[_region("btn_ok")],
                               points=[Point(key="p1", cx_ratio=0.5, cy_ratio=0.5)]))
    actor.click_any(SCENE, "btn_ok")
    actor.click_any(SCENE, "p1")
    assert len(actor._input.clicks) == 2
    with pytest.raises(ValueError, match="btn_cancel"):
        actor.click_any(SCENE, "btn_cancel")


def test_click_point_unbound_raises():
    actor = _Actor(_FakeLayout())
    with pytest.raises(ValueError, match="p1"):
        actor.click_point(SCENE, "p1")


def test_click_region_without_capture_size_raises():
    """截屏后端不可用时坐标全是错的，同样不许静默跳过"""
    actor = _Actor(_FakeLayout(regions=[_region("btn_ok")]), capture_size=(0, 0))
    with pytest.raises(ValueError, match="截屏尺寸"):
        actor.click_region(SCENE, "btn_ok")


# ─── 拖拽：arrow 与其两端 point 都要绑定 ───────────────────────

def test_drag_arrow_unbound_raises():
    actor = _Actor(_FakeLayout())
    with pytest.raises(ValueError, match="scroll_down"):
        actor.drag_arrow(SCENE, "scroll_down")


def test_drag_arrow_missing_from_point_raises():
    layout = _FakeLayout(arrows=[Arrow(key="scroll_down", from_key="p_top",
                                       to_cx_ratio=0.5, to_cy_ratio=0.9)])
    actor = _Actor(layout)
    with pytest.raises(ValueError, match="p_top"):
        actor.drag_arrow(SCENE, "scroll_down")


def test_drag_arrow_missing_to_point_raises():
    """吸附态终点：to_key 指向的 point 丢了也要报错"""
    layout = _FakeLayout(
        points=[Point(key="p_top", cx_ratio=0.5, cy_ratio=0.1)],
        arrows=[Arrow(key="scroll_down", from_key="p_top", to_key="p_bottom")],
    )
    actor = _Actor(layout)
    with pytest.raises(ValueError, match="p_bottom"):
        actor.drag_arrow(SCENE, "scroll_down")
    assert actor._input.drags == []


def test_drag_arrow_absolute_target_works():
    """绝对态终点：不依赖 point，正常拖拽"""
    layout = _FakeLayout(
        points=[Point(key="p_top", cx_ratio=0.5, cy_ratio=0.1)],
        arrows=[Arrow(key="scroll_down", from_key="p_top",
                      to_cx_ratio=0.5, to_cy_ratio=0.9)],
    )
    actor = _Actor(layout)
    actor.drag_arrow(SCENE, "scroll_down")
    assert len(actor._input.drags) == 1


# ─── 等待：未定义的命名参数不再当成「不等待」 ──────────────────

def test_wait_delay_unknown_name_raises():
    actor = _Actor(_FakeLayout())
    with pytest.raises(ValueError, match="page_refresh_wait"):
        actor.wait_delay("page_refresh_wait")


def test_wait_delay_defined_name_works():
    actor = _Actor(_FakeLayout(),
                   delay_params={"page_refresh_wait": DelayParam(range=(0.0, 0.0))})
    actor.wait_delay("page_refresh_wait")  # _stop_check 恒 True，立即返回
