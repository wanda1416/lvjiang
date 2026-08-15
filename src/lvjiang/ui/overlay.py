"""Win32 点击穿透边框覆盖层。

刻意不用 Qt 顶层透明窗跨屏绘制，因为 Qt 的 screen.geometry()
是高 DPI 逻辑坐标，而 Win32 GetWindowRect/SetWindowPos 使用的是
当前进程视角下的窗口坐标。绘制和定位都走 Win32，能避免多屏/混合
DPI 时从屏幕 0 标记屏幕 1 出现偏移。
"""

import ctypes
from ctypes import wintypes

from loguru import logger

HINSTANCE = getattr(wintypes, "HINSTANCE", wintypes.HANDLE)
HICON = getattr(wintypes, "HICON", wintypes.HANDLE)
HCURSOR = getattr(wintypes, "HCURSOR", wintypes.HANDLE)
HBRUSH = getattr(wintypes, "HBRUSH", wintypes.HANDLE)
HMENU = getattr(wintypes, "HMENU", wintypes.HANDLE)
LPVOID = getattr(wintypes, "LPVOID", ctypes.c_void_p)
ATOM = getattr(wintypes, "ATOM", ctypes.c_ushort)
BYTE = getattr(wintypes, "BYTE", ctypes.c_ubyte)
WPARAM = ctypes.c_size_t
LPARAM = ctypes.c_ssize_t
LRESULT = ctypes.c_ssize_t


