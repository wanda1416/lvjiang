"""桌面端 Win32 工具函数

提供 SendInput / PostMessage 共用的底层 Win32 基础设施，
以及窗口枚举工具（list_visible_windows）。
"""

import ctypes
import sys
from ctypes import wintypes

from loguru import logger

# ─── SendInput 基础设施 ────────────────────────────────────────

# 非 Windows 平台允许 import（常量/结构体均为纯 ctypes），
# 但实际调用任一 Win32 函数会因 _user32 为 None 报错——
# 调用方（桌面后端）已在 UI 层按平台门控，正常不会走到这里
if sys.platform == "win32":
    _user32 = ctypes.windll.user32
else:
    _user32 = None

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
    """屏幕坐标 → 窗口客户区坐标（本进程为 Per-Monitor V2，返回物理像素）"""
    pt = wintypes.POINT(screen_x, screen_y)
    _user32.ScreenToClient(hwnd, ctypes.byref(pt))
    return pt.x, pt.y


# ─── DPI 感知适配（PostMessage 坐标换算）────────────────────────

# GetAwarenessFromDpiAwarenessContext 返回值
_DPI_AWARENESS_UNAWARE = 0
_DPI_AWARENESS_SYSTEM = 1
_DPI_AWARENESS_PER_MONITOR = 2

# MonitorFromWindow / GetDpiForMonitor 常量
_MONITOR_DEFAULTTONEAREST = 2
_MDT_EFFECTIVE_DPI = 0


def _get_window_dpi_awareness(hwnd: int) -> int:
    """目标窗口的 DPI awareness；失败按 per-monitor 兜底（不缩放，与旧行为一致）"""
    try:
        _user32.GetWindowDpiAwarenessContext.restype = ctypes.c_void_p
        _user32.GetAwarenessFromDpiAwarenessContext.argtypes = [ctypes.c_void_p]
        _user32.GetAwarenessFromDpiAwarenessContext.restype = ctypes.c_int
        ctx = _user32.GetWindowDpiAwarenessContext(wintypes.HWND(hwnd))
        if not ctx:
            return _DPI_AWARENESS_PER_MONITOR
        return int(_user32.GetAwarenessFromDpiAwarenessContext(ctx))
    except (AttributeError, OSError):
        return _DPI_AWARENESS_PER_MONITOR


def _get_window_screen_dpi(hwnd: int) -> int:
    """目标窗口所在显示器的实际 DPI（与窗口自身 awareness 无关）。

    不能用 GetDpiForWindow：它对 unaware 窗口恒返回 96、对 system aware
    恒返回系统 DPI，拿不到所在屏真实缩放。
    """
    try:
        _user32.MonitorFromWindow.restype = ctypes.c_void_p
        _user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
        hmon = _user32.MonitorFromWindow(wintypes.HWND(hwnd), _MONITOR_DEFAULTTONEAREST)
        if not hmon:
            return 96
        x_dpi = ctypes.c_uint(0)
        y_dpi = ctypes.c_uint(0)
        shcore = ctypes.windll.shcore
        shcore.GetDpiForMonitor.argtypes = [
            ctypes.c_void_p, ctypes.c_int,
            ctypes.POINTER(ctypes.c_uint), ctypes.POINTER(ctypes.c_uint),
        ]
        hr = shcore.GetDpiForMonitor(
            hmon, _MDT_EFFECTIVE_DPI,
            ctypes.byref(x_dpi), ctypes.byref(y_dpi),
        )
        if hr == 0 and x_dpi.value:
            return int(x_dpi.value)
    except (AttributeError, OSError):
        pass
    return 96


def _get_system_dpi() -> int:
    """系统 DPI（system-aware 窗口的虚拟化基准 = 主显示器启动时 DPI）"""
    try:
        _user32.GetDpiForSystem.restype = ctypes.c_uint
        dpi = _user32.GetDpiForSystem()
        if dpi:
            return int(dpi)
    except (AttributeError, OSError):
        pass
    return 96


def message_coord_scale(awareness: int, window_dpi: int, system_dpi: int) -> float:
    """PostMessage 坐标缩放比（本进程物理客户区坐标 → 目标窗口消息坐标系）。

    PostMessageW 不做跨进程 DPI 转换，lparam 坐标按目标窗口自身坐标系解读：
    - per-monitor aware：1:1 原样
    - system aware：客户区按系统 DPI 虚拟化 → ×system_dpi/屏DPI
    - unaware：客户区按 96 DPI 虚拟化 → ×96/屏DPI
    """
    if awareness == _DPI_AWARENESS_PER_MONITOR:
        return 1.0
    base = system_dpi if awareness == _DPI_AWARENESS_SYSTEM else 96
    dpi = window_dpi or 96
    return base / dpi


