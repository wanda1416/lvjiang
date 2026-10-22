"""多网卡扫描 + 本机模拟器扫描（core/android/wireless.py）测试

没有真机也没有真网卡：把 subprocess 输出、probe_port、adb connect 全部打桩，
覆盖多网卡环境下真正会翻车的那几处：
- ipconfig / ip -o addr / ifconfig 三种输出的网卡+IPv4 解析
- 虚拟网卡（非 RFC1918）仍列出但排在私有段之后
- 默认扫全部网卡的网段，指定网段时只扫指定的
- 本机端口扫描发现模拟器
"""
from __future__ import annotations

import pytest

from lvjiang.core.android import device as device_module
from lvjiang.core.android import wireless as w

IPCONFIG_OUT = """
Windows IP 配置

以太网适配器 以太网:

   连接特定的 DNS 后缀 . . . . . . . :
   IPv4 地址 . . . . . . . . . . . . : 192.168.1.23
   子网掩码  . . . . . . . . . . . . : 255.255.255.0

以太网适配器 VMware Network Adapter VMnet8:

   IPv4 地址 . . . . . . . . . . . . : 172.30.5.1
   子网掩码  . . . . . . . . . . . . : 255.255.255.0

未知适配器 本地连接:

   IPv4 地址 . . . . . . . . . . . . : 198.18.0.1
"""

IP_ADDR_OUT = """1: lo    inet 127.0.0.1/8 scope host lo\\       valid_lft forever
2: eth0    inet 192.168.1.23/24 brd 192.168.1.255 scope global eth0\\       valid_lft forever
3: docker0    inet 172.17.0.1/16 brd 172.17.255.255 scope global docker0\\       valid_lft forever
"""

IFCONFIG_OUT = """en0: flags=8863<UP,BROADCAST,SMART,RUNNING> mtu 1500
\tinet 192.168.1.23 netmask 0xffffff00 broadcast 192.168.1.255
lo0: flags=8049<UP,LOOPBACK,RUNNING> mtu 16384
\tinet 127.0.0.1 netmask 0xff000000
"""


class _Completed:
    def __init__(self, stdout: str):
        self.stdout = stdout


def _fake_run(out_by_cmd: dict[str, str]):
    def runner(cmd, *a, **kw):
        return _Completed(out_by_cmd.get(cmd[0], ""))
    return runner


def test_windows_interfaces_parsed_with_names(monkeypatch):
    monkeypatch.setattr(w.os, "name", "nt")
    monkeypatch.setattr(w.subprocess, "run", _fake_run({"ipconfig": IPCONFIG_OUT}))
    ifaces = w.list_ipv4_interfaces()
    assert [(i.name, i.ip, i.subnet) for i in ifaces] == [
        ("以太网适配器 以太网", "192.168.1.23", "192.168.1."),
        ("以太网适配器 VMware Network Adapter VMnet8", "172.30.5.1", "172.30.5."),
        ("未知适配器 本地连接", "198.18.0.1", "198.18.0."),
    ]
    # 198.18 是 VPN/代理常用的虚拟网段，不算私有，但仍要列出来供用户选，且排在最后
    assert [i.private for i in ifaces] == [True, True, False]


def test_linux_interfaces_drop_loopback(monkeypatch):
    monkeypatch.setattr(w.os, "name", "posix")
    monkeypatch.setattr(w.subprocess, "run", _fake_run({"ip": IP_ADDR_OUT}))
    ifaces = w.list_ipv4_interfaces()
    assert [(i.name, i.ip) for i in ifaces] == [
        ("eth0", "192.168.1.23"),
        ("docker0", "172.17.0.1"),
    ]
    assert w.list_scan_subnets() == ["192.168.1.", "172.17.0."]


def test_macos_ifconfig_fallback(monkeypatch):
    monkeypatch.setattr(w.os, "name", "posix")
    monkeypatch.setattr(w.subprocess, "run", _fake_run({"ifconfig": IFCONFIG_OUT}))
    ifaces = w.list_ipv4_interfaces()
    assert [(i.name, i.ip) for i in ifaces] == [("en0", "192.168.1.23")]


def test_scan_subnets_fall_back_to_single_subnet(monkeypatch):
    monkeypatch.setattr(w, "list_ipv4_interfaces", lambda *a, **k: [])
    monkeypatch.setattr(w, "get_local_subnet", lambda: "10.0.0.")
    assert w.list_scan_subnets() == ["10.0.0."]


