"""桌面端 Win32 键盘输入基础设施

提供 SendInput / PostMessage 共用的键盘注入能力，
与 win32_util.py（鼠标/DPI/窗口）平级拆分，避免单文件膨胀。
"""

import ctypes
from ctypes import wintypes

from .win32_util import _Input, _InputUnion, _KeyBdInput, _user32

# ─── SendInput 键盘结构 ────────────────────────────────────────

# 复用 win32_util 中统一的 INPUT 结构体（type + mi/ki union），
# 并已在 win32_util 中为 SendInput 显式声明 argtypes。
# 非 Windows 平台允许 import，但实际调用 Win32 函数会因 _user32 为 None 报错


# 键盘事件常量
INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_SCANCODE = 0x0008
KEYEVENTF_EXTENDEDKEY = 0x0001

# PostMessage 键盘消息常量
_WM_KEYDOWN = 0x0100
_WM_KEYUP = 0x0101
_WM_SYSKEYDOWN = 0x0104
_WM_SYSKEYUP = 0x0105

# 扩展键集合（需要 KEYEVENTF_EXTENDEDKEY 标志）
# 仅包含扫描码带 0xE0 前缀的键；左修饰键（LSHIFT=0x2A/LCTRL=0x1D/LALT=0x38）
# 无 E0 前缀，不应设置此标志。SHIFT/CTRL/ALT 映射到左侧 VK，同样不包含。
_EXTENDED_KEYS = {
    0xA3, 0xA5,        # RCONTROL, RMENU (RALT)
    0x90,        # NUMLOCK
    0x91,        # SCROLL
    0x24, 0x25,  # HOME, END
    0x21, 0x22,  # PRIOR (PGUP), NEXT (PGDN)
    0x26, 0x28,  # UP, DOWN
    0x23, 0x27,  # LEFT, RIGHT
    0x2D, 0x2E,  # INSERT, DELETE
    0x5B, 0x5C,  # LWIN, RWIN
}


def _make_key_lparam(scan: int, flags: int) -> int:
    """构造键盘消息的 lparam

    位布局：
    - 0-15:  repeat count = 1
    - 16-23: scan code
    - 24:    extended key flag
    - 25-28: context code = 0
    - 29:    previous key state (down=0, up=1)
    - 30:    transition state (down=0, up=1)
    """
    repeat_count = 1
    extended = 1 if (flags & KEYEVENTF_EXTENDEDKEY) else 0
    prev_state = 1 if (flags & KEYEVENTF_KEYUP) else 0
    transition = 1 if (flags & KEYEVENTF_KEYUP) else 0
    return (
        repeat_count
        | (scan << 16)
        | (extended << 24)
        | (prev_state << 29)
        | (transition << 31)
    )


# ─── 键名 → VK 映射 ──────────────────────────────────────────

# 别名表：用户可能写的非标准名称 → 标准名称（normalize_key 内部查表）
_KEY_ALIASES: dict[str, str] = {
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
}

# 标准键名 → VK code
KEY_NAME_TO_VK: dict[str, int] = {
    # 字母 A-Z
    **{chr(c): c for c in range(ord("A"), ord("Z") + 1)},
    # 数字 0-9
    **{str(d): 0x30 + d for d in range(10)},
    # 功能键 F1-F12
    **{f"F{i}": 0x70 + i - 1 for i in range(1, 13)},
    # 特殊键
    "ESC": 0x1B,
    "ENTER": 0x0D,
    "SPACE": 0x20,
    "TAB": 0x09,
    "BACKSPACE": 0x08,
    "DELETE": 0x2E,
    "INSERT": 0x2D,
    "HOME": 0x24,
    "END": 0x23,
    "PAGEUP": 0x21,
    "PAGEDOWN": 0x22,
    # 修饰键
    "SHIFT": 0xA0,
    "CTRL": 0xA2,
    "ALT": 0xA4,
    "LCTRL": 0xA2,
    "RCTRL": 0xA3,
    "LSHIFT": 0xA0,
    "RSHIFT": 0xA1,
    "LALT": 0xA4,
    "RALT": 0xA5,
    "WIN": 0x5B,
    "LWIN": 0x5B,
    "RWIN": 0x5C,
    # 方向键
    "UP": 0x26,
    "DOWN": 0x28,
    "LEFT": 0x25,
    "RIGHT": 0x27,
    # 其他
    "CAPSLOCK": 0x14,
    "NUMLOCK": 0x90,
    "SCROLLLOCK": 0x91,
    "PRINTSCREEN": 0x2C,
    "PAUSE": 0x13,
}


