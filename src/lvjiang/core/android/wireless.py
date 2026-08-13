"""ADB 无线连接 - 局域网扫描 + 自动连接

核心流程：
1. 获取本机局域网子网
2. 并发扫描子网内所有 IP 的 ADB 端口（默认 5555）
3. 对开放端口的 IP 执行 adb connect
4. 返回已成功连接的设备列表

也可用于将 USB 设备切换为无线连接（enable_tcpip）。
"""

import os
import re
import shutil
import socket
import subprocess
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed

from loguru import logger

from ..platforms import SUBPROCESS_NO_WINDOW, adb_path_candidates


def resolve_adb() -> str:
    """解析 adb 可执行路径

    优先级：PATH → 随包内置（data/adb）→ 平台常见安装位置，均未命中返回 'adb'。
    """
    found = shutil.which("adb")
    if found:
        return found

    from ...constants import PROJECT_ROOT
    bundled = PROJECT_ROOT / "data" / "adb" / ("adb.exe" if os.name == "nt" else "adb")
    if bundled.exists():
        return str(bundled)
    for c in adb_path_candidates():
        if c and "%" not in c and os.path.exists(c):
            return c
    return "adb"


def run_adb(adb: str, *args: str, timeout: float = 10) -> str:
    """执行 adb 命令，返回 stdout"""
    r = subprocess.run(
        [adb, *args],
        capture_output=True, text=True, timeout=timeout, **SUBPROCESS_NO_WINDOW,
    )
    return r.stdout.strip()


def _is_private_ip(ip: str) -> bool:
    """判断是否为 RFC1918 私有地址（排除 VPN/代理虚拟网段）"""
    parts = ip.split(".")
    if len(parts) != 4:
        return False
    try:
        a, b = int(parts[0]), int(parts[1])
    except ValueError:
        return False
    # 10.0.0.0/8
    if a == 10:
        return True
    # 172.16.0.0/12
    if a == 172 and 16 <= b <= 31:
        return True
    # 192.168.0.0/16
    if a == 192 and b == 168:
        return True
    return False


def _enumerate_local_ips() -> list[str]:
    """枚举本机所有 IPv4 地址（纯标准库，兼容 Windows/macOS/Linux）"""
    ips: list[str] = []
    # 方式一：socket.getaddrinfo（轻量，覆盖多数场景）
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = str(info[4][0])
            if ip not in ips:
                ips.append(ip)
    except Exception:
        pass
    # 方式二：Windows 下解析 ipconfig 输出（覆盖 VPN/多网卡场景）
    if os.name == "nt":
        try:
            out = subprocess.run(
                ["ipconfig"], capture_output=True, text=True,
                timeout=5, **SUBPROCESS_NO_WINDOW,
            ).stdout
            for m in re.finditer(r"IPv4.*?:\s*(\d+\.\d+\.\d+\.\d+)", out):
                ip = m.group(1)
                if ip not in ips:
                    ips.append(ip)
        except Exception:
            pass
    return ips


def get_local_subnet() -> str | None:
    """获取本机局域网子网前缀（如 '192.168.1.'）

    遍历本机所有 IPv4 地址，选择 RFC1918 私有地址段，
    避免 VPN/代理软件（198.18.x.x 等虚拟网段）干扰。
    兜底使用 socket.connect 技巧。
    """
    for ip in _enumerate_local_ips():
        if _is_private_ip(ip):
            parts = ip.split(".")
            return ".".join(parts[:3]) + "."

    # 兜底：socket 技巧（可能被 VPN 干扰）
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
        s.close()
        if _is_private_ip(local_ip):
            parts = local_ip.split(".")
            return ".".join(parts[:3]) + "."
    except Exception:
        pass
    return None


