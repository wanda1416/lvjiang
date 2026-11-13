"""旋转时拒绝过渡帧和旧手势，防止重启后点击桌面错误位置。"""
import pytest

from lvjiang.core.ondevice import a11y
from lvjiang.core.ondevice.capture import A11yCapture
from lvjiang.core.ondevice.input import A11yInput


def test_rotation_after_recognition_blocks_gesture(monkeypatch):
    geometry = [1, 8, 4]
    monkeypatch.setattr(a11y, "display_geometry", lambda: tuple(geometry))
    capture = A11yCapture()
    monkeypatch.setattr(capture, "_grab", lambda timeout: (8, 4, bytes(8 * 4 * 4)))
    assert capture.capture().shape == (4, 8, 3)
    controls = A11yInput()
    controls.geometry_check = capture.validate_geometry
    taps = []
    monkeypatch.setattr(a11y, "tap", lambda x, y: taps.append((x, y)))
    geometry[:] = [0, 4, 8]
    with pytest.raises(RuntimeError, match="旧识别坐标"):
        controls._tap(6, 2)
    assert not taps
    assert capture._size is None
    monkeypatch.setattr(capture, "_grab", lambda timeout: (4, 8, bytes(8 * 4 * 4)))
    capture.capture()
    controls._tap(2, 6)
    assert taps == [(2, 6)]


def test_rotation_during_screenshot_discards_transition_frame(monkeypatch):
    geometry = [1, 8, 4]
    monkeypatch.setattr(a11y, "display_geometry", lambda: tuple(geometry))
    capture = A11yCapture()
    def grab(timeout):
        geometry[:] = [0, 4, 8]
        return 8, 4, bytes(8 * 4 * 4)
    monkeypatch.setattr(capture, "_grab", grab)
    assert capture.capture() is None
    assert capture._size is None
