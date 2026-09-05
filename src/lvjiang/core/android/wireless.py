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
from typing import NamedTuple

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


class NetInterface(NamedTuple):
    """本机一张网卡上的一个 IPv4 地址

    name:    网卡名（Windows 为适配器描述，Unix 为 eth0/wlan0 等）
    ip:      本机在该网卡上的 IPv4 地址
    subnet:  该地址所在的 /24 前缀，如 '192.168.1.'
    private: 是否 RFC1918 私有地址（虚拟网卡常给出非私有段）
    """
    name: str
    ip: str
    subnet: str
    private: bool

    @property
    def label(self) -> str:
        """下拉列表用的展示文本"""
        return f"{self.name} — {self.subnet}0/24 ({self.ip})"


def _subnet_of(ip: str) -> str:
    """取 IPv4 的 /24 前缀，如 '192.168.1.23' → '192.168.1.'"""
    return ".".join(ip.split(".")[:3]) + "."


def _iter_windows_interfaces() -> list[tuple[str, str]]:
    """解析 ipconfig 输出，返回 [(适配器名, IPv4), ...]"""
    try:
        out = subprocess.run(
            ["ipconfig"], capture_output=True, text=True,
            timeout=5, encoding="utf-8", errors="ignore", **SUBPROCESS_NO_WINDOW,
        ).stdout
    except Exception:
        return []
    result: list[tuple[str, str]] = []
    current = ""
    for line in out.splitlines():
        if line and not line[0].isspace():
            # 适配器标题行，形如 "以太网适配器 以太网:" / "Wireless LAN adapter Wi-Fi:"
            current = line.strip().rstrip(":").strip()
            continue
        m = re.search(r"IPv4.*?:\s*(\d+\.\d+\.\d+\.\d+)", line)
        if m:
            result.append((current or "未知网卡", m.group(1)))
    return result


def _iter_unix_interfaces() -> list[tuple[str, str]]:
    """解析 `ip -4 addr` 或 `ifconfig` 输出，返回 [(网卡名, IPv4), ...]"""
    for cmd in (["ip", "-4", "-o", "addr", "show"], ["ifconfig"]):
        try:
            out = subprocess.run(
                [*cmd], capture_output=True, text=True, timeout=5,
            ).stdout
        except Exception:
            continue
        if not out:
            continue
        result: list[tuple[str, str]] = []
        if cmd[0] == "ip":
            # 形如 "2: eth0    inet 192.168.1.23/24 brd ..."
            for line in out.splitlines():
                m = re.match(r"\d+:\s+(\S+)\s+inet\s+(\d+\.\d+\.\d+\.\d+)", line)
                if m:
                    result.append((m.group(1), m.group(2)))
        else:
            current = ""
            for line in out.splitlines():
                if line and not line[0].isspace():
                    current = line.split(":")[0].split()[0]
                    continue
                m = re.search(r"inet\s+(?:addr:)?(\d+\.\d+\.\d+\.\d+)", line)
                if m:
                    result.append((current or "未知网卡", m.group(1)))
        if result:
            return result
    return []


def list_ipv4_interfaces(include_loopback: bool = False) -> list[NetInterface]:
    """枚举本机所有 IPv4 网卡地址（纯标准库，Windows/macOS/Linux 通用）

    多网卡（含 VMware/VirtualBox/WSL/VPN 虚拟网卡）场景下，
    仅凭单一「本机 IP」会漏掉目标设备所在的网段，
    因此这里把每张网卡都列出来交给上层选择。

    Args:
        include_loopback: 是否保留 127.x 环回地址（默认剔除）

    Returns:
        NetInterface 列表，私有网段排在前面，同一 (网卡, IP) 只出现一次。
    """
    pairs = _iter_windows_interfaces() if os.name == "nt" else _iter_unix_interfaces()
    if not pairs:
        # 兜底：命令不可用时退回 getaddrinfo，拿不到网卡名
        pairs = [("本机", ip) for ip in _enumerate_local_ips()]

    seen: set[tuple[str, str]] = set()
    items: list[NetInterface] = []
    for name, ip in pairs:
        if not include_loopback and ip.startswith("127."):
            continue
        if (name, ip) in seen:
            continue
        seen.add((name, ip))
        items.append(NetInterface(name=name, ip=ip, subnet=_subnet_of(ip), private=_is_private_ip(ip)))
    # 私有网段更可能是设备所在网段，排前面；其余保持枚举顺序
    return sorted(items, key=lambda i: not i.private)


def list_scan_subnets() -> list[str]:
    """列出待扫描的网段前缀（去重，私有段优先）

    没枚举到任何网卡时退回 get_local_subnet() 的单一结果。
    """
    subnets: list[str] = []
    for iface in list_ipv4_interfaces():
        if iface.subnet not in subnets:
            subnets.append(iface.subnet)
    if not subnets:
        fallback = get_local_subnet()
        if fallback:
            subnets.append(fallback)
    return subnets

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


# 常见模拟器在本机监听的 ADB 端口（多网卡时局域网扫描扫不到本机模拟器）
LOCAL_ADB_PORTS: tuple[int, ...] = (
    5555, 5556, 5557, 5558,          # 标准 emulator / 雷电 / LDPlayer
    5565, 5575, 5585,                # BlueStacks 多开
    7555,                            # MuMu
    16384, 16416,                    # MuMu 12
    21503,                           # 逍遥 / MEmu
    62001, 62025, 62026,             # 夜神 Nox
)