class BorderOverlay:
    """Win32 点击穿透边框层。"""

    _class_name = "LvjiangBorderOverlayWindow"
    _class_registered = False
    _wndproc = None
    _instances: dict[int, "BorderOverlay"] = {}

    def __init__(self):
        self._hwnd = None
        self._color = (255, 0, 0)
        self._pen_width = 10

    @classmethod
    def _register_class(cls):
        if cls._class_registered:
            return

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        kernel32.GetModuleHandleW.restype = HINSTANCE
        kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
        user32.DefWindowProcW.restype = LRESULT
        user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, WPARAM, LPARAM]

        wndproc_type = ctypes.WINFUNCTYPE(
            LRESULT,
            wintypes.HWND,
            wintypes.UINT,
            WPARAM,
            LPARAM,
        )

        @wndproc_type
        def wndproc(hwnd, msg, wparam, lparam):
            if msg == 0x000F:  # WM_PAINT
                overlay = cls._instances.get(int(hwnd))
                if overlay is not None:
                    overlay._paint(hwnd)
                    return 0
            if msg == 0x0002:  # WM_DESTROY
                cls._instances.pop(int(hwnd), None)
            return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

        cls._wndproc = wndproc

        class WNDCLASSW(ctypes.Structure):
            _fields_ = [
                ("style", wintypes.UINT),
                ("lpfnWndProc", wndproc_type),
                ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int),
                ("hInstance", HINSTANCE),
                ("hIcon", HICON),
                ("hCursor", HCURSOR),
                ("hbrBackground", HBRUSH),
                ("lpszMenuName", wintypes.LPCWSTR),
                ("lpszClassName", wintypes.LPCWSTR),
            ]

        user32.RegisterClassW.restype = ATOM
        user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]

        hinstance = kernel32.GetModuleHandleW(None)
        wc = WNDCLASSW()
        wc.lpfnWndProc = cls._wndproc
        wc.hInstance = hinstance
        wc.lpszClassName = cls._class_name

        if not user32.RegisterClassW(ctypes.byref(wc)):
            raise ctypes.WinError()
        cls._class_registered = True

    def _ensure_window(self):
        if self._hwnd:
            return

        self._register_class()
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        kernel32.GetModuleHandleW.restype = HINSTANCE
        kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]

        user32.CreateWindowExW.restype = wintypes.HWND
        user32.CreateWindowExW.argtypes = [
            wintypes.DWORD,
            wintypes.LPCWSTR,
            wintypes.LPCWSTR,
            wintypes.DWORD,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.HWND,
            HMENU,
            HINSTANCE,
            LPVOID,
        ]
        user32.SetLayeredWindowAttributes.restype = wintypes.BOOL
        user32.SetLayeredWindowAttributes.argtypes = [wintypes.HWND, wintypes.DWORD, BYTE, wintypes.DWORD]

        ex_style = (
            0x00000008  # WS_EX_TOPMOST
            | 0x00080000  # WS_EX_LAYERED
            | 0x00000020  # WS_EX_TRANSPARENT
            | 0x00000080  # WS_EX_TOOLWINDOW
            | 0x08000000  # WS_EX_NOACTIVATE
        )
        style = 0x80000000  # WS_POPUP
        hwnd = user32.CreateWindowExW(
            ex_style,
            self._class_name,
            "Lvjiang Border Overlay",
            style,
            0,
            0,
            1,
            1,
            None,
            None,
            kernel32.GetModuleHandleW(None),
            None,
        )
        if not hwnd:
            raise ctypes.WinError()

        # 颜色键透明：窗口里的黑色透明，红/绿边框可见。
        user32.SetLayeredWindowAttributes(hwnd, 0x000000, 255, 0x00000001)
        self._hwnd = hwnd
        self._instances[int(hwnd)] = self

    def _paint(self, hwnd):
        user32 = ctypes.windll.user32
        gdi32 = ctypes.windll.gdi32

        class PAINTSTRUCT(ctypes.Structure):
            _fields_ = [
                ("hdc", wintypes.HDC),
                ("fErase", wintypes.BOOL),
                ("rcPaint", wintypes.RECT),
                ("fRestore", wintypes.BOOL),
                ("fIncUpdate", wintypes.BOOL),
                ("rgbReserved", ctypes.c_byte * 32),
            ]

        user32.BeginPaint.restype = wintypes.HDC
        user32.BeginPaint.argtypes = [wintypes.HWND, ctypes.POINTER(PAINTSTRUCT)]
        user32.EndPaint.restype = wintypes.BOOL
        user32.EndPaint.argtypes = [wintypes.HWND, ctypes.POINTER(PAINTSTRUCT)]
        user32.GetClientRect.restype = wintypes.BOOL
        user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        user32.FillRect.restype = ctypes.c_int
        user32.FillRect.argtypes = [wintypes.HDC, ctypes.POINTER(wintypes.RECT), HBRUSH]
        gdi32.CreateSolidBrush.restype = HBRUSH
        gdi32.CreateSolidBrush.argtypes = [wintypes.DWORD]
        gdi32.CreatePen.restype = wintypes.HANDLE
        gdi32.CreatePen.argtypes = [ctypes.c_int, ctypes.c_int, wintypes.DWORD]
        gdi32.SelectObject.restype = wintypes.HANDLE
        gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HANDLE]
        gdi32.GetStockObject.restype = wintypes.HANDLE
        gdi32.GetStockObject.argtypes = [ctypes.c_int]
        gdi32.DeleteObject.restype = wintypes.BOOL
        gdi32.DeleteObject.argtypes = [wintypes.HANDLE]
        gdi32.Rectangle.restype = wintypes.BOOL
        gdi32.Rectangle.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int]

        ps = PAINTSTRUCT()
        hdc = user32.BeginPaint(hwnd, ctypes.byref(ps))
        try:
            rect = wintypes.RECT()
            user32.GetClientRect(hwnd, ctypes.byref(rect))

            black_brush = gdi32.CreateSolidBrush(0x000000)
            user32.FillRect(hdc, ctypes.byref(rect), black_brush)
            gdi32.DeleteObject(black_brush)

            r, g, b = self._color
            colorref = r | (g << 8) | (b << 16)
            pen = gdi32.CreatePen(0, self._pen_width, colorref)  # PS_SOLID
            old_pen = gdi32.SelectObject(hdc, pen)
            old_brush = gdi32.SelectObject(hdc, gdi32.GetStockObject(5))  # NULL_BRUSH

            inset = max(1, self._pen_width // 2)
            gdi32.Rectangle(
                hdc,
                inset,
                inset,
                max(inset + 1, rect.right - inset),
                max(inset + 1, rect.bottom - inset),
            )

            gdi32.SelectObject(hdc, old_brush)
            gdi32.SelectObject(hdc, old_pen)
            gdi32.DeleteObject(pen)
        finally:
            user32.EndPaint(hwnd, ctypes.byref(ps))

    def show_border(self, left: int, top: int, width: int, height: int):
        """在 Win32 窗口坐标上显示边框。"""
        self._ensure_window()
        user32 = ctypes.windll.user32
        user32.SetWindowPos.restype = wintypes.BOOL
        user32.SetWindowPos.argtypes = [
            wintypes.HWND,
            wintypes.HWND,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.UINT,
        ]
        user32.InvalidateRect.restype = wintypes.BOOL
        user32.InvalidateRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT), wintypes.BOOL]
        user32.UpdateWindow.restype = wintypes.BOOL
        user32.UpdateWindow.argtypes = [wintypes.HWND]
        user32.SetWindowPos(
            self._hwnd,
            wintypes.HWND(-1),  # HWND_TOPMOST
            int(left),
            int(top),
            max(1, int(width)),
            max(1, int(height)),
            0x0010 | 0x0040 | 0x0200,  # NOACTIVATE | SHOWWINDOW | NOOWNERZORDER
        )
        user32.InvalidateRect(self._hwnd, None, True)
        user32.UpdateWindow(self._hwnd)
        logger.debug(f"Overlay Win32: ({left},{top},{width}x{height})")

    def hide_border(self):
        """隐藏边框。"""
        if self._hwnd:
            ctypes.windll.user32.ShowWindow.restype = wintypes.BOOL
            ctypes.windll.user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
            ctypes.windll.user32.ShowWindow(self._hwnd, 0)  # SW_HIDE

    def set_color(self, color: str):
        """设置边框颜色 ('red' / 'green')。"""
        if color == "red":
            self._color = (255, 0, 0)
        elif color == "green":
            self._color = (0, 200, 0)
        if self._hwnd:
            ctypes.windll.user32.InvalidateRect.restype = wintypes.BOOL
            ctypes.windll.user32.InvalidateRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT), wintypes.BOOL]
            ctypes.windll.user32.UpdateWindow.restype = wintypes.BOOL
            ctypes.windll.user32.UpdateWindow.argtypes = [wintypes.HWND]
            ctypes.windll.user32.InvalidateRect(self._hwnd, None, True)
            ctypes.windll.user32.UpdateWindow(self._hwnd)

    def destroy(self):
        """销毁原生窗口。"""
        if self._hwnd:
            ctypes.windll.user32.DestroyWindow.restype = wintypes.BOOL
            ctypes.windll.user32.DestroyWindow.argtypes = [wintypes.HWND]
            ctypes.windll.user32.DestroyWindow(self._hwnd)
            self._instances.pop(int(self._hwnd), None)
            self._hwnd = None
