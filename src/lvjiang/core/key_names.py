"""平台无关的工作流标准键名与别名规范化。"""

KEY_ALIASES: dict[str, str] = {
    "ESCAPE": "ESC",
    "RETURN": "ENTER",
    "CONTROL": "CTRL",
    "LMENU": "LALT",
    "RMENU": "RALT",
    "LSHIFT": "SHIFT",
    "RSHIFT": "SHIFT",
    "LCONTROL": "LCTRL",
    "RCONTROL": "RCTRL",
    "DEL": "DELETE",
    "INS": "INSERT",
    "PGUP": "PAGEUP",
    "PGDN": "PAGEDOWN",
    "WINDOWS": "WIN",
    **{f"NUM{digit}": f"NUMPAD{digit}" for digit in range(10)},
}

KNOWN_KEY_NAMES = frozenset({
    *(chr(code) for code in range(ord("A"), ord("Z") + 1)),
    *(str(digit) for digit in range(10)),
    *(f"NUMPAD{digit}" for digit in range(10)),
    *(f"F{index}" for index in range(1, 13)),
    "ESC", "ENTER", "SPACE", "TAB", "BACKSPACE", "DELETE", "INSERT",
    "HOME", "END", "PAGEUP", "PAGEDOWN", "SHIFT", "CTRL", "ALT",
    "LCTRL", "RCTRL", "LSHIFT", "RSHIFT", "LALT", "RALT", "WIN",
    "LWIN", "RWIN", "UP", "DOWN", "LEFT", "RIGHT", "CAPSLOCK",
    "NUMLOCK", "SCROLLLOCK", "PRINTSCREEN", "PAUSE",
})


def normalize_key(name: str) -> str:
    """将用户键名转换为平台无关的标准形式，不支持的名称直接报错。"""
    upper = name.strip().upper()
    resolved = KEY_ALIASES.get(upper, upper)
    if resolved not in KNOWN_KEY_NAMES:
        raise ValueError(
            f"未知按键名: {name!r}，标准化为 {resolved!r}，不在已知按键列表中"
        )
    return resolved