def normalize_key(name: str) -> str:
    """统一键名到大写标准形式

    支持别名：Escape→ESC, Control→CTRL 等。
    不在映射表中的键名直接报错。
    """
    upper = name.strip().upper()
    # 先查别名，再查标准表
    resolved = _KEY_ALIASES.get(upper, upper)
    if resolved not in KEY_NAME_TO_VK:
        raise ValueError(f"未知按键名: {name!r}，标准化为 {resolved!r}，不在已知按键列表中")
    return resolved


# ─── 发送函数 ─────────────────────────────────────────────────

def send_keyboard_input(vk: int, scan: int, flags: int) -> None:
    """通过 SendInput 发送单次键盘事件

    调用方应传入含 KEYEVENTF_SCANCODE 的 flags（以扫描码为主键码），
    这样消息型应用（记事本/浏览器，由系统把扫描码翻译成 VK 码）和
    DirectInput 游戏（直接读扫描码）都能命中。
    wVk 一并填充，避免个别依赖 VK 的应用漏判。

    Args:
        vk: 虚拟键码
        scan: 扫描码（由 MapVirtualKeyW 计算）
        flags: 按下(0)或 KEYEVENTF_KEYUP(释放)，可组合 KEYEVENTF_SCANCODE /
               KEYEVENTF_EXTENDEDKEY
    """
    ki = _KeyBdInput(
        wVk=vk,
        wScan=scan,
        dwFlags=flags,
        time=0,
        dwExtraInfo=ctypes.POINTER(ctypes.c_ulong)(ctypes.c_ulong(0)),
    )
    inp = _Input(type=INPUT_KEYBOARD, ii=_InputUnion(ki=ki))
    _user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(inp))


def post_keyboard_input(hwnd: int, vk: int, scan: int, flags: int) -> None:
    """通过 PostMessage 向目标窗口发送单次键盘事件

    Args:
        hwnd: 目标窗口句柄
        vk: 虚拟键码
        scan: 扫描码
        flags: 0（按下）或 KEYEVENTF_KEYUP（释放），可组合 KEYEVENTF_EXTENDEDKEY
    """
    lparam = _make_key_lparam(scan, flags)
    is_extended = bool(flags & KEYEVENTF_EXTENDEDKEY)
    if flags & KEYEVENTF_KEYUP:
        msg = _WM_SYSKEYUP if is_extended else _WM_KEYUP
    else:
        msg = _WM_SYSKEYDOWN if is_extended else _WM_KEYDOWN
    _user32.PostMessageW(wintypes.HWND(hwnd), msg, vk, lparam)


def key_to_vk_scan(key: str) -> tuple[int, int]:
    """将标准化键名转为 (vk, scan) 二元组

    扫描码通过 MapVirtualKeyW 计算，确保 DirectInput 游戏响应。
    """
    vk = KEY_NAME_TO_VK[key]
    scan = 0
    if _user32 is not None:
        _user32.MapVirtualKeyW.argtypes = [wintypes.UINT, wintypes.UINT]
        _user32.MapVirtualKeyW.restype = wintypes.UINT
        scan = _user32.MapVirtualKeyW(vk, 0)
    return vk, scan


def is_extended_key(key: str) -> bool:
    """判断标准化键名是否为扩展键（需要 KEYEVENTF_EXTENDEDKEY 标志）"""
    vk = KEY_NAME_TO_VK[key]
    return vk in _EXTENDED_KEYS