def screen_to_client_logical(hwnd: int, screen_x: int, screen_y: int) -> tuple[int, int]:
    """屏幕物理坐标 → 目标窗口 PostMessage 消息坐标（含 DPI 换算）。

    本进程锁定 Per-Monitor V2（__main__._configure_dpi），ScreenToClient 返回
    物理客户区坐标；若目标窗口 DPI unaware / system aware（投屏软件常见），
    在高缩放副屏上直接投递物理坐标会整体偏移，超出其逻辑客户区的消息被
    DefWindowProc 丢弃——表现为后台模式点击毫无反应。
    """
    cx, cy = screen_to_client(hwnd, screen_x, screen_y)
    awareness = _get_window_dpi_awareness(hwnd)
    if awareness == _DPI_AWARENESS_PER_MONITOR:
        return cx, cy
    screen_dpi = _get_window_screen_dpi(hwnd)
    system_dpi = _get_system_dpi()
    scale = message_coord_scale(awareness, screen_dpi, system_dpi)
    if scale == 1.0:
        return cx, cy
    rx, ry = int(round(cx * scale)), int(round(cy * scale))
    logger.debug(
        f"[DPI] hwnd=0x{hwnd:X} awareness={awareness} "
        f"screen_dpi={screen_dpi} system_dpi={system_dpi} "
        f"scale={scale:.3f} client({cx},{cy}) -> logical({rx},{ry})"
    )
    return rx, ry


# 子窗口命中探测（投屏/游戏窗口常用子窗口接收鼠标，投给顶层无效）
# ChildWindowFromPointEx 标志：跳过不可见 + 禁用子窗口
_CWP_SKIPINVISIBLE = 0x0001
_CWP_SKIPDISABLED = 0x0002


def resolve_message_target(hwnd: int, client_x: int, client_y: int) -> int:
    """返回实际应接收鼠标消息的窗口句柄。

    若 (client_x, client_y) 处命中一个非顶层的子窗口（投屏软件如
    vivo 互传、部分 Qt 渲染窗口），鼠标消息需投给该子窗口，
    投给顶层窗口会被忽略 → 后台模式点击毫无反应。
    未命中子窗口时返回原 hwnd。
    """
    try:
        _user32.ChildWindowFromPointEx.restype = ctypes.c_void_p
        _user32.ChildWindowFromPointEx.argtypes = [wintypes.HWND, wintypes.POINT, wintypes.UINT]
        pt = wintypes.POINT(client_x, client_y)
        child = _user32.ChildWindowFromPointEx(
            wintypes.HWND(hwnd), pt, _CWP_SKIPINVISIBLE | _CWP_SKIPDISABLED
        )
        if child and int(child) != hwnd:
            return int(child)
    except (AttributeError, OSError):
        pass
    return hwnd


# ─── 前台激活辅助（SDL/投屏窗口需要窗口激活才处理鼠标）──────────

