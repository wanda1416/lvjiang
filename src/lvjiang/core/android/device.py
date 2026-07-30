"""AdbDevice - adb 可执行/serial 解析、shell/forward/push、设备属性查询"""

import re
import shutil
import subprocess

from loguru import logger


def _resolve_adb_path() -> str:
    """解析 adb 可执行路径：优先 PATH，其次常见安装位置，找不到返回 'adb'"""
    found = shutil.which("adb")
    if found:
        return found
    # 常见 SDK platform-tools 位置（Windows）
    import os
    candidates = [
        os.path.expandvars(r"%LOCALAPPDATA%\Android\Sdk\platform-tools\adb.exe"),
        os.path.expandvars(r"%ANDROID_HOME%\platform-tools\adb.exe"),
        os.path.expandvars(r"%ANDROID_SDK_ROOT%\platform-tools\adb.exe"),
    ]
    for c in candidates:
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
            capture_output=True, text=True, timeout=10,
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

    # ─── 命令前缀 ─────────────────────────────────────────

    def _base(self) -> list[str]:
        cmd = [self.adb_path]
        if self.serial:
            cmd += ["-s", self.serial]
        return cmd

    # ─── 基础执行 ─────────────────────────────────────────

    def shell(self, *args: str, timeout: float = 15.0) -> str:
        """执行 adb shell 命令，返回 stdout 文本"""
        r = subprocess.run(
            [*self._base(), "shell", *args],
            capture_output=True, text=True, timeout=timeout,
        )
        if r.returncode != 0:
            logger.debug(f"adb shell {args} 返回码 {r.returncode}: {r.stderr.strip()}")
        return r.stdout.strip()

    def shell_bytes(self, *args: str, timeout: float = 15.0) -> bytes:
        """执行 adb 命令返回原始字节流（如 exec-out screencap）"""
        r = subprocess.run(
            [*self._base(), *args],
            capture_output=True, timeout=timeout,
        )
        return r.stdout

    def forward(self, local: str, remote: str) -> bool:
        """建立端口转发 local -> remote，成功返回 True"""
        r = subprocess.run(
            [*self._base(), "forward", local, remote],
            capture_output=True, text=True, timeout=10,
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
                capture_output=True, timeout=10,
            )
        except Exception:
            pass

    def push(self, local_path: str, remote_path: str) -> bool:
        """推送文件到设备，成功返回 True"""
        r = subprocess.run(
            [*self._base(), "push", local_path, remote_path],
            capture_output=True, text=True, timeout=60,
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
