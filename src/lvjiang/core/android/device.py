"""AdbDevice - adb 可执行/serial 解析、shell/forward/push、设备属性查询"""

import os
import re
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Callable

from loguru import logger

from ..config.models import AndroidRecoveryConfig
from ..paths import data_directory
from ..platforms import (
    SUBPROCESS_NO_WINDOW,
    SUBPROCESS_TEXT,
    adb_path_candidates,
)
from .recovery import AdbConnectionError as AdbConnectionError
from .recovery import AndroidRecovery


@dataclass(frozen=True)
class AndroidDeviceIdentity:
    """与当前 ADB transport 分离的逻辑设备身份。"""

    value: str
    source: str
    stable: bool


def _resolve_adb_path() -> str:
    """解析 adb 可执行路径

    优先级：PATH → 随包内置（data/adb，打包端免装 adb）→ 平台常见安装位置，
    均未命中返回 'adb'。
    """
    found = shutil.which("adb")
    if found:
        return found
    bundled = data_directory() / "adb" / ("adb.exe" if os.name == "nt" else "adb")
    if bundled.exists():
        return str(bundled)
    for c in adb_path_candidates():
        if c and "%" not in c and os.path.exists(c):
            return c
    return "adb"


def list_adb_devices(
    adb_path: str | None = None,
    *,
    cancel_check: Callable[[], bool] | None = None,
) -> list[dict]:
    """列出已连接（device 状态）的设备

    Returns:
        [{"serial": str, "model": str}, ...]
    """
    adb = adb_path or _resolve_adb_path()
    try:
        process = subprocess.Popen(
            [adb, "devices", "-l"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            **SUBPROCESS_TEXT,
            **SUBPROCESS_NO_WINDOW,
        )
        deadline = time.monotonic() + 10.0
        while process.poll() is None:
            if cancel_check is not None and cancel_check():
                process.terminate()
                try:
                    process.communicate(timeout=0.5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.communicate()
                return []
            if time.monotonic() >= deadline:
                process.kill()
                process.communicate()
                raise subprocess.TimeoutExpired([adb, "devices", "-l"], 10)
            time.sleep(0.05)
        out, _stderr = process.communicate()
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
        self._identity: AndroidDeviceIdentity | None = None
        # ── 断连暂停恢复 ──
        self.on_connection_lost: Callable[[str], None] | None = None
        self.on_connection_restored: Callable[[], None] | None = None
        self.resume_event = threading.Event()
        self.resume_event.set()  # 初始为已恢复，不阻塞
        self.stop_check: Callable[[], bool] | None = None
        self.can_wait_for_recovery: Callable[[], bool] | None = None
        self.recovery = AndroidRecoveryConfig()
        self.recovery_coordinator = AndroidRecovery(self)

    # ─── 命令前缀 ─────────────────────────────────────────

    def _base(self) -> list[str]:
        cmd = [self.adb_path]
        if self.serial:
            cmd += ["-s", self.serial]
        return cmd

    def get_stable_identity(self) -> AndroidDeviceIdentity:
        """读取跨 USB/无线 transport 一致的设备身份。

        Android ID 通常对同一设备用户稳定，且不随 ADB 的连接地址变化；设备
        未提供时再使用硬件序列属性。

        三者都不可用时再读内核 boot_id：它对本次开机的同一台设备恒定、不同
        设备必然不同，因此足以把 USB 和无线两条 transport 归并为一个目标；
        代价是重启后换值，于是重启前的目标行会留在列表里由用户自己断开。这
        比让一台设备变成两个可执行目标要好得多——目标互斥是按 target_id 算
        的，分裂之后同一台手机可以被两个任务同时操作。

        连 boot_id 都读不到才保留 transport 作为显式的不稳定回退，上层不得
        用它自动归并不同连接。
        """
        if self._identity is not None:
            return self._identity

        candidates = (
            ("android_id", ("settings", "get", "secure", "android_id")),
            ("ro.serialno", ("getprop", "ro.serialno")),
            ("ro.boot.serialno", ("getprop", "ro.boot.serialno")),
            ("boot_id", ("cat", "/proc/sys/kernel/random/boot_id")),
        )
        for source, command in candidates:
            try:
                value = self.shell(*command, timeout=5.0, retry_safe=True).strip()
            except Exception as exc:  # noqa: BLE001
                logger.debug(f"读取 Android 设备身份 {source} 失败，尝试下一来源: {exc}")
                continue
            if value and value.lower() not in {"null", "unknown"}:
                self._identity = AndroidDeviceIdentity(value, source, True)
                return self._identity

        value = str(self.serial or "").strip()
        self._identity = AndroidDeviceIdentity(value, "transport", False)
        return self._identity

    # ─── 断连检测 ─────────────────────────────────────────

    _DISCONNECT_KEYWORDS = ("not found", "device offline", "no devices", "connection refused")

    def _is_disconnect_error(self, stderr: str) -> bool:
        msg = stderr.strip().lower()
        return any(kw in msg for kw in self._DISCONNECT_KEYWORDS)

    # ─── 基础执行 ─────────────────────────────────────────

    def wait_retry_interval(self) -> None:
        self.recovery_coordinator.wait_interval()

    def _reconnect_once(self) -> bool:
        """只恢复当前 transport，不 kill-server，不发任何游戏输入。"""
        def run(args):
            return subprocess.run(args, capture_output=True, timeout=3.0,
                                  **SUBPROCESS_TEXT, **SUBPROCESS_NO_WINDOW)
        def probe():
            result = run([*self._base(), "shell", "echo", "lvjiang-ready"])
            return result.returncode == 0 and (result.stdout or "").strip() == "lvjiang-ready"
        if probe():
            return True
        run([self.adb_path, "start-server"])
        if self.serial and ":" in self.serial:
            run([self.adb_path, "connect", self.serial])
        else:
            run([*self._base(), "reconnect"])
        return probe()

    def reconnect(self) -> bool:
        return self.recovery_coordinator.retry(
            "ADB", self._reconnect_once, self.recovery.adb_attempts,
            (subprocess.TimeoutExpired, OSError))

    def _handle_connection_error(self, error: Exception, cmd_desc: str, *, retry_safe: bool = False,
                                 failed_attempts: int = 1) -> bool:
        """自动恢复耗尽后等待用户确认；调用方随后重试原命令。"""
        return self.recovery_coordinator.recover_adb(
            error, cmd_desc, retry_safe=retry_safe, failed_attempts=failed_attempts)

    def wait_for_connection_recovery(self, error: Exception, cmd_desc: str) -> None:
        self.recovery_coordinator.wait_for_user(error, cmd_desc)

    def shell(self, *args: str, timeout: float = 15.0, retry_safe: bool = False) -> str:
        """执行 adb shell 命令；仅明确只读的调用方可声明 retry_safe。"""
        retried = False
        failures = 0
        while True:
            try:
                if retried:
                    self.recovery_coordinator.check_stop()
                    logger.info(f"ADB 重试: adb shell {' '.join(args)}")
                r = subprocess.run(
                    [*self._base(), "shell", *args],
                    capture_output=True, timeout=timeout,
                    **SUBPROCESS_TEXT, **SUBPROCESS_NO_WINDOW,
                )
                if r.returncode != 0:
                    # 某些打包/代理环境在命令没有对应输出流时会返回 None。
                    # shell() 的公开契约始终是文本，不能让可选的进程输出泄漏
                    # 成 ``None.strip()``，进而把非关键的应用探测误报成异常。
                    stderr_msg = (r.stderr or "").strip()
                    if self._is_disconnect_error(stderr_msg):
                        raise OSError(stderr_msg)
                    logger.debug(f"adb shell {args} 返回码 {r.returncode}: {stderr_msg}")
                if r.returncode == 0:
                    self.recovery_coordinator.succeeded()
                return (r.stdout or "").strip()
            except (subprocess.TimeoutExpired, OSError) as e:
                failures += 1
                if self._handle_connection_error(e, f"adb shell {args}", retry_safe=retry_safe, failed_attempts=failures):
                    failures = 0
                retried = True

    def shell_bytes(self, *args: str, timeout: float = 15.0, retry_safe: bool = False) -> bytes:
        """执行 adb 命令返回字节流；仅明确只读的调用方可声明 retry_safe。"""
        retried = False
        failures = 0
        while True:
            try:
                if retried:
                    self.recovery_coordinator.check_stop()
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
                if r.returncode == 0:
                    self.recovery_coordinator.succeeded()
                return r.stdout
            except (subprocess.TimeoutExpired, OSError) as e:
                failures += 1
                if self._handle_connection_error(e, f"adb {args}", retry_safe=retry_safe, failed_attempts=failures):
                    failures = 0
                retried = True

    def forward(self, local: str, remote: str) -> bool:
        """建立端口转发 local -> remote，成功返回 True"""
        r = subprocess.run(
            [*self._base(), "forward", local, remote],
            capture_output=True, timeout=10,
            **SUBPROCESS_TEXT, **SUBPROCESS_NO_WINDOW,
        )
        if r.returncode != 0:
            logger.error(f"adb forward {local} {remote} 失败: {r.stderr.strip()}")
            return False
        return True

    def forward_dynamic(self, remote: str) -> int | None:
        """让 adb 分配空闲本地 TCP 端口并转发到 ``remote``。

        ``adb forward tcp:0 ...`` 会在 stdout 返回实际分配的端口。端口由
        调用方保存，停止时必须用 :meth:`remove_forward` 精确删除，不能清除
        该设备的全部 forward。
        """
        r = subprocess.run(
            [*self._base(), "forward", "tcp:0", remote],
            capture_output=True, timeout=10,
            **SUBPROCESS_TEXT, **SUBPROCESS_NO_WINDOW,
        )
        output = (r.stdout or "").strip()
        if r.returncode != 0:
            logger.error(f"adb forward tcp:0 {remote} 失败: {(r.stderr or '').strip()}")
            return None
        try:
            port = int(output)
        except (TypeError, ValueError):
            logger.error(f"adb forward 未返回有效动态端口: {output!r}")
            return None
        if not 0 < port <= 65535:
            logger.error(f"adb forward 返回越界端口: {port}")
            return None
        return port

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
            capture_output=True, timeout=60,
            **SUBPROCESS_TEXT, **SUBPROCESS_NO_WINDOW,
        )
        if r.returncode != 0:
            logger.error(f"adb push {local_path} 失败: {r.stderr.strip()}")
            return False
        return True

    def install(
        self,
        apk_path: str,
        *,
        timeout: float = 300.0,
        cancel_check: Callable[[], bool] | None = None,
    ) -> str:
        """`adb install -r` 装包。

        adb 执行失败、取消或超时抛 RuntimeError；进程无法启动时可能直接
        传递 OSError，由调用方按自身失败边界处理。

        -r 保留数据升级；不加 -d（不允许降级）——降级装不上是对的，那通常意味着
        用户拿了个更旧的包，静默允许只会把问题推到运行期。
        """
        logger.info(f"[ADB] 安装 APK: {apk_path}")
        process = subprocess.Popen(
            [*self._base(), "install", "-r", str(apk_path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            **SUBPROCESS_TEXT,
            **SUBPROCESS_NO_WINDOW,
        )
        deadline = time.monotonic() + timeout
        while process.poll() is None:
            if cancel_check is not None and cancel_check():
                process.terminate()
                try:
                    process.communicate(timeout=1.0)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.communicate()
                raise RuntimeError("adb install 已取消")
            if time.monotonic() >= deadline:
                process.kill()
                process.communicate()
                raise RuntimeError(f"adb install 超时（{timeout:.0f}s）")
            time.sleep(0.05)
        stdout, stderr = process.communicate()
        output = f"{stdout}\n{stderr}".strip()
        # adb install 偶尔返回码为 0 却只在输出里报失败，所以两边都看
        if process.returncode != 0 or "Success" not in output:
            raise RuntimeError(output or f"adb install 失败（{process.returncode}）")
        return output

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
        out = self.shell("wm", "size", retry_safe=True)
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
            self._abi = self.shell("getprop", "ro.product.cpu.abi", retry_safe=True) or "arm64-v8a"
        return self._abi

    def get_sdk(self) -> int:
        """获取 sdk 版本（ro.build.version.sdk）"""
        if self._sdk is None:
            raw = self.shell("getprop", "ro.build.version.sdk", retry_safe=True)
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
