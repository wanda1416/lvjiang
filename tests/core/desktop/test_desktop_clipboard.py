"""桌面剪贴板粘贴公共流程测试。"""

from lvjiang.core.desktop import clipboard


def test_paste_via_clipboard_writes_then_sends_ctrl_v(monkeypatch):
    events = []
    monkeypatch.setattr(
        clipboard, "set_clipboard_text", lambda value: events.append(("text", value)))
    monkeypatch.setattr(
        clipboard.time, "sleep", lambda value: events.append(("sleep", value)))

    clipboard.paste_via_clipboard(
        "ABC123",
        lambda key: events.append(("down", key)),
        lambda key: events.append(("up", key)),
    )

    assert events == [
        ("text", "ABC123"),
        ("down", "CTRL"),
        ("down", "V"),
        ("sleep", 0.03),
        ("up", "V"),
        ("up", "CTRL"),
        ("sleep", 0.2),
    ]


def test_paste_releases_ctrl_when_v_down_fails(monkeypatch):
    events = []
    monkeypatch.setattr(clipboard, "set_clipboard_text", lambda _value: None)

    def key_down(key):
        if key == "V":
            raise RuntimeError("send failed")
        events.append(("down", key))

    try:
        clipboard.paste_via_clipboard(
            "ABC123", key_down, lambda key: events.append(("up", key)))
    except RuntimeError:
        pass

    assert events == [("down", "CTRL"), ("up", "CTRL")]


def test_paste_attempts_to_release_ctrl_when_v_up_fails(monkeypatch):
    events = []
    monkeypatch.setattr(clipboard, "set_clipboard_text", lambda _value: None)
    monkeypatch.setattr(clipboard.time, "sleep", lambda _value: None)

    def key_up(key):
        events.append(("up", key))
        if key == "V":
            raise RuntimeError("release failed")

    try:
        clipboard.paste_via_clipboard(
            "ABC123", lambda key: events.append(("down", key)), key_up)
    except RuntimeError:
        pass

    assert events == [
        ("down", "CTRL"), ("down", "V"), ("up", "V"), ("up", "CTRL"),
    ]
