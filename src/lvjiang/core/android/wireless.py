"""ADB 无线连接 - 局域网设备发现 + 自动连接

核心流程（按可靠性排序，靠前者更便宜也更准）：
1. mDNS：读 `adb mdns services`，直接拿到设备广播的 IP:端口。
   Android 11+ 无线调试用随机端口，逐 IP 探测 5555 永远发现不了。
2. ARP 邻居表：发 UDP 触发 ARP 解析后读系统邻居表，不依赖 ICMP 存活。
   设备不回 ping、或不监听已知端口时，邻居表仍能证明它在同一广播域。
3. TCP 确认：只对上面两步得到的少量候选探测端口，超时与重试都从宽。
4. 兜底：两条协议通道都没有候选时，才逐 IP 扫描网段。
   跨 VLAN / 跨路由的网段邻居表看不到设备，只能靠这一步。
5. 对确认的端点执行 adb connect，并返回已成功连接的设备列表。

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

from ..platforms import (
    SUBPROCESS_NO_WINDOW,
    SUBPROCESS_TEXT,
    SUBPROCESS_TEXT_OEM,
    adb_path_candidates,
)


def resolve_adb() -> str:
    """解析 adb 可执行路径

    优先级：PATH → 随包内置（data/adb）→ 平台常见安装位置，均未命中返回 'adb'。
    """
    found = shutil.which("adb")
    if found:
        return found

    from ..paths import data_directory
    bundled = data_directory() / "adb" / ("adb.exe" if os.name == "nt" else "adb")
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
        capture_output=True, timeout=timeout,
        **SUBPROCESS_TEXT, **SUBPROCESS_NO_WINDOW,
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
                ["ipconfig"], capture_output=True, timeout=5,
                **SUBPROCESS_TEXT_OEM, **SUBPROCESS_NO_WINDOW,
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
            ["ipconfig"], capture_output=True, timeout=5,
            **SUBPROCESS_TEXT_OEM, **SUBPROCESS_NO_WINDOW,
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
                [*cmd], capture_output=True, timeout=5, **SUBPROCESS_TEXT,
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
    """列出待扫描的网段前缀（去重，仅私有网段）

    只返回 RFC1918 私有网段（10.0.0.0/8、172.16.0.0/12、192.168.0.0/16），
    过滤掉 VPN/代理虚拟网段（如 198.18.x.x）。
    没枚举到任何私有网卡时退回 get_local_subnet() 的单一结果。
    """
    subnets: list[str] = []
    for iface in list_ipv4_interfaces():
        if iface.private and iface.subnet not in subnets:
            subnets.append(iface.subnet)
    if not subnets:
        fallback = get_local_subnet()
        if fallback:
            subnets.append(fallback)
    return subnets

def _ping_host(ip: str, timeout: float = 0.02) -> bool:
    """快速 ping 探测 IP 是否存活（局域网内 20ms 无响应视为不存在）

    Args:
        ip: 目标 IP
        timeout: 超时时间（秒），默认 20ms

    Returns:
        True 如果 IP 存活，False 如果不可达
    """
    try:
        # Windows: ping -n 1 -w <ms> <ip>
        # Unix: ping -c 1 -W <sec> <ip>
        if os.name == "nt":
            timeout_ms = int(timeout * 1000)
            cmd = ["ping", "-n", "1", "-w", str(timeout_ms), ip]
        else:
            timeout_sec = max(1, int(timeout))
            cmd = ["ping", "-c", "1", "-W", str(timeout_sec), ip]
        result = subprocess.run(
            cmd, capture_output=True, timeout=timeout + 0.5,
            **SUBPROCESS_NO_WINDOW,
        )
        return result.returncode == 0
    except Exception:
        return False


def probe_port(ip: str, port: int, timeout: float = 0.3, *, ping_first: bool = False) -> bool:
    """探测 IP:port 是否可达

    Args:
        ip: 目标 IP
        port: 目标端口
        timeout: TCP connect 超时时间（秒）
        ping_first: 是否先 ping 预筛（默认关闭）。默认值必须是 False：
            息屏设备在 20ms 内常常不回 ICMP，开启后会把真实在线的手机
            直接判死，连 TCP 都不发。

    Returns:
        True 如果端口开放，False 如果不可达
    """
    if ping_first and not _ping_host(ip):
        return False
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout)
            sock.connect((ip, port))
        return True
    except Exception:
        return False


def scan_lan_for_adb(
    subnet: str,
    port: int,
    max_workers: int = 50,
    progress_cb: Callable[[str, int, int], None] | None = None,
    cancel_check: Callable[[], bool] | None = None,
    *,
    ping_first: bool = False,
) -> list[str]:
    """扫描局域网子网，返回端口开放的所有 IP

    Args:
        subnet: 子网前缀，如 '192.168.1.'
        port: ADB 端口，默认 5555
        max_workers: 并发线程数
        progress_cb: 进度回调 (message, current, total)
        ping_first: 是否先 ping 预筛（默认关闭）。开启后 ping 不通的 IP
            会连 TCP 都不发，而息屏设备在 20ms 内常常不回 ICMP，
            会稳定漏掉真实在线的手机，因此默认直接探测端口。

    Returns:
        开放该端口的 IP 列表，按末位数字排序
    """
    candidates = [f"{subnet}{i}" for i in range(1, 255)]
    total = len(candidates)
    found: list[str] = []
    completed = 0
    logger.info(f"正在扫描 {subnet}0/24 的 {port} 端口 ...")
    pool = ThreadPoolExecutor(max_workers=max_workers)
    cancelled = False
    futures = {}
    try:
        futures = {
            pool.submit(probe_port, ip, port, ping_first=ping_first): ip
            for ip in candidates
        }
        for future in as_completed(futures):
            if cancel_check is not None and cancel_check():
                cancelled = True
                break
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
    finally:
        if cancelled:
            for future in futures:
                future.cancel()
        pool.shutdown(wait=not cancelled, cancel_futures=cancelled)
    return sorted(found, key=lambda ip: int(ip.split(".")[-1]))


# ─── 设备发现：mDNS / ARP 邻居表 ─────────────────────────
# 逐 IP 探测 ADB 端口有两个固有问题：
# 1. 设备息屏时首个 ICMP/TCP 包可能被 WiFi 省电延迟，单次短超时会漏判；
# 2. Android 11+ 无线调试用的是随机端口，只探 5555 永远发现不了。
# 所以先用两条协议层通道拿到候选，再只对少量候选做 TCP 确认。

# Android 无线调试会广播的服务类型。_adb-tls-pairing._tcp 只用于配对，
# 直连没有意义，这里不取。
MDNS_ADB_SERVICES: tuple[str, ...] = ("_adb._tcp", "_adb-tls-connect._tcp")

# 触发 ARP 解析用的 UDP 端口。只是让内核为该 IP 发出 ARP 请求，
# 收到端口不可达也没关系；避开 5555 以免给真实 adbd 送无意义数据。
ARP_PROBE_PORT = 9

# TCP 确认的超时与重试：息屏设备的首个 SYN 常因省电延迟，1s + 一次重试才稳。
CONFIRM_TIMEOUT = 1.0
CONFIRM_RETRIES = 1

# 全段扫描一次给出超过这个数量的"开放端口"，基本只可能是透明代理或防火墙
# 接管了出站 TCP（TUN 代理会让整段 IP 的 connect 全部成功）。
SUSPICIOUS_TARGET_COUNT = 32

_MAC_RE = r"(?:[0-9a-fA-F]{2}[:-]){5}[0-9a-fA-F]{2}"
# IP 与 MAC 之间用 .*? 而不是 \D+：Linux 的 "dev eth0 lladdr" 里含数字，
# \D+ 会在此处断开，导致整段邻居表解析不出任何主机。
_NEIGHBOR_RE = re.compile(r"(\d{1,3}(?:\.\d{1,3}){3}).*?(" + _MAC_RE + r")")
_IPV4_PORT_RE = re.compile(r"(\d{1,3}(?:\.\d{1,3}){3}):(\d+)")


def mdns_discover_adb(
    adb: str,
    subnets: list[str] | None = None,
    timeout: float = 5,
) -> list[tuple[str, int]]:
    """通过 `adb mdns services` 发现正在广播的 ADB 端点。

    返回 [(ip, port), ...]，按出现顺序去重。能覆盖 Android 11+ 无线调试的
    随机端口（_adb-tls-connect._tcp），这是逐 IP 探测 5555 拿不到的信息。
    设备未开启无线调试、或 adb 不支持 mdns 时返回空列表，不抛异常。

    Args:
        adb: adb 可执行路径
        subnets: 只保留这些 /24 前缀内的结果；None 表示不过滤
        timeout: adb 命令超时
    """
    try:
        result = subprocess.run(
            [adb, "mdns", "services"],
            capture_output=True, timeout=timeout,
            **SUBPROCESS_TEXT, **SUBPROCESS_NO_WINDOW,
        )
        out = result.stdout or ""
    except Exception as exc:  # noqa: BLE001 - 发现失败只降级，不影响整体扫描
        logger.debug(f"[mDNS] 调用 adb mdns services 失败: {exc}")
        return []

    endpoints: list[tuple[str, int]] = []
    for line in out.splitlines():
        if not any(service in line for service in MDNS_ADB_SERVICES):
            continue
        match = _IPV4_PORT_RE.search(line)
        if not match:
            continue
        ip, port = match.group(1), int(match.group(2))
        if subnets is not None and _subnet_of(ip) not in subnets:
            continue
        if (ip, port) not in endpoints:
            endpoints.append((ip, port))
    return endpoints


def _send_arp_probes(
    subnet: str,
    cancel_check: Callable[[], bool] | None = None,
) -> None:
    """向子网内每个地址发一个无关的 UDP 包，触发内核 ARP 解析。

    UDP 不可达不会阻塞 sendto，/24 全量发送在毫秒级完成。这里不关心
    是否有响应，只要求真实在线的主机在邻居表里出现。
    """
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    except OSError as exc:
        logger.debug(f"[ARP] 创建 UDP 套接字失败: {exc}")
        return
    try:
        for i in range(1, 255):
            if cancel_check is not None and cancel_check():
                return
            try:
                sock.sendto(b"\x00", (f"{subnet}{i}", ARP_PROBE_PORT))
            except OSError:
                # ICMP 端口不可达可能让下一次 sendto 报 ECONNRESET，跳过即可
                continue
    finally:
        sock.close()


def _read_neighbor_table(timeout: float = 5) -> str:
    """读取系统邻居表原始输出（Windows arp / Linux ip neigh / macOS arp）"""
    if os.name == "nt":
        attempts = [(["arp", "-a"], SUBPROCESS_TEXT_OEM)]
    else:
        attempts = [
            (["ip", "neigh", "show"], SUBPROCESS_TEXT),
            (["arp", "-an"], SUBPROCESS_TEXT),
        ]
    for cmd, text_kwargs in attempts:
        try:
            result = subprocess.run(
                cmd, capture_output=True, timeout=timeout,
                **text_kwargs, **SUBPROCESS_NO_WINDOW,
            )
        except Exception:
            continue
        if (result.stdout or "").strip():
            return result.stdout
    return ""


def _parse_neighbor_table(
    output: str,
    subnets: list[str] | None = None,
) -> list[str]:
    """从 arp / ip neigh 输出中提取在线主机的 IPv4，按出现顺序去重。

    同时兼容三种格式：
    - Windows: `192.168.0.1  04-f9-f8-bb-47-ad  dynamic`
    - Linux:   `192.168.1.1 dev eth0 lladdr aa:bb:cc:dd:ee:ff REACHABLE`
    - macOS:   `? (192.168.1.1) at aa:bb:cc:dd:ee:ff on en0 ifscope [ethernet]`
    """
    hosts: list[str] = []
    for line in output.splitlines():
        upper = line.upper()
        # 未完成/失败的邻居条目没有可用 MAC，不能证明主机在线
        if "INCOMPLETE" in upper or "FAILED" in upper:
            continue
        match = _NEIGHBOR_RE.search(line)
        if not match:
            continue
        ip = match.group(1)
        mac = match.group(2).lower().replace("-", ":")
        if mac in {"ff:ff:ff:ff:ff:ff", "00:00:00:00:00:00"}:
            continue
        # 网络地址与广播地址不是主机
        if ip.endswith(".0") or ip.endswith(".255"):
            continue
        if subnets is not None and _subnet_of(ip) not in subnets:
            continue
        if ip not in hosts:
            hosts.append(ip)
    return hosts


def arp_scan_subnet(
    subnet: str,
    *,
    wait: float = 0.35,
    cancel_check: Callable[[], bool] | None = None,
) -> list[str]:
    """用 ARP 邻居表列出 /24 内在线的 IPv4 主机。

    不依赖 ICMP：设备不回 ping 也会因为 ARP 解析出现在邻居表里。
    只对同一广播域有效——跨 VLAN / 跨路由的网段邻居表里没有目标，
    调用方需要在结果为空时走逐 IP 的 TCP 兜底。
    """
    _send_arp_probes(subnet, cancel_check)
    if cancel_check is not None and cancel_check():
        return []
    # 留出 ARP 回复时间：局域网 ARP 往返在毫秒级，350ms 对休眠设备也够
    time.sleep(wait)
    return _parse_neighbor_table(_read_neighbor_table(), [subnet])


def confirm_endpoints(
    endpoints: list[tuple[str, int]],
    *,
    timeout: float = CONFIRM_TIMEOUT,
    retries: int = CONFIRM_RETRIES,
    progress_cb: Callable[[str, int, int], None] | None = None,
    base: int = 0,
    total: int | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> list[tuple[str, int]]:
    """对候选端点做 TCP 连确认，返回真正可连的端点。

    候选来自 mDNS/ARP，数量通常 <20，因此可以给足够宽松的超时与重试；
    被透明代理劫持的连接在多数情况下也能在这里被识别（拿不到对端）。
    """
    total = total if total is not None else base + len(endpoints)
    confirmed: list[tuple[str, int]] = []
    for i, (ip, port) in enumerate(endpoints, 1):
        if cancel_check is not None and cancel_check():
            return []
        if progress_cb:
            progress_cb(f"正在确认 {ip}:{port}...", base + i, total)
        ok = probe_port(ip, port, timeout=timeout, ping_first=False)
        for _ in range(retries):
            if ok:
                break
            ok = probe_port(ip, port, timeout=timeout, ping_first=False)
        if ok:
            confirmed.append((ip, port))
    return confirmed


def connect_wireless(adb: str, ip: str, port: int, timeout: float = 5) -> bool:
    """尝试 adb connect ip:port，返回是否成功"""
    target = f"{ip}:{port}"
    try:
        out = subprocess.run(
            [adb, "connect", target],
            capture_output=True, timeout=timeout,
        **SUBPROCESS_TEXT, **SUBPROCESS_NO_WINDOW,
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
                capture_output=True, timeout=5,
                **SUBPROCESS_TEXT, **SUBPROCESS_NO_WINDOW,
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
    cancel_check: Callable[[], bool] | None = None,
) -> list[dict]:
    """对一批 (ip, port) 依次 adb connect，返回连上的设备信息

    Args:
        base/total: 进度条基准，用于和前面的扫描阶段拼成连续进度
    """
    total = total if total is not None else base + len(targets)
    devices: list[dict] = []
    for i, (ip, port) in enumerate(targets, 1):
        if cancel_check is not None and cancel_check():
            break
        current = base + i
        if progress_cb:
            progress_cb(f"正在连接 {ip}:{port}...", current, total)
        if not connect_wireless(adb, ip, port):
            logger.debug(f"{ip}:{port} 连接失败（端口开放但非 ADB 服务）")
            continue
        for _ in range(10):
            if cancel_check is not None and cancel_check():
                return devices
            time.sleep(0.05)
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


def _dedupe_devices_by_ip(devices: list[dict]) -> list[dict]:
    """同一台设备可能同时连上默认端口与 mDNS 随机端口，按 IP 只保留第一条。

    调用方保证默认端口排在候选前面，因此优先留下的是 ip:5555，
    不会让下拉框出现同一台机器的两条记录。
    """
    result: list[dict] = []
    seen: set[str] = set()
    for device in devices:
        serial = str(device.get("serial", ""))
        ip = serial.rpartition(":")[0] or serial
        if ip in seen:
            continue
        seen.add(ip)
        result.append(device)
    return result


def scan_and_connect_wireless(
    port: int = 5555,
    progress_cb: Callable[[str, int, int], None] | None = None,
    subnets: list[str] | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> list[dict]:
    """发现局域网内的 ADB 设备并尝试连接

    网段选择（全部网卡 / 指定网卡）只决定 ARP 和 TCP 兜底的作用范围：
    mDNS 结果按同一网段过滤，ARP 逐段读邻居表，两条通道都没有候选时才
    逐 IP 扫端口（跨 VLAN 场景的最后手段）。

    流程：
    1. 确定待扫描网段（未指定时取本机所有网卡的网段）
    2. mDNS 读设备广播的 IP:端口（含 Android 11+ 的随机端口）
    3. ARP 邻居表列出各网段在线主机，按默认端口加入候选
    4. 对候选做 TCP 确认；候选为空才回退到逐 IP 扫描
    5. 对确认的端点执行 adb connect 并取回设备信息

    Args:
        port: ADB 端口，默认 5555（mDNS 结果使用广播里的端口）
        progress_cb: 进度回调 (message, current, total)
        subnets: 指定网段前缀列表（如 ['192.168.1.']）；None 表示扫描全部网卡。
                 多网卡（VMware/WSL/VPN）场景下只扫单一网段会漏掉目标设备，
                 因此默认逐个网段处理。

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

    def report(message: str, current: int, total: int = scan_total) -> None:
        if progress_cb:
            progress_cb(message, current, total)

    # 阶段 1：mDNS（不扫网段，直接读设备广播；随机端口只能从这条路拿到）
    report("正在通过 mDNS 查找无线调试设备...", 0)
    mdns_endpoints = mdns_discover_adb(adb, targets_subnets)
    logger.info(f"[mDNS] 发现 {len(mdns_endpoints)} 个 ADB 端点")

    # 候选按 (ip, port) 去重。同一台设备常同时广播 legacy(5555) 与 TLS 随机端口，
    # 两个都要试：设备可能只广播其中一个，而另一个仍然可连。默认端口排前面，
    # 结果按 IP 去重时才会优先留下用户预期的 ip:5555。
    candidates: list[tuple[str, int]] = []
    seen_endpoints: set[tuple[str, int]] = set()

    def add_endpoint(ip: str, endpoint_port: int) -> None:
        key = (ip, endpoint_port)
        if key in seen_endpoints:
            return
        seen_endpoints.add(key)
        candidates.append(key)

    for ip, mdns_port in mdns_endpoints:
        add_endpoint(ip, port)
        add_endpoint(ip, mdns_port)
    if cancel_check is not None and cancel_check():
        return []

    # 阶段 2：ARP 邻居表（补齐没有广播 mDNS 的设备；不依赖 ICMP 存活）
    for subnet in targets_subnets:
        if cancel_check is not None and cancel_check():
            return []
        # ARP 阶段只换消息不推进进度：是否回退到逐 IP 扫描要到这一步之后才知道，
        # 先推进再回退会让进度条倒退。
        report(f"正在通过 ARP 扫描 {subnet}0/24 ...", 0)
        hosts = arp_scan_subnet(subnet, cancel_check=cancel_check)
        logger.info(f"[ARP] {subnet}0/24 在线主机 {len(hosts)} 台")
        for ip in hosts:
            add_endpoint(ip, port)

    if cancel_check is not None and cancel_check():
        return []

    if candidates:
        # 阶段 3：只对少量候选做 TCP 确认，宽松超时 + 重试
        planned = len(candidates)
        candidates = confirm_endpoints(
            candidates,
            progress_cb=progress_cb,
            base=scan_total,
            total=scan_total + planned,
            cancel_check=cancel_check,
        )
        if cancel_check is not None and cancel_check():
            return []
        connect_base = scan_total + planned
        connect_total = scan_total + 2 * planned
    else:
        # 阶段 4：兜底逐 IP 扫描。跨 VLAN / 跨路由时邻居表看不到设备，
        # 只能靠这一步；此时 ping 预筛保持关闭，避免再次漏掉息屏设备。
        logger.info("mDNS 与 ARP 均未发现候选，回退到逐 IP 扫描")
        for idx, subnet in enumerate(targets_subnets):
            if cancel_check is not None and cancel_check():
                return []
            offset = per_subnet * idx
            report(f"正在扫描 {subnet}0/24 ...", offset)

            def sub_progress(message: str, current: int, _total: int, _offset: int = offset):
                report(message, _offset + current)

            for ip in scan_lan_for_adb(
                subnet,
                port,
                progress_cb=sub_progress if progress_cb else None,
                cancel_check=cancel_check,
            ):
                if (ip, port) not in candidates:
                    candidates.append((ip, port))
            if cancel_check is not None and cancel_check():
                return []

        if len(candidates) > SUSPICIOUS_TARGET_COUNT:
            logger.warning(
                f"[扫描] 一次发现 {len(candidates)} 个开放端口，疑似出站 TCP 被代理/"
                "防火墙接管；若正在使用 TUN/VPN，请把局域网网段设为直连")
        connect_base = scan_total
        connect_total = scan_total + len(candidates)

    if not candidates:
        logger.info(f"局域网内未发现端口 {port} 开放的设备")
        return []

    logger.info(f"发现 {len(candidates)} 个 ADB 端点，尝试 adb connect")
    devices = _connect_targets(
        adb, candidates, progress_cb,
        base=connect_base, total=connect_total,
        cancel_check=cancel_check,
    )
    return _dedupe_devices_by_ip(devices)


def scan_and_connect_local(
    ports: tuple[int, ...] | list[int] | None = None,
    progress_cb: Callable[[str, int, int], None] | None = None,
    cancel_check: Callable[[], bool] | None = None,
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
        if cancel_check is not None and cancel_check():
            return []
        if progress_cb:
            progress_cb(f"正在探测 {ip}:{p}...", i, total)
        if probe_port(ip, p, timeout=0.2, ping_first=False):
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
        cancel_check=cancel_check,
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
