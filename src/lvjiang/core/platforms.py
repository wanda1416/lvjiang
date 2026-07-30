"""平台差异适配层——主流程中 Windows/macOS 的行为差异统一收口。

只收「主流程分支」：桌面输入后端创建、全局热键启停策略、
工作流原生弹窗回退、adb 候选路径、桌面投屏入口可见性。

窗口截图/输入注入等具体实现本身已按平台拆分
（core/desktop/ 仅 Windows；core/android/ 跨平台），
其内部的平台门控不在此层重复抽象。
"""
from __future__ import annotations

import sys
from typing import TYPE_CHECKING, Callable

from loguru import logger

if TYPE_CHECKING:
    from pynput.keyboard import GlobalHotKeys

    from ..config import DelayConfig
    from .input_base import InputBackend

IS_WINDOWS = sys.platform == "win32"
IS_MACOS = sys.platform == "darwin"

# 桌面投屏模式（窗口扫描/定位/Win32 输入）仅 Windows 可用；
# 非 Windows 只支持 ADB 模式（UI 层据此隐藏窗口扫描入口）
DESKTOP_BACKEND_AVAILABLE = IS_WINDOWS


# ─── 桌面输入后端 ─────────────────────────────────────────

def create_desktop_input(delay_config: "DelayConfig | None" = None) -> "InputBackend | None":
    """创建桌面输入后端（PostMessage 后台模式）；非 Windows 返回 None。

    非 Windows 平台不 import core.desktop，避免触碰 Win32 基础设施。
    """
    if not IS_WINDOWS:
        return None
    from .desktop import create_input_backend
    return create_input_backend(mode="post", delay_config=delay_config)


# ─── 全局热键 ─────────────────────────────────────────────

def start_global_hotkeys(hotkeys: dict[str, Callable]) -> "GlobalHotKeys | None":
    """启动 pynput 全局热键，返回已 start 的 Listener。

    - 启动前自动安装 pynput 钩子防护补丁（必须在 Listener 启动前）
    - Windows 上失败直接抛出（历史行为，不静默吞错）
    - macOS 需「输入监控/辅助功能」权限，未授权时返回 None，
      由调用方降级为窗口内热键
    """
    from .pynput_patch import install as _install_pynput_patch
    _install_pynput_patch()
    try:
        from pynput import keyboard as pynput_keyboard
        listener = pynput_keyboard.GlobalHotKeys(hotkeys)
        listener.start()
        return listener
    except Exception as e:
        if IS_WINDOWS:
            raise
        logger.warning(f"全局热键不可用，已降级为窗口内热键: {e}")
        return None


# ─── 工作流原生弹窗回退（无 Qt 回调时）────────────────────

def _osa_quote(text: str) -> str:
    """转为 AppleScript 字符串字面量（转义反斜杠与双引号）"""
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _osascript(script: str, blocking: bool = True) -> str:
    """执行 osascript（macOS），阻塞时返回 stdout，非阻塞立即返回空串"""
    import subprocess
    cmd = ["osascript", "-e", script]
    if not blocking:
        subprocess.Popen(cmd)
        return ""
    r = subprocess.run(cmd, capture_output=True, text=True)
    return r.stdout.strip()


def native_confirm(text: str) -> bool:
    """平台原生确认弹窗（是/否），返回 bool"""
    if IS_WINDOWS:
        import ctypes
        result = ctypes.windll.user32.MessageBoxW(0, text, "工作流确认", 4 | 32)
        return result == 6
    if IS_MACOS:
        out = _osascript(
            f'display dialog {_osa_quote(text)} with title "工作流确认" '
            'buttons {"否", "是"} default button "是"'
        )
        return out.endswith("是")
    logger.warning(f"confirm(): 当前平台无原生弹窗回退，默认返回 false: {text}")
    return False


def native_pause(text: str) -> None:
    """平台原生阻塞弹窗（确定后返回）"""
    if IS_WINDOWS:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, text, "工作流暂停", 0x40)
        return
    if IS_MACOS:
        _osascript(
            f'display dialog {_osa_quote(text)} with title "工作流暂停" '
            'buttons {"确定"} default button "确定"'
        )
        return
    logger.warning(f"pause(): 当前平台无原生弹窗回退，不阻塞继续执行: {text}")


def native_notify(text: str) -> None:
    """平台原生非阻塞通知（Windows 5 秒自动关闭；macOS 通知中心）"""
    if IS_MACOS:
        _osascript(
            f'display notification {_osa_quote(text)} with title "工作流通知"',
            blocking=False,
        )
        return
    if not IS_WINDOWS:
        logger.info(f"[通知] {text}")
        return

    import ctypes
    import threading

    def _show():
        try:
            # MessageBoxTimeoutW(hwnd, text, caption, type, wLanguageId, dwMilliseconds)
            # 未文档化但自 Win2000 起稳定导出；wLanguageId=0 表示默认语言
            ctypes.windll.user32.MessageBoxTimeoutW(
                0, text, "工作流通知", 0x40, 0, 5000
            )
        except Exception as e:
            logger.warning(f"notify 弹窗失败: {e}")

    threading.Thread(target=_show, daemon=True, name="wf-notify").start()


# ─── adb 路径候选 ─────────────────────────────────────────

def adb_path_candidates() -> list[str]:
    """PATH 之外的平台常见 adb 安装位置（按优先级排列）"""
    import os
    if IS_WINDOWS:
        # 常见 SDK platform-tools 位置（Windows）
        return [
            os.path.expandvars(r"%LOCALAPPDATA%\Android\Sdk\platform-tools\adb.exe"),
            os.path.expandvars(r"%ANDROID_HOME%\platform-tools\adb.exe"),
            os.path.expandvars(r"%ANDROID_SDK_ROOT%\platform-tools\adb.exe"),
        ]
    # macOS / Linux：Android Studio 默认 SDK、Homebrew、手动安装
    candidates = [
        os.path.expanduser("~/Library/Android/sdk/platform-tools/adb"),
        "/opt/homebrew/bin/adb",
        "/usr/local/bin/adb",
    ]
    sdk_env = os.environ.get("ANDROID_HOME") or os.environ.get("ANDROID_SDK_ROOT")
    if sdk_env:
        candidates.insert(0, os.path.join(sdk_env, "platform-tools", "adb"))
    return candidates
