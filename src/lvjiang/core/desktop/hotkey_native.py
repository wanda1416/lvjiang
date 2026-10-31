"""Windows 系统级热键（``RegisterHotKey``）。

pynput 的 ``GlobalHotKeys`` 用「当前按下的键集合恰好等于目标集合」判定触发，
而 Windows 上按 Alt 会进入系统菜单模式，release 事件经常被吞掉。集合里一旦
残留 ``alt``，这个组合就再也不等于目标集合，表现为「第一次能触发、之后完全
无响应」——实测日志正是这样：第一次 Alt+F8 成功最小化，第二次连回调都没进。

窗口最小化到状态栏之后，这个热键是唯一不依赖系统托盘的恢复入口，失效即等于
窗口丢失。因此它不走键盘钩子状态机，改用系统热键：由 OS 直接投递
``WM_HOTKEY``，只要注册成功就一定触发。
"""
from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes
from typing import Callable

from loguru import logger
from PyQt6.QtCore import QAbstractNativeEventFilter

WM_HOTKEY = 0x0312
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
# 按住不放时系统不重复投递：Alt+F8 这类恢复键位重复触发只会把窗口反复
# 最小化/唤回，看起来像“没反应”。
MOD_NOREPEAT = 0x4000

_MODIFIERS = {
    "alt": MOD_ALT,
    "ctrl": MOD_CONTROL,
    "control": MOD_CONTROL,
    "shift": MOD_SHIFT,
}

# 只接受 F7~F12，与 HotkeyConfig 的键位范围保持一致（VK_F7=0x76 … VK_F12=0x7B）
_VK_KEYS = {f"F{i}": 0x6F + i for i in range(7, 13)}

_HOTKEY_ID_BASE = 0xA170


def parse_hotkey(text: str) -> tuple[int, int] | None:
    """把 ``Alt+F8`` 解析成 ``(modifiers, vk)``；不支持的写法返回 ``None``。"""
    parts = [p for p in str(text or "").replace(" ", "").split("+") if p]
    if not parts:
        return None
    *raw_mods, key = parts
    vk = _VK_KEYS.get(key.upper())
    if vk is None:
        return None
    mods = 0
    for raw in raw_mods:
        bit = _MODIFIERS.get(raw.lower())
        if bit is None:
            return None
        mods |= bit
    return mods, vk


class _MessageFilter(QAbstractNativeEventFilter):
    """从 Qt 的消息泵里截获 ``WM_HOTKEY``。"""

    def __init__(self) -> None:
        super().__init__()
        self.callbacks: dict[int, Callable[[], None]] = {}

    def nativeEventFilter(self, event_type, message):  # noqa: N802 - Qt API
        if not self.callbacks:
            return False, 0
        try:
            if bytes(event_type) == b"windows_generic_MSG":
                msg = ctypes.cast(
                    int(message), ctypes.POINTER(wintypes.MSG)).contents
                if msg.message == WM_HOTKEY:
                    callback = self.callbacks.get(int(msg.wParam))
                    if callback is not None:
                        callback()
                        return True, 0
        except Exception:  # noqa: BLE001 - 解析失败不能打断事件分发
            logger.debug("解析 WM_HOTKEY 失败", exc_info=True)
        return False, 0


class NativeHotkeyManager:
    """在一个窗口上注册系统热键；回调在 GUI 线程执行。"""

    def __init__(self) -> None:
        self._filter = _MessageFilter()
        self._window = 0
        self._installed = False
        self._registered: dict[int, str] = {}

    @staticmethod
    def available() -> bool:
        """系统热键只在 Windows 可用；其他平台沿用 pynput 的监听器。"""
        return sys.platform == "win32"

    def install(self, app, window_handle: int) -> bool:
        """安装消息过滤器并记住目标窗口，返回是否可以注册热键。"""
        if not self.available():
            return False
        self._window = int(window_handle or 0)
        if self._window == 0:
            return False
        if not self._installed and app is not None:
            app.installNativeEventFilter(self._filter)
            self._installed = True
        return True

    def register(self, hotkey_id: int, text: str, callback: Callable[[], None]) -> bool:
        """注册一个系统热键；失败时返回 False，由调用方降级。"""
        if not self.available() or not self._window:
            return False
        parsed = parse_hotkey(text)
        if parsed is None:
            logger.warning(f"系统热键 {text} 不是受支持的键位")
            return False
        mods, vk = parsed
        try:
            ok = bool(ctypes.windll.user32.RegisterHotKey(
                self._window, hotkey_id, mods | MOD_NOREPEAT, vk))
        except Exception as exc:  # noqa: BLE001 - 系统调用失败不应中断启动
            logger.warning(f"系统热键 {text} 注册异常: {exc}")
            return False
        if not ok:
            logger.warning(f"系统热键 {text} 注册失败（大概率被其他程序占用）")
            return False
        self._filter.callbacks[hotkey_id] = callback
        self._registered[hotkey_id] = text
        logger.info(f"系统热键已注册: {text}")
        return True

    def unregister_all(self) -> None:
        """注销全部热键，用于重启监听与退出。"""
        if not self.available():
            self._registered.clear()
            self._filter.callbacks.clear()
            return
        for hotkey_id, text in list(self._registered.items()):
            try:
                ctypes.windll.user32.UnregisterHotKey(self._window, hotkey_id)
            except Exception as exc:  # noqa: BLE001
                logger.debug(f"注销系统热键 {text} 失败: {exc}")
        self._registered.clear()
        self._filter.callbacks.clear()

    @staticmethod
    def next_id(offset: int = 0) -> int:
        """热键 ID：同一个窗口内唯一即可，取固定基准避免与系统冲突。"""
        return _HOTKEY_ID_BASE + offset
