"""AdbDevice - adb 可执行/serial 解析、shell/forward/push、设备属性查询"""

import os
import re
import shutil
import subprocess
import threading
import time
from typing import Callable

from loguru import logger

from ...constants import PROJECT_ROOT
from ..platforms import SUBPROCESS_NO_WINDOW, adb_path_candidates


class AdbConnectionError(RuntimeError):
    """ADB 连接/通信异常，用户恢复后重试或停止后终止"""


def _resolve_adb_path() -> str:
    """解析 adb 可执行路径

    优先级：PATH → 随包内置（data/adb，打包端免装 adb）→ 平台常见安装位置，
    均未命中返回 'adb'。
    """
    found = shutil.which("adb")
    if found:
        return found
    bundled = PROJECT_ROOT / "data" / "adb" / ("adb.exe" if os.name == "nt" else "adb")
    if bundled.exists():
        return str(bundled)
    for c in adb_path_candidates():
        if c and "%" not in c and os.path.exists(c):
            return c
    return "adb"


def list_adb_devices(adb_path: str | None = None) -> list[dict]:
    """列出已连接（device 状态）的设备

    Returns:
        [{"serial": str, "model": str}, ...]
    """
    adb = adb_path or _resolve_adb_path()
    try:
        out = subprocess.run(
            [adb, "devices", "-l"],
            capture_output=True, text=True, timeout=10, **SUBPROCESS_NO_WINDOW,
        ).stdout
    except Exception as e:
        logger.error(f"adb devices 执行失败: {e}")
        return []

    devices: list[dict] = []
    for line in out.splitlines()[1:]:  # 跳过 "List of devices attached"
        line = line.strip()
        if not line or "device" not in line.split():
            # 仅接受 state == device（排除 offline/unauthorized 及空行）
            continue
        parts = line.split()
        serial = parts[0]
        model = ""
        m = re.search(r"model:(\S+)", line)
        if m:
            model = m.group(1)
        devices.append({"serial": serial, "model": model})
    return devices


