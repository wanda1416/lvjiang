"""全局热键的键位分区与窗口切换默认值。

F1~F4 归菜单、F5~F7 归插件、F8~F12 归系统级全局热键。全局热键占用裸键后
窗口内 QAction 就再也收不到同一个键，所以区间由数据模型强制，而不是靠使用者
自觉。窗口切换键还兼着「托盘隐藏后唯一恢复入口」的职责，不能被越界配置废掉。
"""
from __future__ import annotations


def test_toggle_window_hotkey_defaults_to_alt_f8() -> None:
    """最小化/恢复默认 Alt+F8：带修饰键，不与游戏/输入法的裸 F 键抢。"""
    from lvjiang.core.config.models import HotkeyConfig

    hk = HotkeyConfig()

    assert hk.toggle_window == "Alt+F8"
    assert hk.toggle_window not in {hk.start, hk.pause, hk.stop, hk.record}


def test_toggle_window_hotkey_is_configurable() -> None:
    """用户在热键设置里改成别的组合也要生效，并规范成统一写法。"""
    from lvjiang.core.config.models import HotkeyConfig

    assert HotkeyConfig(toggle_window="ctrl+f8").toggle_window == "Ctrl+F8"
    assert HotkeyConfig(toggle_window="Shift + Alt + F9").toggle_window == \
        "Alt+Shift+F9"
    assert HotkeyConfig(toggle_window="F8").toggle_window == "F8"


def test_invalid_toggle_window_hotkey_falls_back_to_default() -> None:
    """主键越界（含菜单段 F1~F7）、修饰键拼错、重复修饰键都回退默认。"""
    from lvjiang.core.config.models import HotkeyConfig

    for raw in ("F1", "F7", "Alt+Space", "Alt+Alt+F8", "", "  "):
        assert HotkeyConfig(toggle_window=raw).toggle_window == "Alt+F8"


def test_duplicate_hotkeys_fall_back_to_defaults() -> None:
    """与其他动作撞键时整组回退，避免注册表互相覆盖。"""
    from lvjiang.core.config.models import HotkeyConfig

    hk = HotkeyConfig(toggle_window="F9")

    assert hk.toggle_window == "Alt+F8"
    assert hk.start == "F9"


def test_hotkey_token_supports_modifier_combos() -> None:
    """pynput 需要 <alt>+<f8> 这种形式，纯 F 键的旧行为不能变。"""
    from lvjiang.core.platforms import hotkey_pynput_token

    assert hotkey_pynput_token("F9") == "<f9>"
    assert hotkey_pynput_token("Alt+F8") == "<alt>+<f8>"
    assert hotkey_pynput_token("Alt + Shift + F9") == "<alt>+<shift>+<f9>"
