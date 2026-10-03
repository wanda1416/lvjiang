"""平台差异适配层——主流程中 Windows/macOS 的行为差异统一收口。

只收「主流程分支」：桌面输入后端创建、全局热键启停策略、
工作流原生弹窗回退、adb 候选路径、后台子进程窗口抑制、桌面投屏入口可见性。

窗口截图/输入注入等具体实现本身已按平台拆分
（core/desktop/ 仅 Windows；core/android/ 跨平台），
其内部的平台门控不在此层重复抽象。
"""
from __future__ import annotations

import subprocess
import sys
from typing import TYPE_CHECKING, Callable

from loguru import logger

from ..i18n import tr

if TYPE_CHECKING:
    from pynput.keyboard import GlobalHotKeys

    from ..core.config import InputSimConfig
    from .input_base import InputBackend

IS_WINDOWS = sys.platform == "win32"
IS_MACOS = sys.platform == "darwin"

# 桌面投屏模式（窗口扫描/定位/Win32 输入）仅 Windows 可用；
# 非 Windows 只支持 ADB 模式（UI 层据此隐藏窗口扫描入口）
DESKTOP_BACKEND_AVAILABLE = IS_WINDOWS


def is_process_elevated() -> bool | None:
    """当前 Windows 进程是否使用提升后的令牌；非 Windows 返回 ``None``。

    查询失败也返回 ``None``，由要求管理员权限的调用方按失败关闭处理。不要
    根据管理员组成员身份判断：UAC 下管理员账号的普通令牌仍然没有提升权限。
    """
    if not IS_WINDOWS:
        return None

    import ctypes
    from ctypes import wintypes

    token_query = 0x0008
    token_elevation_class = 20

    class TokenElevation(ctypes.Structure):
        _fields_ = [("TokenIsElevated", wintypes.DWORD)]

    token = wintypes.HANDLE()
    try:
        kernel32 = ctypes.windll.kernel32
        advapi32 = ctypes.windll.advapi32
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        advapi32.OpenProcessToken.argtypes = (
            wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE),
        )
        advapi32.OpenProcessToken.restype = wintypes.BOOL
        advapi32.GetTokenInformation.argtypes = (
            wintypes.HANDLE, ctypes.c_uint, ctypes.c_void_p, wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
        )
        advapi32.GetTokenInformation.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel32.CloseHandle.restype = wintypes.BOOL
        if not advapi32.OpenProcessToken(
            kernel32.GetCurrentProcess(), token_query, ctypes.byref(token),
        ):
            raise ctypes.WinError()
        elevation = TokenElevation()
        returned = wintypes.DWORD()
        if not advapi32.GetTokenInformation(
            token,
            token_elevation_class,
            ctypes.byref(elevation),
            ctypes.sizeof(elevation),
            ctypes.byref(returned),
        ):
            raise ctypes.WinError()
        return bool(elevation.TokenIsElevated)
    except (AttributeError, OSError) as exc:
        logger.warning(f"无法检查当前进程的 Windows 管理员权限: {exc}")
        return None
    finally:
        if token.value:
            ctypes.windll.kernel32.CloseHandle(token)

# 后台 CLI 子进程（adb 等）统一附加参数：Windows 下 windowed（console=False）
# 打包运行时进程自身无控制台，每次 subprocess 都会给子进程新开控制台窗口，
# 必须用 CREATE_NO_WINDOW 抑制；开发模式/非 Windows 无副作用。
# 用法：subprocess.run([...], **SUBPROCESS_NO_WINDOW)
SUBPROCESS_NO_WINDOW: dict = (
    {"creationflags": subprocess.CREATE_NO_WINDOW} if IS_WINDOWS else {}
)

# 读 CLI 输出统一用这组参数，不要裸写 text=True。
#
# `text=True` 不带 encoding 时按**本地 locale** 解码：中文 Windows 上是 GBK，
# 而 adb / Android 的输出是 UTF-8。轻则中文窗口标题变乱码，重则直接抛
# UnicodeDecodeError——`dumpsys window windows` 有几百 KB，里面混进一个非 GBK
# 字节，subprocess 的读取线程就在 fh.read() 处炸掉，调用方只拿到一条孤立的
# 线程 traceback，而那次探测静默失败。
#
# errors="replace" 而不是 "ignore"：dumpsys 这类大块输出里混进非法字节是常态，
# 要的是「尽量读出可用文本」；ignore 会悄悄删字符，replace 留下的 U+FFFD 至少
# 能让人看出这里原本有东西。
#
# 用法：subprocess.run([...], **SUBPROCESS_TEXT, **SUBPROCESS_NO_WINDOW)
SUBPROCESS_TEXT: dict = {
    "text": True, "encoding": "utf-8", "errors": "replace",
}

