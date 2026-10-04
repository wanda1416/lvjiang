"""桌面端 Win32 工具函数

提供 SendInput / PostMessage 共用的底层 Win32 基础设施，
以及窗口枚举工具（list_visible_windows）。
"""

import ctypes
import sys
import time
from collections.abc import Callable
from ctypes import wintypes

from loguru import logger

from ..timing import NS_PER_SECOND, precise_wait, precise_wait_until

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


class _KeyBdInput(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", PUL),
    ]


class _InputUnion(ctypes.Union):
    _fields_ = [("mi", _MouseInput), ("ki", _KeyBdInput)]


class _Input(ctypes.Structure):
    _fields_ = [("type", ctypes.c_ulong), ("ii", _InputUnion)]


# SendInput 必须显式声明 argtypes，否则 ctypes 默认把所有参数按 c_int 处理，
# 指针参数（byref 地址）会被截断为 32 位，SendInput 读到错误内存而返回 0，
# 键盘/鼠标事件注入静默失败（实测：未声明时 ret=0，声明后 ret=1）。
if _user32 is not None:
    _user32.SendInput.argtypes = [
        wintypes.UINT,             # nInputs
        ctypes.POINTER(_Input),    # lpInput
        ctypes.c_int,              # cbSize
    ]
    _user32.SendInput.restype = wintypes.UINT


# 鼠标事件常量
_INPUT_MOUSE = 0
_MOUSEEVENTF_MOVE = 0x0001
_MOUSEEVENTF_LEFTDOWN = 0x0002
_MOUSEEVENTF_LEFTUP = 0x0004
_MOUSEEVENTF_RIGHTDOWN = 0x0008
_MOUSEEVENTF_RIGHTUP = 0x0010
_MOUSEEVENTF_MIDDLEDOWN = 0x0020
_MOUSEEVENTF_MIDDLEUP = 0x0040
_MOUSEEVENTF_WHEEL = 0x0800
_MOUSEEVENTF_XDOWN = 0x0080
_MOUSEEVENTF_XUP = 0x0100
_MOUSEEVENTF_MOVE_NOCOALESCE = 0x2000
_WHEEL_DELTA = 120
# XBUTTONDOWN/XBUTTONUP 靠 mouseData 区分具体是侧键一（后退）还是侧键二
# （前进），与 dwFlags 的 XDOWN/XUP 组合使用，其余事件类型该字段固定为 0。
_XBUTTON1 = 0x0001
_XBUTTON2 = 0x0002

# PostMessage 鼠标消息常量
_WM_MOUSEMOVE = 0x0200
_WM_LBUTTONDOWN = 0x0201
_WM_LBUTTONUP = 0x0202
_WM_MOUSEWHEEL = 0x020A
_MK_LBUTTON = 0x0001
_WM_NCHITTEST = 0x0084
_HTCLIENT = 1


def send_mouse_event(flags: int, dx: int = 0, dy: int = 0, mouse_data: int = 0):
    """通过 SendInput 发送鼠标事件

    mouse_data 仅 XBUTTONDOWN/XBUTTONUP（侧键 前进/后退）需要，用来标识
    具体是 XBUTTON1 还是 XBUTTON2；其余事件类型固定传 0。
    """
    mi = _MouseInput(
        dx=dx, dy=dy, mouseData=mouse_data, dwFlags=flags, time=0,
        dwExtraInfo=PUL(ctypes.c_ulong(0)),
    )
    ii = _InputUnion(mi=mi)
    inp = _Input(type=_INPUT_MOUSE, ii=ii)
    _user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(inp))


def send_mouse_wheel_event(delta: int):
    """通过 SendInput 发送鼠标滚轮事件

    delta > 0 向上滚动，delta < 0 向下滚动。
    每个 WHEEL_DELTA (120) 对应一格滚动。
    """
    mi = _MouseInput(
        dx=0, dy=0, mouseData=delta, dwFlags=_MOUSEEVENTF_WHEEL, time=0,
        dwExtraInfo=PUL(ctypes.c_ulong(0)),
    )
    ii = _InputUnion(mi=mi)
    inp = _Input(type=_INPUT_MOUSE, ii=ii)
    _user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(inp))


