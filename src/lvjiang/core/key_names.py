"""平台无关的工作流标准键名与别名规范化。"""

KEY_ALIASES: dict[str, str] = {
    "ESCAPE": "ESC",
    "RETURN": "ENTER",
    "CONTROL": "CTRL",
    "LMENU": "LALT",
    "RMENU": "RALT",
    "LCONTROL": "LCTRL",
    "RCONTROL": "RCTRL",
    "DEL": "DELETE",
    "INS": "INSERT",
    "PGUP": "PAGEUP",
    "PGDN": "PAGEDOWN",
    "WINDOWS": "WIN",
    **{f"NUM{digit}": f"NUMPAD{digit}" for digit in range(10)},
    # DSL 字符字面量表示的是物理键未按 Shift 时的键帽。需要大写符号时
    # 显式写组合键，例如 press "SHIFT" + "`"。
    "`": "GRAVE",
    "-": "MINUS",
    "=": "EQUALS",
    "[": "LBRACKET",
    "]": "RBRACKET",
    "\\": "BACKSLASH",
    ";": "SEMICOLON",
    "'": "APOSTROPHE",
    ",": "COMMA",
    ".": "PERIOD",
    "/": "SLASH",
}

KNOWN_KEY_NAMES = frozenset({
    *(chr(code) for code in range(ord("A"), ord("Z") + 1)),
    *(str(digit) for digit in range(10)),
    *(f"NUMPAD{digit}" for digit in range(10)),
    *(f"F{index}" for index in range(1, 13)),
    "GRAVE", "MINUS", "EQUALS", "LBRACKET", "RBRACKET",
    "BACKSLASH", "SEMICOLON", "APOSTROPHE", "COMMA", "PERIOD",
    "SLASH", "OEM102",
    "NUMPAD_MULTIPLY", "NUMPAD_ADD", "NUMPAD_SUBTRACT",
    "NUMPAD_DECIMAL", "NUMPAD_DIVIDE", "NUMPAD_ENTER",
    "ESC", "ENTER", "SPACE", "TAB", "BACKSPACE", "DELETE", "INSERT",
    "HOME", "END", "PAGEUP", "PAGEDOWN", "SHIFT", "CTRL", "ALT",
    "LCTRL", "RCTRL", "LSHIFT", "RSHIFT", "LALT", "RALT", "WIN",
    "LWIN", "RWIN", "UP", "DOWN", "LEFT", "RIGHT", "CAPSLOCK",
    "NUMLOCK", "SCROLLLOCK", "PRINTSCREEN", "PAUSE",
})


# pynput 对字符键给出按当前 Shift 状态翻译后的字符。录制器必须把两层
# 字符都还原成同一个物理键，否则 Shift+` 得到的 "~" 会被丢弃。
CHARACTER_TO_KEY_NAME: dict[str, str] = {
    "`": "GRAVE", "~": "GRAVE",
    "-": "MINUS", "_": "MINUS",
    "=": "EQUALS", "+": "EQUALS",
    "[": "LBRACKET", "{": "LBRACKET",
    "]": "RBRACKET", "}": "RBRACKET",
    "\\": "BACKSLASH", "|": "BACKSLASH",
    ";": "SEMICOLON", ":": "SEMICOLON",
    "'": "APOSTROPHE", '"': "APOSTROPHE",
    ",": "COMMA", "<": "COMMA",
    ".": "PERIOD", ">": "PERIOD",
    "/": "SLASH", "?": "SLASH",
}


def normalize_key(name: str) -> str:
    """将用户键名转换为平台无关的标准形式，不支持的名称直接报错。"""
    upper = name.strip().upper()
    resolved = KEY_ALIASES.get(upper, upper)
    if resolved not in KNOWN_KEY_NAMES:
        raise ValueError(
            f"未知按键名: {name!r}，标准化为 {resolved!r}，不在已知按键列表中"
        )
    return resolved