def _connect_targets(
    adb: str,
    targets: list[tuple[str, int]],
    progress_cb: Callable[[str, int, int], None] | None = None,
    base: int = 0,
    total: int | None = None,
) -> list[dict]:
    """对一批 (ip, port) 依次 adb connect，返回连上的设备信息

    Args:
        base/total: 进度条基准，用于和前面的扫描阶段拼成连续进度
    """
    total = total if total is not None else base + len(targets)
    devices: list[dict] = []
    for i, (ip, port) in enumerate(targets, 1):
        current = base + i
        if progress_cb:
            progress_cb(f"正在连接 {ip}:{port}...", current, total)
        if not connect_wireless(adb, ip, port):
            logger.debug(f"{ip}:{port} 连接失败（端口开放但非 ADB 服务）")
            continue
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
    return devices


def scan_and_connect_wireless(
    port: int = 5555,
    progress_cb: Callable[[str, int, int], None] | None = None,
    subnets: list[str] | None = None,
) -> list[dict]:
    """扫描局域网并尝试连接所有发现的 ADB 设备

    流程：
    1. 确定待扫描网段（未指定时取本机所有网卡的网段）
    2. 并发扫描各网段内所有 IP 的指定端口
    3. 对开放端口的 IP 执行 adb connect
    4. 获取已连接设备信息

    Args:
        port: ADB 端口，默认 5555
        progress_cb: 进度回调 (message, current, total)
        subnets: 指定网段前缀列表（如 ['192.168.1.']）；None 表示扫描全部网卡。
                 多网卡（VMware/WSL/VPN）场景下只扫单一网段会漏掉目标设备，
                 因此默认逐个网段全扫。

    Returns:
        [{"serial": "ip:port", "model": str}, ...]
    """
    adb = resolve_adb()
    targets_subnets = [s for s in (subnets if subnets is not None else list_scan_subnets()) if s]
    if not targets_subnets:
        logger.warning("无法获取本机局域网地址")
        return []

    logger.info(f"待扫描网段: {', '.join(s + '0/24' for s in targets_subnets)}")
    per_subnet = 254
    scan_total = per_subnet * len(targets_subnets)
    open_targets: list[tuple[str, int]] = []
    for idx, subnet in enumerate(targets_subnets):
        offset = per_subnet * idx
        if progress_cb:
            progress_cb(f"正在扫描 {subnet}0/24 ...", offset, scan_total)

        def sub_progress(message: str, current: int, _total: int, _offset: int = offset):
            if progress_cb:
                progress_cb(message, _offset + current, scan_total)

        for ip in scan_lan_for_adb(subnet, port, progress_cb=sub_progress if progress_cb else None):
            open_targets.append((ip, port))

    if not open_targets:
        logger.info(f"局域网内未发现端口 {port} 开放的设备")
        return []

    logger.info(f"发现 {len(open_targets)} 个 IP 开放端口 {port}，尝试 adb connect")
    return _connect_targets(
        adb, open_targets, progress_cb,
        base=scan_total, total=scan_total + len(open_targets),
    )


def scan_and_connect_local(
    ports: tuple[int, ...] | list[int] | None = None,
    progress_cb: Callable[[str, int, int], None] | None = None,
) -> list[dict]:
    """扫描本机开放的 ADB 端口并连接（用于发现模拟器）

    模拟器只在 127.0.0.1 上监听 ADB 端口，局域网扫描扫不到；
    多网卡环境下局域网扫描本就容易失手，本地扫描是更稳的兜底。

    Args:
        ports: 待探测端口，默认 LOCAL_ADB_PORTS
        progress_cb: 进度回调 (message, current, total)

    Returns:
        [{"serial": "127.0.0.1:port", "model": str}, ...]
    """
    adb = resolve_adb()
    port_list = list(ports if ports is not None else LOCAL_ADB_PORTS)
    ip = "127.0.0.1"
    total = len(port_list)
    open_targets: list[tuple[str, int]] = []
    logger.info(f"正在探测本机 {total} 个常见模拟器 ADB 端口 ...")
    for i, p in enumerate(port_list, 1):
        if progress_cb:
            progress_cb(f"正在探测 {ip}:{p}...", i, total)
        if probe_port(ip, p, timeout=0.2):
            logger.debug(f"发现 {ip}:{p} 开放")
            open_targets.append((ip, p))
            if progress_cb:
                progress_cb(f"发现 {ip}:{p} 开放端口", i, total)

    if not open_targets:
        logger.info("本机未发现开放的模拟器 ADB 端口")
        return []

    return _connect_targets(
        adb, open_targets, progress_cb,
        base=total, total=total + len(open_targets),
    )


__all__ = [
    "LOCAL_ADB_PORTS",
    "NetInterface",
    "resolve_adb",
    "run_adb",
    "get_local_subnet",
    "list_ipv4_interfaces",
    "list_scan_subnets",
    "scan_lan_for_adb",
    "connect_wireless",
    "get_adb_device_info",
    "get_device_ip",
    "enable_tcpip",
    "scan_and_connect_wireless",
    "scan_and_connect_local",
]
