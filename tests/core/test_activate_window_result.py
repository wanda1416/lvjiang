"""`activate_window()` 必须如实报告激活结果。

它原来无条件 `return True`：等待前台的循环超时后既不记日志也不改返回值。
后果是前台输入静悄悄地发给**别的**窗口——日志里 key_down/key_up 一切正常，
游戏毫无反应，排查时完全看不出输入落在哪（已经为此绕过一次）。而且
`apps/premium/ui/gather.py` 早就写了 `if not activate_window(...): raise`，
那个提示因为返回值永远为真而成了死代码。

Win32 调用全部 monkeypatch：这些用例在 Linux 上也要能跑，断言的是契约而不是
某台机器的窗口行为。
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from loguru import logger

from lvjiang.core.desktop import win32_util

TARGET = 0x1A2B3C
OTHER = 0x998877


@pytest.fixture
def user32(monkeypatch):
    """假 user32：前台窗口由 foreground 列表逐次给出，不真的 sleep。"""
    fake = MagicMock()
    fake.GetCurrentThreadId.return_value = 1001
    fake.GetWindowThreadProcessId.return_value = 2002
    fake.AttachThreadInput.return_value = 1
    monkeypatch.setattr(win32_util, "_user32", fake)
    monkeypatch.setattr(win32_util.time, "sleep", lambda _s: None)
    monkeypatch.setattr(win32_util, "window_title", lambda hwnd: f"win-{hwnd}")
    win32_util._activation_warned.clear()
    return fake


@pytest.fixture
def warnings() -> list[str]:
    """收 loguru 的 WARNING；caplog 只接标准库 logging，收不到 loguru。"""
    collected: list[str] = []
    sink_id = logger.add(collected.append, level="WARNING")
    try:
        yield collected
    finally:
        logger.remove(sink_id)


def test_returns_true_when_the_window_really_comes_to_front(user32) -> None:
    user32.GetForegroundWindow.side_effect = [OTHER, OTHER, TARGET]

    assert win32_util.activate_window(TARGET, restore=False) is True


def test_returns_false_when_the_foreground_never_changes(user32, warnings) -> None:
    """SetForegroundWindow 被 Windows 拒绝时要报 False，并说清两边是谁。"""
    user32.GetForegroundWindow.return_value = OTHER

    result = win32_util.activate_window(TARGET, restore=False)

    assert result is False
    text = "".join(warnings)
    assert f"目标 {TARGET}" in text
    assert f"实际前台 {OTHER}" in text


def test_repeated_failures_warn_once_then_drop_to_debug(user32, warnings) -> None:
    """一次任务里每个动作都要激活；失败时不能刷几百条同样的告警。"""
    user32.GetForegroundWindow.return_value = OTHER

    for _ in range(5):
        win32_util.activate_window(TARGET, restore=False)

    assert "".join(warnings).count("请切回游戏窗口后重试") == 1


def test_a_success_rearms_the_warning(user32, warnings) -> None:
    """激活成功过之后再失败，仍然要显眼地报一次，不能被之前的记录吞掉。"""
    user32.GetForegroundWindow.return_value = OTHER
    win32_util.activate_window(TARGET, restore=False)
    user32.GetForegroundWindow.return_value = TARGET
    assert win32_util.activate_window(TARGET, restore=False) is True

    user32.GetForegroundWindow.return_value = OTHER
    warnings.clear()
    win32_util.activate_window(TARGET, restore=False)

    assert "".join(warnings).count("请切回游戏窗口后重试") == 1


def test_restore_mode_still_reports_the_activation_itself(user32) -> None:
    """restore=True 随后把前台让回去，返回值仍指激活那一步成功与否。"""
    user32.GetForegroundWindow.side_effect = [OTHER, OTHER, TARGET]

    assert win32_util.activate_window(TARGET, restore=True) is True


def test_win32_call_failure_is_reported_as_failure(user32, warnings) -> None:
    """Win32 调用本身抛了也算失败，不能当成功。"""
    user32.GetForegroundWindow.return_value = OTHER
    user32.SetForegroundWindow.side_effect = OSError("拒绝访问")

    assert win32_util.activate_window(TARGET, restore=False) is False
    assert "激活窗口失败" in "".join(warnings)