# Windows 自带 CLI（ipconfig 等）的输出走**控制台代码页**而不是 UTF-8，
# 按 UTF-8 解会把中文适配器名解成乱码。"oem" 正是 Python 为这种场合提供的
# 别名；非 Windows 上没有这个编码，回落到 UTF-8（那边本来就是 UTF-8）。
SUBPROCESS_TEXT_OEM: dict = (
    {"text": True, "encoding": "oem", "errors": "replace"} if IS_WINDOWS
    else SUBPROCESS_TEXT
)


# ─── 桌面输入后端 ─────────────────────────────────────────

def create_desktop_input(input_sim: "InputSimConfig | None" = None) -> "InputBackend | None":
    """创建桌面输入后端（PostMessage 后台模式）；非 Windows 返回 None。

    非 Windows 平台不 import core.desktop，避免触碰 Win32 基础设施。
    """
    if not IS_WINDOWS:
        return None
    from .desktop import create_input_backend
    return create_input_backend(mode="post", input_sim=input_sim)


# ─── 全局热键 ─────────────────────────────────────────────

def hotkey_pynput_token(key: str) -> str:
    """把展示用的按键名（如 "F9"）转换成 pynput GlobalHotKeys 需要的 token（"<f9>"）。

    供 start_global_hotkeys() 的调用方按用户配置的 HotkeyConfig 动态拼绑定表用，
    避免每处调用各自手写 f"<{key.lower()}>"。
    """
    return f"<{key.lower()}>"


def start_global_hotkeys(hotkeys: dict[str, Callable]) -> "GlobalHotKeys | None":
    """启动 pynput 全局热键，返回已 start 的 Listener。

    - 启动前自动安装 pynput 钩子防护补丁（必须在 Listener 启动前）
    - Windows 上失败直接抛出（历史行为，不静默吞错）
    - macOS 上 pynput 的 CGEventTap 实现会导致 libffi 500+ 层递归，
      长时间运行触发 use-after-free 崩溃，因此直接跳过，降级为窗口内热键
    - macOS 需「输入监控/辅助功能」权限，未授权时返回 None，
      由调用方降级为窗口内热键
    """
    from .access import is_readonly
    if is_readonly():
        logger.info("只读实例无权注册全局热键")
        return None
    if IS_MACOS:
        # macOS 上 pynput GlobalHotKeys 使用 CGEventTap + libffi，
        # 事件回调深度递归（500+ 层 ffi_call_int），
        # 长时间运行后触发 native crash（EXC_BAD_ACCESS）。
        # 直接跳过，由调用方降级为 Qt keyPressEvent 窗口内热键。
        logger.info("macOS 上跳过 pynput 全局热键（libffi 递归问题），使用窗口内热键")
        return None
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
    cmd = ["osascript", "-e", script]
    if not blocking:
        subprocess.Popen(cmd)
        return ""
    r = subprocess.run(cmd, capture_output=True, **SUBPROCESS_TEXT)
    return r.stdout.strip()


def native_confirm(text: str) -> bool:
    """平台原生确认弹窗（是/否），返回 bool"""
    if IS_WINDOWS:
        import ctypes
        result = ctypes.windll.user32.MessageBoxW(0, text, tr("工作流确认"), 4 | 32)
        return result == 6
    if IS_MACOS:
        out = _osascript(
            f'display dialog {_osa_quote(text)} with title "工作流确认" '
            'buttons {"否", "是"} default button "是"'
        )
        return out.endswith(tr("是"))
    logger.warning(f"confirm(): 当前平台无原生弹窗回退，默认返回 false: {text}")
    return False


def native_pause(text: str) -> None:
    """平台原生阻塞弹窗（确定后返回）"""
    if IS_WINDOWS:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, text, tr("工作流暂停"), 0x40)
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
                0, text, tr("工作流通知"), 0x40, 0, 5000
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