@pytest.fixture
def _stub_connect(monkeypatch):
    """adb connect 一律成功，设备信息按 serial 造；协议层发现默认空手而归"""
    monkeypatch.setattr(w, "resolve_adb", lambda: "adb")
    monkeypatch.setattr(w, "connect_wireless", lambda adb, ip, port, **k: True)
    monkeypatch.setattr(w, "get_adb_device_info",
                        lambda adb, ip, port: {"serial": f"{ip}:{port}", "model": "stub"})
    monkeypatch.setattr(w.time, "sleep", lambda *_: None)
    monkeypatch.setattr(w, "mdns_discover_adb", lambda *a, **k: [])
    monkeypatch.setattr(w, "arp_scan_subnet", lambda *a, **k: [])


MDNS_OUT = """List of discovered mdns services
adb-10AF1L019G0031K-fcBfHa\t_adb-tls-connect._tcp\t192.168.0.101:33973
adb-10AF1L019G0031K\t_adb._tcp\t192.168.0.101:5555
adb-other\t_adb-tls-pairing._tcp\t192.168.0.102:37123
"""

ARP_WINDOWS_OUT = """Interface: 192.168.0.102 --- 0x2
  Internet Address      Physical Address      Type
  192.168.0.1           04-f9-f8-bb-47-ad     dynamic
  192.168.0.101         40-45-a0-3c-f7-0d     dynamic
  192.168.0.255         ff-ff-ff-ff-ff-ff     static
"""

ARP_LINUX_OUT = """192.168.1.1 dev eth0 lladdr aa:bb:cc:dd:ee:ff REACHABLE
192.168.1.9 dev eth0  FAILED
"""

ARP_MACOS_OUT = """? (192.168.1.23) at 40:45:a0:3c:f7:0d on en0 ifscope [ethernet]
? (192.168.1.30) at (incomplete) on en0 ifscope [ethernet]
"""


def test_mdns_discovery_returns_random_port_endpoints(monkeypatch):
    """Android 11+ 无线调试用随机端口，只探 5555 永远发现不了，必须读 mDNS 广播"""
    monkeypatch.setattr(w.subprocess, "run", lambda *a, **k: _Completed(MDNS_OUT))
    assert w.mdns_discover_adb("adb") == [
        ("192.168.0.101", 33973),
        ("192.168.0.101", 5555),
    ]
    # 限定某个网卡时，其他网段的广播结果必须丢弃
    assert w.mdns_discover_adb("adb", ["10.0.0."]) == []


def test_arp_neighbor_table_handles_three_platform_formats():
    """ARP 发现不依赖 ICMP：三种 arp / ip neigh 输出都要能解析出在线主机"""
    assert w._parse_neighbor_table(ARP_WINDOWS_OUT) == ["192.168.0.1", "192.168.0.101"]
    assert w._parse_neighbor_table(ARP_LINUX_OUT) == ["192.168.1.1"]
    assert w._parse_neighbor_table(ARP_MACOS_OUT) == ["192.168.1.23"]


def test_wireless_scan_uses_mdns_without_full_sweep(monkeypatch, _stub_connect):
    """mDNS 已有候选时不能再全段扫 254 个 IP —— 那是代理劫持下假设备的入口"""
    monkeypatch.setattr(w, "list_scan_subnets", lambda: ["192.168.0."])
    monkeypatch.setattr(w, "mdns_discover_adb",
                        lambda adb, subnets=None: [("192.168.0.101", 5555)])
    monkeypatch.setattr(w, "confirm_endpoints", lambda eps, **k: eps)
    monkeypatch.setattr(w, "scan_lan_for_adb",
                        lambda *a, **k: pytest.fail("有 mDNS 候选时不应全段扫描"))
    assert w.scan_and_connect_wireless() == [
        {"serial": "192.168.0.101:5555", "model": "stub"}]