def probe_port(ip: str, port: int, timeout: float = 0.3) -> bool:
    """探测 IP:port 是否可达（TCP connect）"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout)
        s.connect((ip, port))
        s.close()
        return True
    except Exception:
        return False


def scan_lan_for_adb(
    subnet: str,
    port: int,
    max_workers: int = 50,
    progress_cb: Callable[[str, int, int], None] | None = None,
) -> list[str]:
    """扫描局域网子网，返回端口开放的所有 IP

    Args:
        subnet: 子网前缀，如 '192.168.1.'
        port: ADB 端口，默认 5555
        max_workers: 并发线程数
        progress_cb: 进度回调 (message, current, total)

    Returns:
        开放该端口的 IP 列表，按末位数字排序
    """
    candidates = [f"{subnet}{i}" for i in range(1, 255)]
    total = len(candidates)
    found: list[str] = []
    completed = 0
    logger.info(f"正在扫描 {subnet}0/24 的 {port} 端口 ...")
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(probe_port, ip, port): ip for ip in candidates}
        for future in as_completed(futures):
            completed += 1
            ip = futures[future]
            if future.result():
                found.append(ip)
                logger.debug(f"发现 {ip}:{port} 开放")
                if progress_cb:
                    progress_cb(f"发现 {ip}:{port} 开放端口", completed, total)
            elif progress_cb and completed % 10 == 0:
                # 每 10 个 IP 更新一次进度
                progress_cb(f"正在扫描 {ip}...", completed, total)
    return sorted(found, key=lambda ip: int(ip.split(".")[-1]))


def connect_wireless(adb: str, ip: str, port: int, timeout: float = 5) -> bool:
    """尝试 adb connect ip:port，返回是否成功"""
    target = f"{ip}:{port}"
    try:
        out = subprocess.run(
            [adb, "connect", target],
            capture_output=True, text=True, timeout=timeout, **SUBPROCESS_NO_WINDOW,
        ).stdout.strip()
        return "connected" in out.lower()
    except Exception:
        return False


def get_adb_device_info(adb: str, ip: str, port: int) -> dict | None:
    """adb connect 后从 adb devices -l 获取设备信息

    Returns:
        {"serial": "ip:port", "model": str} 或 None
    """
    target = f"{ip}:{port}"
    out = run_adb(adb, "devices", "-l")
    for line in out.splitlines()[1:]:
        line = line.strip()
        if not line or target not in line:
            continue
        if "device" not in line.split():
            continue
        model = ""
        m = re.search(r"model:(\S+)", line)
        if m:
            model = m.group(1)
        return {"serial": target, "model": model}
    return None


def get_device_ip(adb: str, serial: str) -> str | None:
    """通过 adb shell 获取设备局域网 IP"""
    for cmd_args in [
        ["shell", "ip", "-4", "addr", "show", "wlan0"],
        ["shell", "ifconfig", "wlan0"],
    ]:
        try:
            out = subprocess.run(
                [adb, "-s", serial, *cmd_args],
                capture_output=True, text=True, timeout=5, **SUBPROCESS_NO_WINDOW,
            ).stdout
            m = re.search(r"inet\s+(\d+\.\d+\.\d+\.\d+)", out)
            if m:
                return m.group(1)
        except Exception:
            continue
    return None


def enable_tcpip(adb: str, serial: str, port: int) -> bool:
    """开启设备的 ADB TCP 模式（用于 USB 转无线）"""
    out = run_adb(adb, "-s", serial, "tcpip", str(port))
    return "restarting" in out.lower() or str(port) in out


def scan_and_connect_wireless(
    port: int = 5555,
    progress_cb: Callable[[str, int, int], None] | None = None,
) -> list[dict]:
    """扫描局域网并尝试连接所有发现的 ADB 设备

    流程：
    1. 获取本机局域网子网
    2. 并发扫描子网内所有 IP 的指定端口
    3. 对开放端口的 IP 执行 adb connect
    4. 获取已连接设备信息

    Args:
        port: ADB 端口，默认 5555
        progress_cb: 进度回调 (message, current, total)

    Returns:
        [{"serial": "ip:port", "model": str}, ...]
    """
    adb = resolve_adb()
    subnet = get_local_subnet()
    if not subnet:
        logger.warning("无法获取本机局域网地址")
        return []

    logger.info(f"本机局域网地址段: {subnet}0/24")
    if progress_cb:
        progress_cb(f"正在扫描 {subnet}0/24 ...", 0, 254)
    open_ips = scan_lan_for_adb(subnet, port, progress_cb=progress_cb)

    if not open_ips:
        logger.info(f"局域网内未发现端口 {port} 开放的设备")
        return []

    logger.info(f"发现 {len(open_ips)} 个 IP 开放端口 {port}，尝试 adb connect")
    devices: list[dict] = []
    scan_total = 254  # 扫描阶段总数，用于进度条连续性
    connect_total = len(open_ips)
    for i, ip in enumerate(open_ips, 1):
        # 进度条从扫描阶段继续，避免回退跳跃
        current = scan_total + i
        total = scan_total + connect_total
        if progress_cb:
            progress_cb(f"正在连接 {ip}:{port}...", current, total)
        if connect_wireless(adb, ip, port):
            time.sleep(0.5)
            info = get_adb_device_info(adb, ip, port)
            if info:
                devices.append(info)
                logger.info(f"已连接 {ip}:{port} model={info['model']}")
                if progress_cb:
                    progress_cb(f"已连接 {ip} ({info['model']})", current, total)
            else:
                # 连接成功但未识别设备信息，仍加入列表
                devices.append({"serial": f"{ip}:{port}", "model": ""})
                logger.info(f"已连接 {ip}:{port} 但未识别设备信息")
                if progress_cb:
                    progress_cb(f"已连接 {ip} (未知设备)", current, total)
        else:
            logger.debug(f"{ip}:{port} 连接失败（端口开放但非 ADB 服务）")

    return devices


__all__ = [
    "resolve_adb",
    "run_adb",
    "get_local_subnet",
    "scan_lan_for_adb",
    "connect_wireless",
    "get_adb_device_info",
    "get_device_ip",
    "enable_tcpip",
    "scan_and_connect_wireless",
]
