"""设备截图短暂失败允许重试，内存不足必须立即停止，不能被当成未命中。"""
from unittest.mock import Mock

import pytest

from lvjiang.core.ondevice import a11y
from lvjiang.core.ondevice.capture import A11yCapture


def test_capture_retries_transient_failure(monkeypatch):
    frame = (1, 1, b"\x00\x00\x00\xff")
    screenshot = Mock(side_effect=[None, frame])
    monkeypatch.setattr(a11y, "is_ready", lambda: True)
    monkeypatch.setattr(a11y, "screenshot_rgba", screenshot)
    monkeypatch.setattr(A11yCapture, "_RETRY_DELAY", 0)
    assert A11yCapture()._grab(1) == frame
    assert screenshot.call_count == 2


def test_capture_does_not_swallow_or_retry_out_of_memory(monkeypatch):
    screenshot = Mock(side_effect=MemoryError("capture allocation failed"))
    monkeypatch.setattr(a11y, "is_ready", lambda: True)
    monkeypatch.setattr(a11y, "screenshot_rgba", screenshot)
    with pytest.raises(MemoryError, match="capture allocation failed"):
        A11yCapture()._grab(1)
    screenshot.assert_called_once()
