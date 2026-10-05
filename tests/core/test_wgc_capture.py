"""后台截图（Windows Graphics Capture）后端测试

WGC 本身只有 Windows 上跑得起来，这里测的是能在任何平台验证的那部分：
**几何对齐**与**后端选择**。

几何对齐是这条路最容易悄悄出错的地方：WGC 交付的帧是窗口可见边界，比
``GetWindowRect`` 窄若干像素，若直接当 mss 的图用，所有按窗口矩形标定过的布局
都会整体偏移几个像素——不会报错，只会点偏。所以这里逐像素验证「帧按偏移贴回
窗口矩形」这件事。
"""

import numpy as np
import pytest

from lvjiang.core.desktop import DesktopCapture, WgcCapture, create_capture_backend
from lvjiang.core.desktop.wgc_capture import wgc_available


def _frame(width: int, height: int, value: int = 200) -> np.ndarray:
    return np.full((height, width, 3), value, dtype=np.uint8)


class TestBackendSelection:
    def test_factory_picks_backend_by_flag(self):
        assert isinstance(create_capture_backend(background=False), DesktopCapture)
        assert isinstance(create_capture_backend(background=True), WgcCapture)


    def test_availability_reports_reason(self):
        """不可用时要给出可执行的原因，而不是只返回 False。"""
        ok, reason = wgc_available()
        assert isinstance(ok, bool)
        if not ok:
            assert reason


class TestFrameAlignment:
    """把 WGC 帧贴回窗口矩形，输出与 mss 那张图逐像素同构"""

    def _backend(self, region, offset):
        cap = WgcCapture()
        cap.set_capture_region(*region)
        cap._hwnd = 1234                      # 跳过真实窗口
        cap._frame_offset = lambda: offset    # 跳过 DWM 查询
        return cap

    def test_offset_frame_is_pasted_at_offset(self):
        """帧比窗口矩形小时，按偏移贴进去，四周补黑。"""
        cap = self._backend((100, 200, 40, 30), (8, 5))
        out = cap._align_to_region(_frame(24, 20))

        assert out.shape == (30, 40, 3)
        # 帧内容落在偏移处
        assert np.all(out[5:25, 8:32] == 200)
        # 不可见边框区域是黑的：那本来就是窗口背后的内容，不该有识别区落上去
        assert np.all(out[:5, :] == 0)
        assert np.all(out[:, :8] == 0)
        assert np.all(out[25:, :] == 0)
        assert np.all(out[:, 32:] == 0)

    def test_same_size_frame_passes_through(self):
        """尺寸一致时原样返回，不做无谓拷贝。"""
        cap = self._backend((0, 0, 20, 10), (0, 0))
        frame = _frame(20, 10)
        assert cap._align_to_region(frame) is frame

    def test_frame_larger_than_region_is_cropped(self):
        """帧比窗口矩形大（偏移为负）时按交集裁剪，不越界。"""
        cap = self._backend((0, 0, 20, 10), (-4, -2))
        out = cap._align_to_region(_frame(30, 16))
        assert out.shape == (10, 20, 3)
        assert np.all(out == 200)

    def test_no_overlap_falls_back_to_raw_frame(self):
        """偏移离谱到两者无交集时返回原帧并告警，不能返回全黑图。"""
        cap = self._backend((0, 0, 20, 10), (999, 999))
        frame = _frame(20, 10)
        assert cap._align_to_region(frame) is frame

    def test_without_region_returns_raw_frame(self):
        """没标窗口矩形（未定位）时不做任何加工。"""
        cap = WgcCapture()
        cap._hwnd = 1234
        frame = _frame(8, 6)
        assert cap._align_to_region(frame) is frame


class TestCaptureGuards:
    def test_capture_without_session_returns_none(self):
        """未建立会话时返回 None 而不是抛异常——上层按 None 走失败分支。"""
        assert WgcCapture().capture(timeout=0.01) is None


    def test_stop_is_idempotent(self):
        cap = WgcCapture()
        cap.stop()
        cap.stop()
        assert cap._latest_frame is None


@pytest.mark.parametrize("width,height", [(1936, 1119), (2560, 1440)])
def test_alignment_preserves_region_size(width, height):
    """无论帧多大，输出尺寸恒等于窗口矩形——画布归一化坐标据此换算。"""
    cap = WgcCapture()
    cap.set_capture_region(0, 0, width, height)
    cap._hwnd = 1
    cap._frame_offset = lambda: (8, 8)
    out = cap._align_to_region(_frame(width - 16, height - 8))
    assert out.shape == (height, width, 3)
