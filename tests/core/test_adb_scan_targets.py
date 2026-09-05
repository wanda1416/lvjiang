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
    """adb connect 一律成功，设备信息按 serial 造"""
    monkeypatch.setattr(w, "resolve_adb", lambda: "adb")
    monkeypatch.setattr(w, "connect_wireless", lambda adb, ip, port, **k: True)
    monkeypatch.setattr(w, "get_adb_device_info",
                        lambda adb, ip, port: {"serial": f"{ip}:{port}", "model": "stub"})
    monkeypatch.setattr(w.time, "sleep", lambda *_: None)


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

    def fake_probe(ip, port, timeout=0.3):
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
