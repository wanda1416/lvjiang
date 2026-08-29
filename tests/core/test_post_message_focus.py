"""后台模式绝不主动抢焦点。

后台投递的全部意义就是「不抢焦点」：用户只承诺目标窗口可见（副屏常驻
即可），焦点归他自己用。历史上这里默认会在每次投递前 SetForegroundWindow
把目标窗口拉到前台再还原，后台模式因此退化成「只是不移动光标」——副屏
挂机边用电脑的场景直接废掉。这组用例把「默认不激活」钉住。
"""

from __future__ import annotations

import sys

import pytest

pytest.importorskip("lvjiang.core.desktop.post_message")

from lvjiang.core.desktop.post_message import PostMessageInput  # noqa: E402


def _backend() -> PostMessageInput:
    return PostMessageInput(hwnd=1234)


def test_background_backend_does_not_activate_by_default():
    assert _backend().activate_before_send is False


def test_background_backend_is_marked_background():
    backend = _backend()
    assert backend.background_mode is True
    assert backend.target_hwnd == 1234


@pytest.mark.parametrize(
    "method, args",
    [
        ("click_screen", (100, 200)),
        ("move_screen", (100, 200)),
        ("drag_screen", (10, 20, 30, 40)),
    ],
)
def test_no_input_path_activates_the_target_window(monkeypatch, method, args):
    """点击/移动/拖拽都不得把目标窗口拉到前台。"""
    from lvjiang.core.desktop import post_message as pm

    activated: list[int] = []
    monkeypatch.setattr(
        pm, "activate_window", lambda hwnd, restore=True: activated.append(hwnd),
        raising=False,
    )
    seen: list[bool] = []
    for name in ("postmessage_click", "postmessage_move", "postmessage_drag"):
        monkeypatch.setattr(
            pm, name,
            lambda *a, activate=False, **kw: seen.append(activate),
            raising=False,
        )
    monkeypatch.setattr(pm, "screen_to_client_logical", lambda h, x, y: (x, y))

    backend = _backend()
    backend.before_click_wait = (0, 0)
    backend.after_click_wait = (0, 0)
    backend.mouse_move_duration = (0, 0)
    getattr(backend, method)(*args)

    assert activated == [], f"{method} 不应激活目标窗口"
    assert seen and not any(seen), f"{method} 传了 activate=True"


@pytest.mark.skipif(sys.platform != "win32", reason="SendInput 仅 Windows")
def test_foreground_backend_still_activates():
    """前台模式(SendInput)必须保留激活——系统级注入本就打给焦点窗口。"""
    from lvjiang.core.desktop.send_input import SendInputInput

    assert SendInputInput().activate_before_send is True
