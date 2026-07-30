"""桌面端 Win32 工具函数

提供 SendInput / PostMessage 共用的底层 Win32 基础设施，
以及窗口枚举工具（list_visible_windows）。
"""

import ctypes
from ctypes import wintypes

from loguru import logger

# ─── SendInput 基础设施 ────────────────────────────────────────

_user32 = ctypes.windll.user32

PUL = ctypes.POINTER(ctypes.c_ulong)


class _MouseInput(ctypes.Structure):
    _fields_ = [
        ("dx", ctypes.c_long),
        ("dy", ctypes.c_long),
        ("mouseData", ctypes.c_ulong),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", PUL),
    ]


class _InputUnion(ctypes.Union):
    _fields_ = [("mi", _MouseInput)]


class _Input(ctypes.Structure):
    _fields_ = [("type", ctypes.c_ulong), ("ii", _InputUnion)]


# 鼠标事件常量
_INPUT_MOUSE = 0
_MOUSEEVENTF_MOVE = 0x0001
_MOUSEEVENTF_LEFTDOWN = 0x0002
_MOUSEEVENTF_LEFTUP = 0x0004

# PostMessage 鼠标消息常量
_WM_MOUSEMOVE = 0x0200
_WM_LBUTTONDOWN = 0x0201
_WM_LBUTTONUP = 0x0202
_MK_LBUTTON = 0x0001
_WM_NCHITTEST = 0x0084
_HTCLIENT = 1


def send_mouse_event(flags: int, dx: int = 0, dy: int = 0):
    """通过 SendInput 发送鼠标事件"""
    mi = _MouseInput(
        dx=dx, dy=dy, mouseData=0, dwFlags=flags, time=0,
        dwExtraInfo=PUL(ctypes.c_ulong(0)),
    )
    ii = _InputUnion(mi=mi)
    inp = _Input(type=_INPUT_MOUSE, ii=ii)
    _user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(inp))


def smooth_move_to(x: int, y: int, duration: float):
    """平滑移动鼠标到指定位置（分步 SetCursorPos）"""
    import time
    steps = max(int(duration / 0.01), 1)
    point = wintypes.POINT()
    _user32.GetCursorPos(ctypes.byref(point))
    sx, sy = point.x, point.y
    for i in range(1, steps + 1):
        ratio = i / steps
        cx = int(sx + (x - sx) * ratio)
        cy = int(sy + (y - sy) * ratio)
        _user32.SetCursorPos(cx, cy)
        time.sleep(duration / steps)


def make_lparam(x: int, y: int) -> int:
    """将 (x, y) 打包为 PostMessage LPARAM（低位 x，高位 y）"""
    return (y << 16) | (x & 0xFFFF)


def screen_to_client(hwnd: int, screen_x: int, screen_y: int) -> tuple[int, int]:
    """屏幕坐标 → 窗口客户区坐标"""
    pt = wintypes.POINT(screen_x, screen_y)
    _user32.ScreenToClient(hwnd, ctypes.byref(pt))
    return pt.x, pt.y


def postmessage_click(hwnd: int, client_x: int, client_y: int):
    """通过 PostMessage 向窗口发送一次点击（不移动光标）"""
    import time
    lparam = make_lparam(client_x, client_y)
    _user32.PostMessageW(hwnd, _WM_MOUSEMOVE, 0, lparam)
    time.sleep(0.03)
    _user32.PostMessageW(hwnd, _WM_LBUTTONDOWN, _MK_LBUTTON, lparam)
    time.sleep(0.05)
    _user32.PostMessageW(hwnd, _WM_LBUTTONUP, 0, lparam)


def postmessage_drag(hwnd: int, x1: int, y1: int, x2: int, y2: int, steps: int = 20):
    """通过 PostMessage 向窗口发送拖拽（不移动光标）

    在 WM_LBUTTONDOWN 前先通过 SendMessage 发送 WM_NCHITTEST，
    让目标窗口的 DefWindowProc 完成命中测试、建立正确的拖拽上下文，
    避免与外部真实鼠标点击产生状态冲突。
    """
    import time
    # 移动到起点
    _user32.PostMessageW(hwnd, _WM_MOUSEMOVE, 0, make_lparam(x1, y1))
    time.sleep(0.03)
    # 命中测试：同步确认起点在客户区内（DefWindowProc 返回 HTCLIENT）
    _user32.SendMessageW(hwnd, _WM_NCHITTEST, 0, make_lparam(x1, y1))
    # 按下
    _user32.PostMessageW(hwnd, _WM_LBUTTONDOWN, _MK_LBUTTON, make_lparam(x1, y1))
    time.sleep(0.05)
    # 逐步移动
    for i in range(1, steps + 1):
        ratio = i / steps
        cx = int(x1 + (x2 - x1) * ratio)
        cy = int(y1 + (y2 - y1) * ratio)
        _user32.PostMessageW(hwnd, _WM_MOUSEMOVE, _MK_LBUTTON, make_lparam(cx, cy))
        time.sleep(0.02)
    # 终点松开
    _user32.PostMessageW(hwnd, _WM_LBUTTONUP, 0, make_lparam(x2, y2))


# ─── 窗口枚举 ─────────────────────────────────────────────────

def list_visible_windows() -> list[dict]:
    """列出所有可见窗口（Win32 API）

    返回 list[dict]，每个 dict 包含 title, hwnd, left, top, width, height
    """
    results = []

    # Win32 常量
    GWL_EXSTYLE = -20
    WS_EX_TOOLWINDOW = 0x00000080   # 工具窗口（不显示在任务栏）
    WS_EX_NOACTIVATE = 0x08000000   # 不可激活的窗口
    GW_OWNER = 4

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def _callback(hwnd, lParam):
        if not _user32.IsWindowVisible(hwnd):
            return True

        # 过滤工具窗口（NVIDIA控制面板、托盘图标等）
        ex_style = _user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        if ex_style & WS_EX_TOOLWINDOW:
            return True
        if ex_style & WS_EX_NOACTIVATE:
            return True

        # 过滤有所有者的窗口（弹窗、对话框，不是主窗口）
        owner = _user32.GetWindow(hwnd, GW_OWNER)
        if owner:
            return True

        # 用固定大小缓冲区获取窗口标题
        buf = ctypes.create_unicode_buffer(256)
        _user32.GetWindowTextW(hwnd, buf, 256)
        title = buf.value
        if not title.strip():
            return True

        rect = wintypes.RECT()
        if _user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            w = rect.right - rect.left
            h = rect.bottom - rect.top
            if w > 200 and h > 200:
                results.append({
                    "title": title,
                    "hwnd": hwnd,
                    "left": rect.left,
                    "top": rect.top,
                    "width": w,
                    "height": h,
                })
        return True

    _user32.EnumWindows(_callback, None)

    logger.debug(f"枚举到 {len(results)} 个可见窗口")
    return results
