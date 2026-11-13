"""设备端输入后端：drag hold 两段 stroke + ESC/HOME 全局动作

PC 上没有 Chaquopy，a11y / shell 的桥接函数整体 monkeypatch 成记录器，
只验证 InputBackend → 通道原语的分派与参数换算。
"""
from __future__ import annotations

import pytest

from lvjiang.core.config import InputSimConfig
from lvjiang.core.ondevice import a11y, shell
from lvjiang.core.ondevice import input as dev_input


@pytest.fixture
def calls(monkeypatch):
    """记录对 a11y / shell 桥的调用；time.sleep 置空，前后延迟不真等"""
    log: list[tuple] = []
    monkeypatch.setattr(dev_input.time, "sleep", lambda *_: None)
    # 手势桥接「成功返回 None，失败返回原因」，别用 True 冒充成功：那样每次调用
    # 都会被当成失败并把 True 当原因打出来。
    for name in ("tap", "swipe", "long_press", "hold_move"):
        monkeypatch.setattr(a11y, name, lambda *a, _n=name: log.append((_n, *a)))
    for name in ("back", "home"):
        monkeypatch.setattr(a11y, name, lambda *a, _n=name: log.append((_n, *a)) or True)
    for name in ("tap", "swipe"):
        monkeypatch.setattr(shell, name, lambda *a, _n=name: log.append(("shell." + _n, *a)) or "")
    monkeypatch.setattr(shell, "key_event", lambda code: log.append(("shell.key_event", code)) or True)
    return log


def _cfg() -> InputSimConfig:
    # 固定移动时长、零抖动，便于断言毫秒数
    return InputSimConfig(mouse_move_duration=(0.2, 0.2), click_random_offset=0,
                          before_click_wait=(0, 0), after_click_wait=(0, 0))


# ─── A11yInput ───────────────────────────────────────────

def test_a11y_drag_without_hold_is_plain_swipe(calls):
    inp = dev_input.A11yInput(_cfg())
    inp.drag_screen(10, 20, 110, 220, duration=0.5)
    assert calls == [("swipe", 10, 20, 110, 220, 500)]


def test_a11y_click_hold_uses_long_press(calls):
    inp = dev_input.A11yInput(_cfg())
    inp.click_screen(10, 20, hold=1.4)
    assert calls == [("long_press", 10, 20, 1400)]


def test_a11y_drag_with_hold_uses_two_stroke_hold_move(calls):
    inp = dev_input.A11yInput(_cfg())
    inp.drag_screen(10, 20, 110, 220, duration=0.3, hold=2.0)
    assert calls == [("hold_move", 10, 20, 110, 220, 300, 2000)]


def test_a11y_drag_default_duration_from_input_sim(calls):
    inp = dev_input.A11yInput(_cfg())
    inp.drag_screen(0, 0, 50, 50, hold=0.5)
    assert calls == [("hold_move", 0, 0, 50, 50, 200, 500)]


def test_a11y_hold_zero_or_negative_is_swipe(calls):
    inp = dev_input.A11yInput(_cfg())
    inp.drag_screen(0, 0, 50, 50, duration=0.1, hold=0)
    inp.drag_screen(0, 0, 50, 50, duration=0.1, hold=-1)
    assert calls == [("swipe", 0, 0, 50, 50, 100)] * 2


def test_a11y_esc_is_back_and_home_is_home(calls):
    inp = dev_input.A11yInput(_cfg())
    inp.key_down("ESC")
    inp.key_up("ESC")
    inp.key_down("HOME")
    inp.key_up("HOME")
    assert calls == [("back",), ("home",)]


def test_a11y_other_keys_not_supported(calls):
    inp = dev_input.A11yInput(_cfg())
    with pytest.raises(NotImplementedError, match="仅 ESC/HOME"):
        inp.key_down("W")
    with pytest.raises(NotImplementedError):
        inp.key_up("W")
    assert calls == []


def test_a11y_failed_global_action_does_not_raise(calls, monkeypatch, capsys):
    monkeypatch.setattr(a11y, "back", lambda: False)
    dev_input.A11yInput(_cfg()).key_down("ESC")
    assert "未成功" in capsys.readouterr().out


def test_a11y_gesture_failure_prints_the_device_reason(calls, monkeypatch, capsys):
    """手势失败要带出设备端的原因，不能再自己猜「无障碍开关未开？」。

    失败有四种互不相干的来源（服务未连接 / dispatchGesture 被拒 / 被真实触摸
    打断 / 等回调超时），猜错一次就会把排查引到完全无关的方向。
    """
    monkeypatch.setattr(
        a11y, "tap",
        lambda *_a: "手势被系统取消（真实触摸、系统弹窗或其他无障碍服务打断）")
    dev_input.A11yInput(_cfg()).click_screen(10, 20)

    out = capsys.readouterr().out
    assert "点击未成功：手势被系统取消" in out
    assert "无障碍开关未开" not in out


def test_a11y_gesture_success_prints_nothing(calls, capsys):
    """成功是 None：别把成功当失败刷日志。"""
    dev_input.A11yInput(_cfg()).click_screen(10, 20)

    assert "未成功" not in capsys.readouterr().out


# ─── ShellInput ──────────────────────────────────────────

def test_shell_drag_hold_merges_into_swipe_duration(calls):
    inp = dev_input.ShellInput(_cfg())
    inp.drag_screen(10, 20, 110, 220, duration=0.3, hold=2.0)
    assert calls == [("shell.swipe", 10, 20, 110, 220, 2300)]


def test_shell_click_hold_uses_stationary_swipe(calls):
    inp = dev_input.ShellInput(_cfg())
    inp.click_screen(10, 20, hold=1.4)
    assert calls == [("shell.swipe", 10, 20, 10, 20, 1400)]


def test_shell_esc_home_keyevents(calls):
    inp = dev_input.ShellInput(_cfg())
    inp.key_down("ESC")
    inp.key_down("HOME")
    assert calls == [("shell.key_event", 4), ("shell.key_event", 3)]


# ─── press 指令端到端：KeyStateRegistry → 设备后端 ───────

def test_press_esc_via_key_state_registry(calls):
    from lvjiang.workflows.engine.key_state import KeyStateRegistry

    reg = KeyStateRegistry(dev_input.A11yInput(_cfg()))
    reg.key_down("ESC")
    reg.key_up("ESC")
    assert calls == [("back",)]