def smooth_move_to(x: int, y: int, duration: float):
    """按绝对时间轴平滑移动鼠标，避免逐步等待累计漂移。"""
    steps = max(int(duration / 0.01), 1)
    start_ns = time.perf_counter_ns()
    duration_ns = max(0, int(duration * NS_PER_SECOND))
    point = wintypes.POINT()
    _user32.GetCursorPos(ctypes.byref(point))
    sx, sy = point.x, point.y
    for i in range(1, steps + 1):
        ratio = i / steps
        cx = int(sx + (x - sx) * ratio)
        cy = int(sy + (y - sy) * ratio)
        _user32.SetCursorPos(cx, cy)
        precise_wait_until(
            start_ns + duration_ns * i // steps,
            spin_tail_ns=0,
        )


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

#: 已经报过「激活失败」的窗口。一次任务里每个动作都要激活，失败时不能每次都
#: warning——几百次点击就是几百条同样的告警。首次失败给完整告警，之后降级到
#: debug；一旦激活成功就清掉，下次再失败仍然会显眼地报一次。
_activation_warned: set[int] = set()


def window_title(hwnd: int) -> str:
    """读窗口标题，只用于日志；读不到返回空串。"""
    if _user32 is None or not hwnd:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(256)
        _user32.GetWindowTextW(wintypes.HWND(hwnd), buf, 256)
        return buf.value
    except (AttributeError, OSError):
        return ""


def activate_window(hwnd: int, restore: bool = True) -> bool:
    """激活目标窗口；返回**激活本身是否成功**。

    `restore=True` 时随后又把前台让回原窗口，返回值仍指激活那一步的结果。

    这类窗口只有处于前台/焦点状态才把鼠标消息转成 SDL 事件，
    后台直接投递 PostMessage 会被忽略（实测 PostMessage/SendMessage
    均无反应，激活窗口后即生效）。

    restore=True 时投递完成后把焦点还原给原前台窗口，尽量不影响
    用户正在使用的窗口（后台跑）。

    返回值必须如实反映结果。`SetForegroundWindow` 会被 Windows 以调用进程
    不在前台为由拒绝，窗口最小化时也不会被恢复（这里不调 ShowWindow，强行
    恢复用户最小化的窗口是更强的侵入）。失败却返回成功的话，前台输入会静悄悄
    地发给**别的**窗口：日志里 key_down/key_up 一切正常，游戏毫无反应，排查时
    完全看不出输入落在哪——已经为此绕过一次。
    """
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

    activated = False
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
                activated = True
                break
            time.sleep(0.01)
    except (AttributeError, OSError) as exc:
        logger.debug(f"激活窗口 {hwnd} 时 Win32 调用失败: {exc}")

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

    if activated:
        _activation_warned.discard(hwnd)
        return True

    # 这条决定后续输入有没有落在目标窗口上，必须带上两边的身份才可行动。
    actual = _user32.GetForegroundWindow() or 0
    detail = (f"激活窗口失败：目标 {hwnd}「{window_title(hwnd)}」，"
              f"实际前台 {actual}「{window_title(actual)}」")
    if hwnd in _activation_warned:
        logger.debug(detail)
    else:
        _activation_warned.add(hwnd)
        logger.warning(
            f"{detail}。前台输入会发给实际前台窗口，游戏不会有反应；"
            f"窗口被最小化或律匠不在前台时会出现，请切回游戏窗口后重试")
    return False


def postmessage_click(
    hwnd: int,
    client_x: int,
    client_y: int,
    activate: bool = False,
    hold: float | None = None,
    stop_check: Callable[[], bool] | None = None,
):
    """通过 PostMessage 向窗口发送一次点击（不移动光标）

    activate=True 时先瞬时激活目标窗口再投递，适配 SDL 类窗口
    （scrcpy 等）——这类窗口只有处于前台/焦点才处理鼠标消息。
    投递完成后自动还原原前台窗口焦点。
    """
    if activate:
        activate_window(hwnd)
    target = resolve_message_target(hwnd, client_x, client_y)
    lparam = make_lparam(client_x, client_y)
    _user32.PostMessageW(target, _WM_MOUSEMOVE, 0, lparam)
    precise_wait(0.03)
    _user32.PostMessageW(target, _WM_LBUTTONDOWN, _MK_LBUTTON, lparam)
    try:
        if hold is None:
            precise_wait(0.05)
        else:
            precise_wait(hold, stop_check=stop_check)
    finally:
        _user32.PostMessageW(target, _WM_LBUTTONUP, 0, lparam)


