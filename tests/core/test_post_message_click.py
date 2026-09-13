"""PostMessage 后台点击长按分派。"""

from unittest.mock import MagicMock

from lvjiang.core.config import InputSimConfig
from lvjiang.core.desktop import post_message as post_module
from lvjiang.core.desktop import win32_util
from lvjiang.core.desktop.post_message import PostMessageInput


def test_backend_forwards_hold_to_postmessage(monkeypatch):
    backend = PostMessageInput(
        input_sim=InputSimConfig(
            click_random_offset=0,
            before_click_wait=(0, 0),
            after_click_wait=(0, 0),
        ),
        hwnd=123,
    )
    send = MagicMock()
    monkeypatch.setattr(post_module, "postmessage_click", send)
    monkeypatch.setattr(
        post_module, "screen_to_client_logical", lambda _hwnd, x, y: (x, y))
    monkeypatch.setattr(post_module.time, "sleep", lambda _s: None)

    backend.click_screen(10, 20, hold=1.4)

    send.assert_called_once_with(123, 10, 20, activate=False, hold=1.4)


def test_win32_postmessage_holds_between_down_and_up(monkeypatch):
    user32 = MagicMock()
    sleeps = []
    monkeypatch.setattr(win32_util, "_user32", user32)
    monkeypatch.setattr(
        win32_util, "resolve_message_target", lambda hwnd, _x, _y: hwnd)
    monkeypatch.setattr(win32_util, "make_lparam", lambda _x, _y: 99)
    monkeypatch.setattr("time.sleep", sleeps.append)

    win32_util.postmessage_click(123, 10, 20, hold=1.4)

    assert sleeps == [0.03, 1.4]
    assert user32.PostMessageW.call_count == 3
