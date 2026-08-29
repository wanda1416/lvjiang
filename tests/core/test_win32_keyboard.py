"""Win32 键盘消息编码测试。"""

from lvjiang.core.desktop.win32_keyboard import (
    KEY_NAME_TO_VK,
    KEYEVENTF_KEYUP,
    _make_key_lparam,
)
from lvjiang.core.key_names import KNOWN_KEY_NAMES, normalize_key


def test_keydown_lparam_has_scan_without_release_bits():
    value = _make_key_lparam(scan=0x02, flags=0)

    assert value & 0xFFFF == 1
    assert (value >> 16) & 0xFF == 0x02
    assert (value >> 29) & 0b111 == 0


def test_keyup_lparam_sets_previous_and_transition_bits():
    value = _make_key_lparam(scan=0x02, flags=KEYEVENTF_KEYUP)

    assert (value >> 29) & 1 == 0  # context/ALT
    assert (value >> 30) & 1 == 1  # previous state
    assert (value >> 31) & 1 == 1  # transition


def test_main_and_numpad_digits_are_distinct():
    assert normalize_key("1") == "1"
    assert normalize_key("NUM1") == "NUMPAD1"


def test_platform_neutral_key_names_match_win32_mapping():
    assert KNOWN_KEY_NAMES == KEY_NAME_TO_VK.keys()
