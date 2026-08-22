"""Windows 游戏键盘注入诊断工具。

逐项测试 PostMessage / SendMessage / SendInput 的不同键盘事件形式，
用于定位“字母键有效、主键盘数字键无效”的接收路径差异。

用法：
    python scripts/manual-tests/diag_keyboard_input.py "窗口标题关键字" 1

每个测试只在用户按 Enter 后执行，执行后输入 y/n 记录游戏是否响应。
建议先以普通权限运行；若全部失败，再以管理员权限运行一次对照。
"""

from __future__ import annotations

import ctypes
import sys
import time
from ctypes import wintypes

user32 = ctypes.windll.user32

WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_CHAR = 0x0102
INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_SCANCODE = 0x0008
MAPVK_VK_TO_VSC = 0


ULONG_PTR = wintypes.WPARAM


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class INPUTUNION(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", INPUTUNION)]


class GUITHREADINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("hwndActive", wintypes.HWND),
        ("hwndFocus", wintypes.HWND),
        ("hwndCapture", wintypes.HWND),
        ("hwndMenuOwner", wintypes.HWND),
        ("hwndMoveSize", wintypes.HWND),
        ("hwndCaret", wintypes.HWND),
        ("rcCaret", wintypes.RECT),
    ]


def list_windows() -> list[tuple[int, str]]:
    result: list[tuple[int, str]] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def callback(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        result.append((int(hwnd), buf.value))
        return True

    user32.EnumWindows(callback, 0)
    return result


def class_name(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(wintypes.HWND(hwnd), buf, len(buf))
    return buf.value


def focused_hwnd(hwnd: int) -> int:
    pid = wintypes.DWORD()
    thread_id = user32.GetWindowThreadProcessId(
        wintypes.HWND(hwnd), ctypes.byref(pid))
    info = GUITHREADINFO(cbSize=ctypes.sizeof(GUITHREADINFO))
    if thread_id and user32.GetGUIThreadInfo(thread_id, ctypes.byref(info)):
        return int(info.hwndFocus or info.hwndActive or hwnd)
    return hwnd


def key_codes(key: str) -> tuple[int, int, str | None]:
    upper = key.upper()
    if len(upper) != 1 or not (upper.isalpha() or upper.isdigit()):
        raise ValueError("本诊断脚本仅接受单个字母或主键盘数字")
    vk = ord(upper)
    scan = int(user32.MapVirtualKeyW(vk, MAPVK_VK_TO_VSC))
    char = key if key.isdigit() else upper.lower()
    return vk, scan, char


def key_lparam(scan: int, key_up: bool = False) -> int:
    value = 1 | ((scan & 0xFF) << 16)
    if key_up:
        value |= (1 << 30) | (1 << 31)
    return value


def post_key(hwnd: int, vk: int, scan: int, *, hold: float = 0.08,
             send: bool = False, char: str | None = None) -> None:
    fn = user32.SendMessageW if send else user32.PostMessageW
    ctypes.set_last_error(0)
    down = fn(wintypes.HWND(hwnd), WM_KEYDOWN, vk,
              key_lparam(scan, key_up=False))
    if char is not None:
        fn(wintypes.HWND(hwnd), WM_CHAR, ord(char), 1)
    time.sleep(hold)
    up = fn(wintypes.HWND(hwnd), WM_KEYUP, vk,
            key_lparam(scan, key_up=True))
    print(f"    API 返回: down={down!r}, up={up!r}, "
          f"last_error={ctypes.get_last_error()}")


def send_input_key(vk: int, scan: int, *, scan_mode: bool,
                   hybrid_vk: bool = False, hold: float = 0.08) -> None:
    flags = KEYEVENTF_SCANCODE if scan_mode else 0
    event_vk = vk if (not scan_mode or hybrid_vk) else 0

    def emit(event_flags: int) -> int:
        inp = INPUT(type=INPUT_KEYBOARD)
        inp.ki = KEYBDINPUT(
            wVk=event_vk,
            wScan=scan if scan_mode else 0,
            dwFlags=event_flags,
            time=0,
            dwExtraInfo=0,
        )
        ctypes.set_last_error(0)
        return int(user32.SendInput(
            1, ctypes.byref(inp), ctypes.sizeof(INPUT)))

    down = emit(flags)
    time.sleep(hold)
    up = emit(flags | KEYEVENTF_KEYUP)
    print(f"    SendInput 返回: down={down}, up={up}, "
          f"last_error={ctypes.get_last_error()}")


def activate(hwnd: int) -> None:
    user32.ShowWindow(wintypes.HWND(hwnd), 9)  # SW_RESTORE
    user32.SetForegroundWindow(wintypes.HWND(hwnd))
    time.sleep(0.5)


def run_case(label: str, action) -> str:
    input(f"\n[{label}] 按 Enter 执行；请先确保角色处于可释放技能状态...")
    action()
    answer = input("    游戏是否响应？[y/n/?] ").strip().lower() or "?"
    return answer


def main() -> None:
    keyword = sys.argv[1] if len(sys.argv) > 1 else input("窗口标题关键字: ")
    key = sys.argv[2] if len(sys.argv) > 2 else "1"
    matches = [(h, t) for h, t in list_windows()
               if keyword.lower() in t.lower()]
    if not matches:
        raise SystemExit(f"未找到标题包含 {keyword!r} 的窗口")
    if len(matches) > 1:
        for index, (_, title) in enumerate(matches):
            print(f"{index}: {title}")
        hwnd, title = matches[int(input("选择窗口编号: "))]
    else:
        hwnd, title = matches[0]

    vk, scan, char = key_codes(key)
    activate(hwnd)
    focus = focused_hwnd(hwnd)
    print(f"目标: {title!r} top=0x{hwnd:X}({class_name(hwnd)})")
    print(f"焦点: 0x{focus:X}({class_name(focus)})")
    print(f"按键: {key!r}, vk=0x{vk:02X}, scan=0x{scan:02X}")

    cases = [
        ("A PostMessage 顶层，标准按下/释放",
         lambda: post_key(hwnd, vk, scan)),
        ("B PostMessage 焦点窗口，标准按下/释放",
         lambda: post_key(focus, vk, scan)),
        ("C PostMessage 焦点窗口，按住 300ms",
         lambda: post_key(focus, vk, scan, hold=0.3)),
        ("D PostMessage 焦点窗口，附加 WM_CHAR",
         lambda: post_key(focus, vk, scan, char=char)),
        ("E SendMessage 焦点窗口，标准按下/释放",
         lambda: post_key(focus, vk, scan, send=True)),
        ("F SendInput VK 模式",
         lambda: send_input_key(vk, scan, scan_mode=False)),
        ("G SendInput 标准扫描码模式（wVk=0）",
         lambda: send_input_key(vk, scan, scan_mode=True)),
        ("H SendInput 当前生产混合模式（wVk+scan）",
         lambda: send_input_key(vk, scan, scan_mode=True, hybrid_vk=True)),
    ]
    results = [(label[0], run_case(label, action))
               for label, action in cases]
    print("\n结果: " + ", ".join(f"{tag}={answer}"
                                  for tag, answer in results))


if __name__ == "__main__":
    main()