def activate_window(hwnd: int, restore: bool = True) -> bool:
    """瞬时激活目标窗口，让 SDL 类窗口（scrcpy 等）产生鼠标事件。

    这类窗口只有处于前台/焦点状态才把鼠标消息转成 SDL 事件，
    后台直接投递 PostMessage 会被忽略（实测 PostMessage/SendMessage
    均无反应，激活窗口后即生效）。

    restore=True 时投递完成后把焦点还原给原前台窗口，尽量不影响
    用户正在使用的窗口（后台跑）。
    """
    import time
    # 记录原前台窗口（用于还原）
    _user32.GetForegroundWindow.restype = ctypes.c_void_p
    prev = _user32.GetForegroundWindow() or 0

    # 解除前台锁定：把本线程输入附加到当前前台窗口线程，使 SetForegroundWindow 合法
    try:
        _user32.GetCurrentThreadId.restype = ctypes.c_uint
        _user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        _user32.GetWindowThreadProcessId.restype = wintypes.DWORD
        _user32.GetForegroundWindow.restype = wintypes.HWND
        fg = _user32.GetForegroundWindow()
        cur = _user32.GetCurrentThreadId()
        fg_tid = _user32.GetWindowThreadProcessId(fg, None) if fg else 0
        attached = False
        if fg_tid and fg_tid != cur:
            _user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
            _user32.AttachThreadInput.restype = wintypes.BOOL
            attached = bool(_user32.AttachThreadInput(cur, fg_tid, True))
    except (AttributeError, OSError):
        attached = False

    try:
        _user32.SetForegroundWindow.argtypes = [wintypes.HWND]
        _user32.SetForegroundWindow.restype = wintypes.BOOL
        _user32.BringWindowToTop.argtypes = [wintypes.HWND]
        _user32.BringWindowToTop.restype = wintypes.BOOL
        _user32.SetForegroundWindow(wintypes.HWND(hwnd))
        _user32.BringWindowToTop(wintypes.HWND(hwnd))
        # 等窗口真正激活
        for _ in range(20):
            _user32.GetForegroundWindow.restype = wintypes.HWND
            if _user32.GetForegroundWindow() == hwnd:
                break
            time.sleep(0.01)
    except (AttributeError, OSError):
        pass

    if restore and prev and prev != hwnd:
        try:
            _user32.SetForegroundWindow.argtypes = [wintypes.HWND]
            _user32.SetForegroundWindow(wintypes.HWND(prev))
        except (AttributeError, OSError):
            pass

    # 解除线程附加
    if attached:
        try:
            _user32.AttachThreadInput(cur, fg_tid, False)
        except (AttributeError, OSError):
            pass
    return True


def postmessage_click(hwnd: int, client_x: int, client_y: int, activate: bool = False):
    """通过 PostMessage 向窗口发送一次点击（不移动光标）

    activate=True 时先瞬时激活目标窗口再投递，适配 SDL 类窗口
    （scrcpy 等）——这类窗口只有处于前台/焦点才处理鼠标消息。
    投递完成后自动还原原前台窗口焦点。
    """
    import time
    if activate:
        activate_window(hwnd)
    target = resolve_message_target(hwnd, client_x, client_y)
    lparam = make_lparam(client_x, client_y)
    _user32.PostMessageW(target, _WM_MOUSEMOVE, 0, lparam)
    time.sleep(0.03)
    _user32.PostMessageW(target, _WM_LBUTTONDOWN, _MK_LBUTTON, lparam)
    time.sleep(0.05)
    _user32.PostMessageW(target, _WM_LBUTTONUP, 0, lparam)


def postmessage_drag(
    hwnd: int,
    x1: int,
    y1: int,
    x2: int,
    y2: int,
    steps: int = 20,
    activate: bool = False,
):
    """通过 PostMessage 向窗口发送拖拽（不移动光标）

    在 WM_LBUTTONDOWN 前先通过 SendMessage 发送 WM_NCHITTEST，
    让目标窗口的 DefWindowProc 完成命中测试、建立正确的拖拽上下文，
    避免与外部真实鼠标点击产生状态冲突。

    activate=True 时先瞬时激活目标窗口再投递（适配 SDL 类窗口）。

    按下窗口自动解析为起点命中点处的实际子窗口（投屏窗口），
    整个拖拽过程统一投递给该子窗口。
    """
    import time
    if activate:
        activate_window(hwnd)
    target = resolve_message_target(hwnd, x1, y1)
    # 移动到起点
    _user32.PostMessageW(target, _WM_MOUSEMOVE, 0, make_lparam(x1, y1))
    time.sleep(0.03)
    # 命中测试：同步确认起点在客户区内（DefWindowProc 返回 HTCLIENT）
    _user32.SendMessageW(target, _WM_NCHITTEST, 0, make_lparam(x1, y1))
    # 按下
    _user32.PostMessageW(target, _WM_LBUTTONDOWN, _MK_LBUTTON, make_lparam(x1, y1))
    time.sleep(0.05)
    # 逐步移动
    for i in range(1, steps + 1):
        ratio = i / steps
        cx = int(x1 + (x2 - x1) * ratio)
        cy = int(y1 + (y2 - y1) * ratio)
        _user32.PostMessageW(target, _WM_MOUSEMOVE, _MK_LBUTTON, make_lparam(cx, cy))
        time.sleep(0.02)
    # 终点松开
    _user32.PostMessageW(target, _WM_LBUTTONUP, 0, make_lparam(x2, y2))


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