class AdbDevice:
    """封装单台设备的 adb 交互

    公开能力：
    - shell / shell_bytes：执行命令
    - forward：端口转发
    - push：推送文件
    - get_resolution / get_abi / get_sdk：设备属性
    """

    def __init__(self, serial: str | None = None, adb_path: str | None = None):
        self.adb_path = adb_path or _resolve_adb_path()
        self.serial = serial
        self._resolution: tuple[int, int] | None = None
        self._abi: str | None = None
        self._sdk: int | None = None
        # ── 断连暂停恢复 ──
        self.on_connection_lost: Callable[[str], None] | None = None
        self.resume_event = threading.Event()
        self.resume_event.set()  # 初始为已恢复，不阻塞
        self.stop_check: Callable[[], bool] | None = None

    # ─── 命令前缀 ─────────────────────────────────────────

    def _base(self) -> list[str]:
        cmd = [self.adb_path]
        if self.serial:
            cmd += ["-s", self.serial]
        return cmd

    # ─── 断连检测 ─────────────────────────────────────────

    _DISCONNECT_KEYWORDS = ("not found", "device offline", "no devices", "connection refused")

    def _is_disconnect_error(self, stderr: str) -> bool:
        msg = stderr.strip().lower()
        return any(kw in msg for kw in self._DISCONNECT_KEYWORDS)

    # ─── 基础执行 ─────────────────────────────────────────

    def _handle_connection_error(self, error: Exception, cmd_desc: str):
        """ADB 命令失败 → 通知 UI → 暂停工作流线程等待用户点「恢复」"""
        err_msg = f"{cmd_desc} 失败: {error}"
        logger.warning(f"ADB 连接异常: {err_msg}")
        if self.on_connection_lost:
            try:
                self.on_connection_lost(err_msg)
            except Exception:
                pass
        self.resume_event.clear()
        while True:
            if self.stop_check and self.stop_check():
                self.resume_event.set()
                raise AdbConnectionError(
                    f"{cmd_desc} 失败且用户停止: {error}"
                ) from None
            if self.resume_event.wait(timeout=1.0):
                logger.info("ADB 用户已恢复，等待设备稳定...")
                self.resume_event.clear()
                # 给设备短暂稳定时间（scrcpy 重连后 adb 需要几秒就绪）
                # 期间保持响应 F10 停止
                for _ in range(6):
                    if self.stop_check and self.stop_check():
                        self.resume_event.set()
                        raise AdbConnectionError(
                            f"{cmd_desc} 恢复后用户停止: {error}"
                        ) from None
                    time.sleep(0.5)
                return

    def shell(self, *args: str, timeout: float = 15.0) -> str:
        """执行 adb shell 命令，返回 stdout 文本"""
        retried = False
        while True:
            try:
                if retried:
                    logger.info(f"ADB 重试: adb shell {' '.join(args)}")
                r = subprocess.run(
                    [*self._base(), "shell", *args],
                    capture_output=True, text=True, timeout=timeout,
                    **SUBPROCESS_NO_WINDOW,
                )
                if r.returncode != 0:
                    stderr_msg = r.stderr.strip()
                    if self._is_disconnect_error(stderr_msg):
                        raise OSError(stderr_msg)
                    logger.debug(f"adb shell {args} 返回码 {r.returncode}: {stderr_msg}")
                return r.stdout.strip()
            except (subprocess.TimeoutExpired, OSError) as e:
                self._handle_connection_error(e, f"adb shell {args}")
                retried = True

    def shell_bytes(self, *args: str, timeout: float = 15.0) -> bytes:
        """执行 adb 命令返回原始字节流（如 exec-out screencap）"""
        retried = False
        while True:
            try:
                if retried:
                    logger.info(f"ADB 重试: adb {' '.join(args)}")
                r = subprocess.run(
                    [*self._base(), *args],
                    capture_output=True, timeout=timeout,
                    **SUBPROCESS_NO_WINDOW,
                )
                if r.returncode != 0:
                    stderr_msg = r.stderr.strip()
                    if isinstance(stderr_msg, bytes):
                        stderr_msg = stderr_msg.decode(errors="replace")
                    if self._is_disconnect_error(stderr_msg):
                        raise OSError(stderr_msg)
                return r.stdout
            except (subprocess.TimeoutExpired, OSError) as e:
                self._handle_connection_error(e, f"adb {args}")
                retried = True

    def forward(self, local: str, remote: str) -> bool:
        """建立端口转发 local -> remote，成功返回 True"""
        r = subprocess.run(
            [*self._base(), "forward", local, remote],
            capture_output=True, text=True, timeout=10, **SUBPROCESS_NO_WINDOW,
        )
        if r.returncode != 0:
            logger.error(f"adb forward {local} {remote} 失败: {r.stderr.strip()}")
            return False
        return True

    def remove_forward(self, local: str):
        """移除端口转发（忽略失败）"""
        try:
            subprocess.run(
                [*self._base(), "forward", "--remove", local],
                capture_output=True, timeout=10, **SUBPROCESS_NO_WINDOW,
            )
        except Exception:
            pass

    def push(self, local_path: str, remote_path: str) -> bool:
        """推送文件到设备，成功返回 True"""
        r = subprocess.run(
            [*self._base(), "push", local_path, remote_path],
            capture_output=True, text=True, timeout=60, **SUBPROCESS_NO_WINDOW,
        )
        if r.returncode != 0:
            logger.error(f"adb push {local_path} 失败: {r.stderr.strip()}")
            return False
        return True

    def start_shell_process(self, *args: str) -> subprocess.Popen:
        """启动一个 adb shell 子进程（历史用于常驻 minicap/minitouch，现保留为兼容桩）"""
        return subprocess.Popen(
            [*self._base(), "shell", *args],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            **SUBPROCESS_NO_WINDOW,
        )

    # ─── 设备属性（带缓存）─────────────────────────────────

    def get_resolution(self) -> tuple[int, int]:
        """获取物理分辨率 (width, height)，解析 `wm size`"""
        if self._resolution is not None:
            return self._resolution
        out = self.shell("wm", "size")
        # 优先 Physical size，其次 Override size
        w, h = 0, 0
        phys = re.search(r"Physical size:\s*(\d+)x(\d+)", out)
        over = re.search(r"Override size:\s*(\d+)x(\d+)", out)
        m = over or phys  # Override 优先反映当前实际
        if m:
            w, h = int(m.group(1)), int(m.group(2))
        else:
            logger.error(f"无法解析分辨率: {out!r}")
        self._resolution = (w, h)
        return self._resolution

    def get_abi(self) -> str:
        """获取主 abi（ro.product.cpu.abi）"""
        if self._abi is None:
            self._abi = self.shell("getprop", "ro.product.cpu.abi") or "arm64-v8a"
        return self._abi

    def get_sdk(self) -> int:
        """获取 sdk 版本（ro.build.version.sdk）"""
        if self._sdk is None:
            raw = self.shell("getprop", "ro.build.version.sdk")
            try:
                self._sdk = int(raw)
            except (ValueError, TypeError):
                logger.warning(f"无法解析 sdk: {raw!r}，回退 24")
                self._sdk = 24
        return self._sdk

    def is_online(self) -> bool:
        """设备是否处于 device 状态"""
        return any(d["serial"] == self.serial for d in list_adb_devices(self.adb_path)) \
            if self.serial else bool(list_adb_devices(self.adb_path))
