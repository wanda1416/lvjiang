"""脚本录制器的键盘名称转换。"""

from lvjiang.core import macro_recorder
from lvjiang.workflows.grammar import parse_text


class _KeyCode:
    def __init__(self, *, vk=None, char=None, name=None):
        self.vk = vk
        self.char = char
        self.name = name


def test_windows_numpad_digit_uses_vk_even_when_char_is_present(monkeypatch):
    monkeypatch.setattr(macro_recorder.sys, "platform", "win32")

    assert macro_recorder._pynput_key_to_dsl_name(
        _KeyCode(vk=0x68, char="8")) == "NUMPAD8"


def test_windows_numpad_keys_without_char_are_not_dropped(monkeypatch):
    monkeypatch.setattr(macro_recorder.sys, "platform", "win32")

    expected = {
        0x60: "NUMPAD0",
        0x69: "NUMPAD9",
        0x6A: "NUMPAD_MULTIPLY",
        0x6B: "NUMPAD_ADD",
        0x6D: "NUMPAD_SUBTRACT",
        0x6E: "NUMPAD_DECIMAL",
        0x6F: "NUMPAD_DIVIDE",
    }
    assert {
        vk: macro_recorder._pynput_key_to_dsl_name(_KeyCode(vk=vk))
        for vk in expected
    } == expected


def test_windows_main_keyboard_digit_remains_distinct(monkeypatch):
    monkeypatch.setattr(macro_recorder.sys, "platform", "win32")

    assert macro_recorder._pynput_key_to_dsl_name(
        _KeyCode(vk=0x38, char="8")) == "8"


def test_windows_numpad_press_and_release_are_emitted(monkeypatch):
    monkeypatch.setattr(macro_recorder.sys, "platform", "win32")
    recorder = macro_recorder.MacroRecorder(
        {}, None, None, 0, 0, reserved_keys=set())
    recorder._recording = True
    key = _KeyCode(vk=0x68, char="8")

    recorder._on_key_press(key)
    recorder._on_key_release(key)

    assert recorder._lines[0] == 'press "NUMPAD8" down'
    assert recorder._lines[-1] == 'press "NUMPAD8" up'


def test_non_windows_keycode_does_not_apply_windows_vk_mapping(monkeypatch):
    monkeypatch.setattr(macro_recorder.sys, "platform", "linux")

    assert macro_recorder._pynput_key_to_dsl_name(
        _KeyCode(vk=0x68, char="8")) == "8"


def test_low_precision_compacts_press_hold_and_following_wait():
    compacted = macro_recorder._compact_low_precision_lines([
        'press "Q" down',
        "wait 0.235",
        'press "Q" up',
        "wait 0.6",
        'press "E" down',
    ])

    assert compacted == [
        'press "Q" hold 0.235 after wait 0.6',
        'press "E" down',
    ]
    parse_text("\n".join(compacted))


def test_low_precision_does_not_collapse_an_interleaved_key():
    compacted = macro_recorder._compact_low_precision_lines([
        'press "Q" down',
        "wait 0.1",
        'press "W" down',
        "wait 0.2",
        'press "W" up',
        "wait 0.3",
        'press "Q" up',
        "wait 0.4",
    ])

    assert compacted == [
        'press "Q" down after wait 0.1',
        'press "W" hold 0.2 after wait 0.3',
        'press "Q" up after wait 0.4',
    ]
    parse_text("\n".join(compacted))


def test_low_precision_compacts_raw_mouse_event_as_press_hold():
    lines = [
        "place (0.5, 0.5)",
        'press "MOUSE_LEFT" down',
        "wait 0.2",
        'press "MOUSE_LEFT" up',
    ]

    assert macro_recorder._compact_low_precision_lines(lines) == [
        "place (0.5, 0.5)",
        'press "MOUSE_LEFT" hold 0.2',
    ]
