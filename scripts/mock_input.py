import ctypes
import time
from ctypes import wintypes

user32 = ctypes.windll.user32

INPUT_KEYBOARD = 1
INPUT_MOUSE = 0

KEYEVENTF_KEYUP = 0x0002

MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class INPUT_UNION(ctypes.Union):
    _fields_ = [
        ("ki", KEYBDINPUT),
        ("mi", MOUSEINPUT),
    ]


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [
        ("type", wintypes.DWORD),
        ("u", INPUT_UNION),
    ]


def send_key(vk: int):
    scan = user32.MapVirtualKeyW(vk, 0)  # MAPVK_VK_TO_VSC

    inp = INPUT()
    inp.type = INPUT_KEYBOARD
    inp.ki = KEYBDINPUT(
        wVk=vk,
        wScan=scan,
        dwFlags=0,
        time=0,
        dwExtraInfo=None,
    )

    result_down = user32.SendInput(
        1,
        ctypes.byref(inp),
        ctypes.sizeof(INPUT),
    )

    inp.ki.dwFlags = KEYEVENTF_KEYUP

    result_up = user32.SendInput(
        1,
        ctypes.byref(inp),
        ctypes.sizeof(INPUT),
    )

    return result_down, result_up


def send_left_click():
    down = INPUT()
    down.type = INPUT_MOUSE
    down.mi = MOUSEINPUT(
        dx=0,
        dy=0,
        mouseData=0,
        dwFlags=MOUSEEVENTF_LEFTDOWN,
        time=0,
        dwExtraInfo=None,
    )

    up = INPUT()
    up.type = INPUT_MOUSE
    up.mi = MOUSEINPUT(
        dx=0,
        dy=0,
        mouseData=0,
        dwFlags=MOUSEEVENTF_LEFTUP,
        time=0,
        dwExtraInfo=None,
    )

    result_down = user32.SendInput(
        1,
        ctypes.byref(down),
        ctypes.sizeof(INPUT),
    )

    result_up = user32.SendInput(
        1,
        ctypes.byref(up),
        ctypes.sizeof(INPUT),
    )

    return result_down, result_up


def get_cursor_pos():
    point = wintypes.POINT()
    if user32.GetCursorPos(ctypes.byref(point)):
        return point.x, point.y
    return None


def main():
    print("=" * 60)
    print("Windows Input Probe")
    print("=" * 60)
    print()
    print("5 秒后开始。")
    print("请在这 5 秒内把鼠标移动到游戏窗口。")
    print("程序不会激活窗口、不会按 ESC、不会改变窗口状态。")
    print()

    for i in range(5, 0, -1):
        print(f"{i}...")
        time.sleep(1)

    print()
    print("[1/2] 发送 M 键...")

    result_down, result_up = send_key(ord("M"))

    print(f"      M key down SendInput = {result_down}")
    print(f"      M key up   SendInput = {result_up}")

    if result_down != 1 or result_up != 1:
        print("      WARNING: SendInput 没有成功注入完整的键盘事件。")

    print()
    print("等待 3 秒...")
    time.sleep(3)

    pos = get_cursor_pos()
    print()
    print("[2/2] 发送鼠标左键...")
    print(f"      当前鼠标位置: {pos}")

    result_down, result_up = send_left_click()

    print(f"      LEFT DOWN SendInput = {result_down}")
    print(f"      LEFT UP   SendInput = {result_up}")

    if result_down != 1 or result_up != 1:
        print("      WARNING: SendInput 没有成功注入完整的鼠标事件。")

    print()
    print("测试结束。")


if __name__ == "__main__":
    main()