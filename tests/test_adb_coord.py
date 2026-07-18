"""ADB 模式坐标链回归测试

ADB 后端下截图与输入同为设备物理像素、原点左上，window_left/top 恒为 0。
本测试用假 capture 注入固定分辨率，验证归一化坐标 → 屏幕像素的换算链
（_region_to_screen / _point_to_screen / _ratio_to_screen）结果 == 画布内像素坐标，
确保坐标链在 ADB 模式（无客户区偏移）下正确。
"""

from lvjiang.core.region_config import Layout, CanvasConfig, Region, Point
from lvjiang.workflows.base import BaseWorkflow


class _FakeCapture:
    """仅实现 get_capture_size 的假截图器"""

    def __init__(self, size: tuple[int, int]):
        self._size = size

    def get_capture_size(self) -> tuple[int, int]:
        return self._size


def _make_wf(device_size=(1080, 1920), canvas=None, regions=None, points=None):
    layout = Layout(name="t")
    layout.set_canvas(canvas or CanvasConfig())  # 默认满画布 (0,0,1,1)
    if regions:
        layout.set_scene_regions("s", regions)
    if points:
        layout.set_scene_points("s", points)
    return BaseWorkflow(
        capture=_FakeCapture(device_size),
        ocr=None,
        input_ctrl=None,
        layout=layout,
        window_left=0,   # ADB 模式恒为 0
        window_top=0,
    )


def test_region_to_screen_full_canvas_no_jitter():
    # 满画布、无抖动：区域中心归一化比例 × 设备分辨率
    region = Region(key="r", name="r", x_ratio=0.5, y_ratio=0.5, w_ratio=0.1, h_ratio=0.1)
    wf = _make_wf(device_size=(1080, 1920), regions=[region])
    x, y = wf._region_to_screen(region, jitter=False)
    # 中心 = (0.5 + 0.1/2) = 0.55
    assert x == int(0.55 * 1080)   # 594
    assert y == int(0.55 * 1920)   # 1056


def test_point_to_screen_zero_radius_deterministic():
    # r_ratio=0 消除随机偏移，point 中心即精确像素
    point = Point(key="p", cx_ratio=0.3, cy_ratio=0.7, r_ratio=0.0)
    wf = _make_wf(device_size=(1080, 1920), points=[point])
    x, y = wf._point_to_screen(point)
    assert x == int(0.3 * 1080)   # 324
    assert y == int(0.7 * 1920)   # 1344


def test_ratio_to_screen_full_canvas():
    wf = _make_wf(device_size=(1080, 1920))
    x, y = wf._ratio_to_screen(0.25, 0.8)
    assert x == int(0.25 * 1080)   # 270
    assert y == int(0.8 * 1920)    # 1536


def test_ratio_to_screen_offset_canvas():
    # 非满画布：验证画布原点 + 画布尺寸缩放同样成立
    canvas = CanvasConfig(x_ratio=0.1, y_ratio=0.05, w_ratio=0.8, h_ratio=0.9)
    wf = _make_wf(device_size=(1080, 1920), canvas=canvas)
    x, y = wf._ratio_to_screen(0.5, 0.5)
    # sx = 0.1 + 0.5*0.8 = 0.5 ; sy = 0.05 + 0.5*0.9 = 0.5
    assert x == int(0.5 * 1080)   # 540
    assert y == int(0.5 * 1920)   # 960


def test_region_to_screen_offset_canvas_no_jitter():
    canvas = CanvasConfig(x_ratio=0.1, y_ratio=0.1, w_ratio=0.8, h_ratio=0.8)
    region = Region(key="r", name="r", x_ratio=0.0, y_ratio=0.0, w_ratio=0.5, h_ratio=0.5)
    wf = _make_wf(device_size=(1000, 2000), canvas=canvas, regions=[region])
    x, y = wf._region_to_screen(region, jitter=False)
    # canvas_x=100, canvas_w=800 ; cx = 100 + (0 + 0.25)*800 = 300
    # canvas_y=200, canvas_h=1600 ; cy = 200 + (0 + 0.25)*1600 = 600
    assert x == 300
    assert y == 600