def postmessage_move(hwnd: int, client_x: int, client_y: int, activate: bool = False):
    """通过 PostMessage 向窗口发送鼠标移动消息（不点击）

    activate=True 时先瞬时激活目标窗口再投递。
    """
    if activate:
        activate_window(hwnd)
    target = resolve_message_target(hwnd, client_x, client_y)
    lparam = make_lparam(client_x, client_y)
    _user32.PostMessageW(target, _WM_MOUSEMOVE, 0, lparam)


def postmessage_scroll(
    hwnd: int,
    client_x: int,
    client_y: int,
    delta: int,
    activate: bool = False,
):
    """通过 PostMessage 向窗口发送鼠标滚轮消息

    delta > 0 向上滚动，delta < 0 向下滚动。
    每个 _WHEEL_DELTA (120) 对应一格滚动。
    activate=True 时先瞬时激活目标窗口再投递。
    """
    if activate:
        activate_window(hwnd)
    target = resolve_message_target(hwnd, client_x, client_y)
    # wParam: 高 16 位 = wheel delta，低 16 位 = 虚拟键标志（0）
    wparam = (delta & 0xFFFF) << 16
    lparam = make_lparam(client_x, client_y)
    _user32.PostMessageW(target, _WM_MOUSEWHEEL, wparam, lparam)


def postmessage_drag(
    hwnd: int,
    x1: int,
    y1: int,
    x2: int,
    y2: int,
    duration: float = 0.4,
    hold: float | None = None,
    steps: int | None = None,
    activate: bool = False,
    stop_check: Callable[[], bool] | None = None,
):
    """通过 PostMessage 向窗口发送拖拽（不移动光标）

    在 WM_LBUTTONDOWN 前先通过 SendMessage 发送 WM_NCHITTEST，
    让目标窗口的 DefWindowProc 完成命中测试、建立正确的拖拽上下文，
    避免与外部真实鼠标点击产生状态冲突。

    activate=True 时先瞬时激活目标窗口再投递（适配 SDL 类窗口）。

    按下窗口自动解析为起点命中点处的实际子窗口（投屏窗口），
    整个拖拽过程统一投递给该子窗口。
    """
    if activate:
        activate_window(hwnd)
    target = resolve_message_target(hwnd, x1, y1)
    # 移动到起点
    _user32.PostMessageW(target, _WM_MOUSEMOVE, 0, make_lparam(x1, y1))
    precise_wait(0.03)
    # 命中测试：同步确认起点在客户区内（DefWindowProc 返回 HTCLIENT）
    _user32.SendMessageW(target, _WM_NCHITTEST, 0, make_lparam(x1, y1))
    # 按下
    _user32.PostMessageW(target, _WM_LBUTTONDOWN, _MK_LBUTTON, make_lparam(x1, y1))
    try:
        precise_wait(0.05)
        # 逐步移动
        duration = max(0.0, float(duration))
        if steps is None:
            steps = max(int(duration / 0.02), 5)
        else:
            steps = max(int(steps), 1)
        start_ns = time.perf_counter_ns()
        duration_ns = int(duration * NS_PER_SECOND)
        for i in range(1, steps + 1):
            ratio = i / steps
            cx = int(x1 + (x2 - x1) * ratio)
            cy = int(y1 + (y2 - y1) * ratio)
            _user32.PostMessageW(
                target, _WM_MOUSEMOVE, _MK_LBUTTON, make_lparam(cx, cy))
            precise_wait_until(
                start_ns + duration_ns * i // steps,
                spin_tail_ns=0,
            )
        if hold is not None and hold > 0:
            precise_wait(float(hold), stop_check=stop_check)
    finally:
        # 异常也不能把目标窗口留在鼠标按下状态。
        _user32.PostMessageW(target, _WM_LBUTTONUP, 0, make_lparam(x2, y2))


