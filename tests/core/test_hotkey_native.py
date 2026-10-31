"""窗口切换热键走系统 RegisterHotKey，不依赖 pynput 的组合键状态机。

pynput 的 ``GlobalHotKeys`` 用「按下集合恰好等于目标集合」判定，Windows 上
Alt 的 release 会被系统菜单模式吞掉，残留一次后该组合永久失效——实测第一次
能最小化、第二次连回调都没进。因此这条恢复键位改用系统热键，解析逻辑必须与
HotkeyConfig 的键位范围一致。
"""
from __future__ import annotations


def test_parse_alt_f8() -> None:
    """默认键位 Alt+F8 要解析成 MOD_ALT + VK_F8。"""
    from lvjiang.core.desktop.hotkey_native import MOD_ALT, parse_hotkey

    assert parse_hotkey("Alt+F8") == (MOD_ALT, 0x77)


def test_parse_plain_function_key() -> None:
    """无修饰键的写法同样可用（用户可以把窗口切换改回纯 F 键）。"""
    from lvjiang.core.desktop.hotkey_native import parse_hotkey

    assert parse_hotkey("F9") == (0, 0x78)
    assert parse_hotkey("f12") == (0, 0x7B)


def test_parse_modifiers_are_order_insensitive() -> None:
    from lvjiang.core.desktop.hotkey_native import MOD_ALT, MOD_CONTROL, parse_hotkey

    expected = (MOD_ALT | MOD_CONTROL, 0x76)
    assert parse_hotkey("Alt+Ctrl+F7") == expected
    assert parse_hotkey("Ctrl + Alt + F7") == expected


def test_parse_rejects_unsupported_key_names() -> None:
    """注册一个系统热键失败不报错但会静默无效，所以非法写法必须提前拒绝。"""
    from lvjiang.core.desktop.hotkey_native import parse_hotkey

    for raw in ("", "   ", "F1", "Alt+F1", "Alt+Space", "Alt+Bogus", "F8+Alt"):
        assert parse_hotkey(raw) is None
