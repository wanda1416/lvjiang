"""设备截图按框架限流节拍发请求，短暂失败允许重试，内存不足必须立即停止。"""
from unittest.mock import Mock

import pytest

from lvjiang.core import capture_base
from lvjiang.core.ondevice import a11y
from lvjiang.core.ondevice.capture import A11yCapture


class _FakeTime:
    """假时钟：sleep 只推进时间，不真的等，也不污染全局 time 模块。"""

    def __init__(self, now: float = 100.0):
        self.now = now
        self.slept: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def test_capture_paces_requests_above_framework_throttle(monkeypatch):
    """背靠背截图必须由后端补齐间隔

    无障碍 takeScreenshot 在框架侧按「距上一次请求 <=333ms」判定间隔过短。
    「等画面稳定后紧接着识别」就是这种背靠背请求，不补节拍必然拿到
    ERROR_TAKE_SCREENSHOT_INTERVAL_TIME_SHORT，上层只能靠失败重试白等一轮。
    """
    frame = (1, 1, b"\x00\x00\x00\xff")
    clock = _FakeTime()
    monkeypatch.setattr(capture_base, "time", clock)
    monkeypatch.setattr(a11y, "is_ready", lambda: True)
    monkeypatch.setattr(a11y, "screenshot_rgba", lambda _timeout: frame)

    backend = A11yCapture()
    assert backend._grab(1) == frame
    assert clock.slept == []  # 首帧不等待

    clock.now += 0.05  # 50ms 后紧接着再要一帧
    assert backend._grab(1) == frame
    assert clock.slept == [pytest.approx(A11yCapture.MIN_REQUEST_INTERVAL - 0.05)]

    clock.now += 10.0  # 间隔足够长就不再补等待
    assert backend._grab(1) == frame
    assert len(clock.slept) == 1


def test_capture_retries_transient_failure(monkeypatch):
    frame = (1, 1, b"\x00\x00\x00\xff")
    screenshot = Mock(side_effect=[None, frame])
    monkeypatch.setattr(a11y, "is_ready", lambda: True)
    monkeypatch.setattr(a11y, "screenshot_rgba", screenshot)
    monkeypatch.setattr(A11yCapture, "_RETRY_DELAY", 0)
    monkeypatch.setattr(A11yCapture, "MIN_REQUEST_INTERVAL", 0)
    assert A11yCapture()._grab(1) == frame
    assert screenshot.call_count == 2


def test_capture_does_not_swallow_or_retry_out_of_memory(monkeypatch):
    screenshot = Mock(side_effect=MemoryError("capture allocation failed"))
    monkeypatch.setattr(a11y, "is_ready", lambda: True)
    monkeypatch.setattr(a11y, "screenshot_rgba", screenshot)
    with pytest.raises(MemoryError, match="capture allocation failed"):
        A11yCapture()._grab(1)
    screenshot.assert_called_once()