# ─── 窗口枚举 ─────────────────────────────────────────────────

def list_visible_windows() -> list[dict]:
    """列出所有可见窗口（Win32 API）

    返回 list[dict]，每个 dict 包含 title, hwnd, left, top, width, height。
    自动排除当前进程自身的窗口（含律匠主窗口及其子窗口）。
    """
    import os
    results = []

    # Win32 常量
    GWL_EXSTYLE = -20
    WS_EX_TOOLWINDOW = 0x00000080   # 工具窗口（不显示在任务栏）
    WS_EX_NOACTIVATE = 0x08000000   # 不可激活的窗口
    GW_OWNER = 4

    # 当前进程 PID（排除自身窗口）
    _self_pid = os.getpid()
    _pid_buf = ctypes.c_ulong()

    def _process_path(pid: int) -> str:
        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return ""
        try:
            size = ctypes.c_ulong(32768)
            buf = ctypes.create_unicode_buffer(size.value)
            if ctypes.windll.kernel32.QueryFullProcessImageNameW(
                    handle, 0, buf, ctypes.byref(size)):
                return buf.value
            return ""
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)

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

        # 过滤当前进程自身的窗口（律匠主窗口等）
        _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(_pid_buf))
        if _pid_buf.value == _self_pid:
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
                    "pid": int(_pid_buf.value),
                    "executable": _process_path(int(_pid_buf.value)),
                    "left": rect.left,
                    "top": rect.top,
                    "width": w,
                    "height": h,
                })
        return True

    _user32.EnumWindows(_callback, None)

    logger.debug(f"枚举到 {len(results)} 个可见窗口（已排除自身进程）")
    return results


# ─── DWM 可见边界 ─────────────────────────────────────────────

#: DwmGetWindowAttribute 的 DWMWA_EXTENDED_FRAME_BOUNDS
_DWMWA_EXTENDED_FRAME_BOUNDS = 9


def get_window_rect(hwnd: int) -> tuple[int, int, int, int] | None:
    """``GetWindowRect`` 的物理屏幕矩形 ``(left, top, width, height)``。

    Win10 起窗口矩形两侧各含约 8px 不可见的调整边框，这块区域在屏幕上显示的是
    窗口背后的内容——mss 截图连同它一起抓，所以窗口矩形就是 mss 那张图的口径。
    """
    if _user32 is None:
        return None
    rect = wintypes.RECT()
    if not _user32.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(rect)):
        return None
    return (rect.left, rect.top,
            rect.right - rect.left, rect.bottom - rect.top)


def get_window_visible_rect(hwnd: int) -> tuple[int, int, int, int] | None:
    """窗口**可见**边界 ``(left, top, width, height)``，取不到时返回 ``None``。

    即 DWM 的 ``DWMWA_EXTENDED_FRAME_BOUNDS``：去掉不可见调整边框后，用户真正
    看得见的那一块。Windows Graphics Capture 交付的帧就是这块内容，因此它比
    ``GetWindowRect`` 窄若干像素——两者的差值正是 WGC 帧贴回窗口矩形时的偏移。
    """
    if sys.platform != "win32":
        return None
    rect = wintypes.RECT()
    try:
        hr = ctypes.windll.dwmapi.DwmGetWindowAttribute(
            wintypes.HWND(hwnd),
            ctypes.c_uint(_DWMWA_EXTENDED_FRAME_BOUNDS),
            ctypes.byref(rect),
            ctypes.sizeof(rect),
        )
    except OSError as exc:  # dwmapi 不可用（理论上 Vista 起都有）
        logger.debug(f"DwmGetWindowAttribute 不可用: {exc}")
        return None
    if hr != 0:
        logger.debug(f"DwmGetWindowAttribute 失败: hr={hr}")
        return None
    return (rect.left, rect.top,
            rect.right - rect.left, rect.bottom - rect.top)