def test_wireless_scan_dedupes_same_device_mdns_services(monkeypatch, _stub_connect):
    """同一台手机同时广播 5555 与 TLS 随机端口，不能变成两条设备记录"""
    monkeypatch.setattr(w, "list_scan_subnets", lambda: ["192.168.0."])
    monkeypatch.setattr(w, "mdns_discover_adb", lambda *a, **k: [
        ("192.168.0.101", 33973),
        ("192.168.0.101", 5555),
    ])
    confirmed: list[tuple[str, int]] = []

    def fake_confirm(endpoints, **kwargs):
        confirmed.extend(endpoints)
        return endpoints

    monkeypatch.setattr(w, "confirm_endpoints", fake_confirm)
    devices = w.scan_and_connect_wireless(port=5555)
    # 两个端口都要尝试，但默认端口排在前面
    assert confirmed == [("192.168.0.101", 5555), ("192.168.0.101", 33973)]
    assert devices == [{"serial": "192.168.0.101:5555", "model": "stub"}]


def test_wireless_scan_also_tries_default_port_for_mdns_host(monkeypatch, _stub_connect):
    """设备只广播 TLS 随机端口时，不能因此放弃仍可用的默认 5555"""
    monkeypatch.setattr(w, "list_scan_subnets", lambda: ["192.168.0."])
    monkeypatch.setattr(w, "mdns_discover_adb",
                        lambda *a, **k: [("192.168.0.101", 33973)])
    seen: list[tuple[str, int]] = []

    def fake_confirm(endpoints, **kwargs):
        seen.extend(endpoints)
        return endpoints

    monkeypatch.setattr(w, "confirm_endpoints", fake_confirm)
    w.scan_and_connect_wireless(port=5555)
    assert seen == [("192.168.0.101", 5555), ("192.168.0.101", 33973)]


def test_wireless_scan_probes_arp_hosts_on_default_port(monkeypatch, _stub_connect):
    """没有 mDNS 广播的设备靠 ARP 发现，并按默认端口进入 TCP 确认"""
    monkeypatch.setattr(w, "list_scan_subnets", lambda: ["192.168.1."])
    monkeypatch.setattr(w, "arp_scan_subnet", lambda *a, **k: ["192.168.1.77"])
    confirmed: list[tuple[str, int]] = []

    def fake_confirm(endpoints, **kwargs):
        confirmed.extend(endpoints)
        return endpoints

    monkeypatch.setattr(w, "confirm_endpoints", fake_confirm)
    assert w.scan_and_connect_wireless(port=5555) == [
        {"serial": "192.168.1.77:5555", "model": "stub"}]
    assert confirmed == [("192.168.1.77", 5555)]


def test_confirm_endpoints_drops_unreachable_candidates(monkeypatch):
    """被代理劫持或已离线的候选不能进入 adb connect，且确认阶段不再 ping 预筛"""
    calls: list[tuple[str, int, bool]] = []

    def fake_probe(ip, port, timeout=0.3, ping_first=True):
        calls.append((ip, port, ping_first))
        return ip == "192.168.1.77"

    monkeypatch.setattr(w, "probe_port", fake_probe)
    assert w.confirm_endpoints(
        [("192.168.1.77", 5555), ("192.168.1.78", 5555)]) == [("192.168.1.77", 5555)]
    assert all(ping_first is False for _, _, ping_first in calls)


def test_wireless_scan_covers_every_adapter_subnet(monkeypatch, _stub_connect):
    """默认不指定网段时，每个网卡的网段都要扫到 —— 多网卡漏扫正是本次要修的问题"""
    monkeypatch.setattr(w, "list_scan_subnets", lambda: ["192.168.1.", "172.17.0."])
    scanned: list[str] = []

    def fake_scan(subnet, port, **kw):
        scanned.append(subnet)
        return [f"{subnet}77"] if subnet == "172.17.0." else []

    monkeypatch.setattr(w, "scan_lan_for_adb", fake_scan)
    devices = w.scan_and_connect_wireless(port=5555)
    assert scanned == ["192.168.1.", "172.17.0."]
    assert devices == [{"serial": "172.17.0.77:5555", "model": "stub"}]


def test_wireless_scan_honours_explicit_subnet(monkeypatch, _stub_connect):
    monkeypatch.setattr(w, "list_scan_subnets", lambda: ["192.168.1.", "172.17.0."])
    scanned: list[str] = []
    monkeypatch.setattr(w, "scan_lan_for_adb",
                        lambda subnet, port, **kw: scanned.append(subnet) or [])
    assert w.scan_and_connect_wireless(subnets=["172.17.0."]) == []
    assert scanned == ["172.17.0."]


