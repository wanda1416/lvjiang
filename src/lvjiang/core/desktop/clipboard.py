"""Windows 桌面文本粘贴公共实现。"""

from __future__ import annotations

import ctypes
import sys
import time
from collections.abc import Callable

_CF_UNICODETEXT = 13
_GMEM_MOVEABLE = 0x0002

if sys.platform == "win32":
    from ctypes import wintypes

    _clipboard_user32 = ctypes.WinDLL("user32", use_last_error=True)
    _clipboard_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    _clipboard_user32.OpenClipboard.argtypes = [wintypes.HWND]
    _clipboard_user32.OpenClipboard.restype = wintypes.BOOL
    _clipboard_user32.EmptyClipboard.restype = wintypes.BOOL
    _clipboard_user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
    _clipboard_user32.SetClipboardData.restype = wintypes.HANDLE
    _clipboard_user32.CloseClipboard.restype = wintypes.BOOL

    _clipboard_kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    _clipboard_kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
    _clipboard_kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    _clipboard_kernel32.GlobalLock.restype = wintypes.LPVOID
    _clipboard_kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    _clipboard_kernel32.GlobalUnlock.restype = wintypes.BOOL
    _clipboard_kernel32.GlobalFree.argtypes = [wintypes.HGLOBAL]
    _clipboard_kernel32.GlobalFree.restype = wintypes.HGLOBAL
else:  # pragma: no cover - 生产能力仅在 Windows 使用
    _clipboard_user32 = None
    _clipboard_kernel32 = None


def set_clipboard_text(text: str) -> None:
    """以 CF_UNICODETEXT 写入 Windows 剪贴板。"""
    if _clipboard_user32 is None or _clipboard_kernel32 is None:
        raise RuntimeError("系统剪贴板粘贴仅支持 Windows")
    if "\x00" in text:
        raise ValueError("粘贴文本不能包含 NUL 字符")

    encoded = (text + "\x00").encode("utf-16-le")
    handle = _clipboard_kernel32.GlobalAlloc(_GMEM_MOVEABLE, len(encoded))
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    transferred = False
    try:
        pointer = _clipboard_kernel32.GlobalLock(handle)
        if not pointer:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            ctypes.memmove(pointer, encoded, len(encoded))
        finally:
            _clipboard_kernel32.GlobalUnlock(handle)

        for attempt in range(10):
            if _clipboard_user32.OpenClipboard(None):
                break
            if attempt == 9:
                raise ctypes.WinError(ctypes.get_last_error())
            time.sleep(0.01)
        try:
            if not _clipboard_user32.EmptyClipboard():
                raise ctypes.WinError(ctypes.get_last_error())
            if not _clipboard_user32.SetClipboardData(_CF_UNICODETEXT, handle):
                raise ctypes.WinError(ctypes.get_last_error())
            # SetClipboardData 成功后内存所有权转交给系统。
            transferred = True
        finally:
            _clipboard_user32.CloseClipboard()
    finally:
        if not transferred:
            _clipboard_kernel32.GlobalFree(handle)


def paste_via_clipboard(
    text: str,
    key_down: Callable[[str], None],
    key_up: Callable[[str], None],
    *,
    settle_delay: float = 0.2,
) -> None:
    """写剪贴板并发送原子 Ctrl+V，供桌面输入后端共享。"""
    set_clipboard_text(text)
    pressed: list[str] = []
    try:
        for key in ("CTRL", "V"):
            key_down(key)
            pressed.append(key)
        time.sleep(0.03)
    finally:
        first_error: Exception | None = None
        for key in reversed(pressed):
            try:
                key_up(key)
            except Exception as exc:  # 两个键都必须尽力释放
                first_error = first_error or exc
        if first_error is not None:
            raise first_error
    # PostMessage 是异步投递；给目标窗口留出读取剪贴板的时间。
    if settle_delay > 0:
        time.sleep(settle_delay)
