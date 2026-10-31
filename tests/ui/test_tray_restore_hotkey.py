"""托盘隐藏后的恢复入口必须独立于系统通知区域。

托盘图标是系统 UI：通知区域不登记、资源管理器重启之后，窗口 hide() 了就再
没有任何可见入口，只能杀进程（运行中的任务一起丢）。因此「最小化 / 恢复
窗口」是一个可配置的全局热键（默认 Alt+F8），且不能与其他动作键重复。
"""
from __future__ import annotations


def test_toggle_window_hotkey_defaults_to_alt_f8() -> None:
    """默认键位要避开 F9~F12 这几个动作键。"""
    from lvjiang.core.config.models import HotkeyConfig

    hk = HotkeyConfig()

    assert hk.toggle_window == "Alt+F8"
    assert hk.toggle_window not in {hk.start, hk.pause, hk.stop, hk.record}


def test_toggle_window_hotkey_is_configurable() -> None:
    """用户在热键设置里改成别的组合也要生效，并规范成统一写法。"""
    from lvjiang.core.config.models import HotkeyConfig

    assert HotkeyConfig(toggle_window="ctrl+f8").toggle_window == "Ctrl+F8"
    assert HotkeyConfig(toggle_window="Shift + Alt + F7").toggle_window == \
        "Alt+Shift+F7"
    assert HotkeyConfig(toggle_window="F8").toggle_window == "F8"


def test_invalid_toggle_window_hotkey_falls_back_to_default() -> None:
    """主键越界、修饰键拼错、重复修饰键都回退默认，不留无法注册的键位。"""
    from lvjiang.core.config.models import HotkeyConfig

    for raw in ("F1", "Alt+Space", "Alt+Alt+F8", "", "  "):
        assert HotkeyConfig(toggle_window=raw).toggle_window == "Alt+F8"


def test_duplicate_hotkeys_fall_back_to_defaults() -> None:
    """与其他动作撞键时整组回退，避免 pynput 绑定表互相覆盖。"""
    from lvjiang.core.config.models import HotkeyConfig

    hk = HotkeyConfig(toggle_window="F9")

    assert hk.toggle_window == "Alt+F8"
    assert hk.start == "F9"


def test_hotkey_token_supports_modifier_combos() -> None:
    """pynput 需要 <alt>+<f8> 这种形式，纯 F 键的旧行为不能变。"""
    from lvjiang.core.platforms import hotkey_pynput_token

    assert hotkey_pynput_token("F9") == "<f9>"
    assert hotkey_pynput_token("Alt+F8") == "<alt>+<f8>"
    assert hotkey_pynput_token("Alt + Shift + F7") == "<alt>+<shift>+<f7>"