def test_wireless_scan_progress_is_monotonic(monkeypatch, _stub_connect):
    """两个网段的进度要接着往前走，不能扫完第一段回退到 0"""
    monkeypatch.setattr(w, "list_scan_subnets", lambda: ["192.168.1.", "172.17.0."])

    def fake_scan(subnet, port, progress_cb=None, **kw):
        if progress_cb:
            progress_cb("扫描中", 10, 254)
            progress_cb("扫描中", 254, 254)
        return [f"{subnet}5"]

    monkeypatch.setattr(w, "scan_lan_for_adb", fake_scan)
    seen: list[tuple[int, int]] = []
    w.scan_and_connect_wireless(progress_cb=lambda m, c, t: seen.append((c, t)))
    assert [c for c, _ in seen] == sorted(c for c, _ in seen)
    assert seen[-1][0] <= seen[-1][1]


def test_local_scan_finds_emulator_ports(monkeypatch, _stub_connect):
    probed: list[tuple[str, int]] = []

    def fake_probe(ip, port, timeout=0.3, ping_first=True):
        probed.append((ip, port))
        return port == 7555

    monkeypatch.setattr(w, "probe_port", fake_probe)
    devices = w.scan_and_connect_local()
    assert {ip for ip, _ in probed} == {"127.0.0.1"}
    assert [p for _, p in probed] == list(w.LOCAL_ADB_PORTS)
    assert devices == [{"serial": "127.0.0.1:7555", "model": "stub"}]


def test_local_scan_returns_empty_when_nothing_open(monkeypatch, _stub_connect):
    monkeypatch.setattr(w, "probe_port", lambda *a, **k: False)
    assert w.scan_and_connect_local(ports=[5555]) == []


def test_wireless_scan_stops_before_next_subnet_when_cancelled(
    monkeypatch, _stub_connect,
):
    monkeypatch.setattr(w, "list_scan_subnets", lambda: ["192.168.1.", "10.0.0."])
    scanned: list[str] = []
    cancelled = False

    def fake_scan(subnet, port, **kwargs):
        nonlocal cancelled
        scanned.append(subnet)
        cancelled = True
        return []

    monkeypatch.setattr(w, "scan_lan_for_adb", fake_scan)

    assert w.scan_and_connect_wireless(
        cancel_check=lambda: cancelled,
    ) == []
    assert scanned == ["192.168.1."]


def test_local_scan_stops_between_ports_when_cancelled(monkeypatch, _stub_connect):
    probed: list[int] = []
    cancelled = False

    def fake_probe(ip, port, timeout=0.3, ping_first=True):
        nonlocal cancelled
        probed.append(port)
        cancelled = True
        return False

    monkeypatch.setattr(w, "probe_port", fake_probe)

    assert w.scan_and_connect_local(
        ports=[5555, 7555],
        cancel_check=lambda: cancelled,
    ) == []
    assert probed == [5555]


def test_lan_scan_cancellation_does_not_report_partial_results(monkeypatch):
    cancelled = False

    def fake_probe(ip, port, timeout=0.3, ping_first=True):
        nonlocal cancelled
        cancelled = True
        return ip.endswith(".1")

    monkeypatch.setattr(w, "probe_port", fake_probe)

    assert w.scan_lan_for_adb(
        "192.168.1.",
        5555,
        max_workers=1,
        cancel_check=lambda: cancelled,
    ) == []


def test_usb_adb_scan_terminates_process_when_cancelled(monkeypatch):
    class Process:
        returncode = None
        terminated = False

        def poll(self):
            return self.returncode

        def terminate(self):
            self.terminated = True
            self.returncode = 1

        def kill(self):
            self.returncode = 1

        def communicate(self, timeout=None):
            return "", ""

    process = Process()
    monkeypatch.setattr(device_module.subprocess, "Popen", lambda *a, **k: process)

    assert device_module.list_adb_devices(
        "adb",
        cancel_check=lambda: True,
    ) == []
    assert process.terminated is True


def test_adb_shell_normalizes_missing_process_output(monkeypatch):
    """空 stdout/stderr 仍须履行 shell() 返回字符串的契约。"""
    class Result:
        returncode = 1
        stdout = None
        stderr = None

    monkeypatch.setattr(
        device_module.subprocess, "run", lambda *args, **kwargs: Result())

    assert device_module.AdbDevice("serial").shell("dumpsys", "activity") == ""
