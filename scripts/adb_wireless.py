"""自动扫描局域网内的安卓设备，通过 ADB 无线连接

流程：
1. 列出当前 USB 连接的设备，获取 IP 并开启 TCP 模式，切换为无线连接
2. 扫描局域网，尝试 adb connect 固定端口，发现已开启无线调试的设备
3. 汇总所有已连接设备

用法：
    python scripts/adb_wireless.py          # 默认端口 5555
    python scripts/adb_wireless.py 5555     # 指定端口
"""

import os
import sys
import time

# src-layout：仓库根不是可导入路径，需要把 src/ 加入 sys.path
_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_src = os.path.join(_root, "src")
if _src not in sys.path:
    sys.path.insert(0, _src)

# 仓库裸检出场景必须先注入 src/，因此这两条导入有意位于 bootstrap 之后。
from lvjiang.core.android.device import list_adb_devices  # noqa: E402
from lvjiang.core.android.wireless import (  # noqa: E402
    connect_wireless,
    enable_tcpip,
    get_adb_device_info,
    get_device_ip,
    list_ipv4_interfaces,
    list_scan_subnets,
    resolve_adb,
    scan_lan_for_adb,
)


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 5555
    adb = resolve_adb()
    print(f"ADB 路径: {adb}")
    print(f"目标端口: {port}")
    print()

    connected_ips: set[str] = set()  # 已成功连接的 IP，避免重复

    # ── 阶段 1：处理 USB 设备 ──
    usb_devices = list_adb_devices(adb)
    if usb_devices:
        print(f"发现 {len(usb_devices)} 台 USB 设备：")
        for i, d in enumerate(usb_devices):
            print(f"  [{i}] {d['serial']}  model={d['model']}")
        print()

        for d in usb_devices:
            serial = d["serial"]
            # 跳过已经是网络连接的设备（含冒号说明是 ip:port）
            if ":" in serial:
                continue
            print(f"── 处理设备 {serial} ──")

            ip = get_device_ip(adb, serial)
            if not ip:
                print("  [失败] 无法获取设备 IP，跳过")
                continue
            print(f"  设备 IP: {ip}")

            if not enable_tcpip(adb, serial, port):
                print("  [失败] 无法开启 tcpip 模式，跳过")
                continue
            print(f"  已开启 ADB TCP 模式 (端口 {port})")

            print("  等待设备 ADB daemon 重启...", end="", flush=True)
            time.sleep(2)
            print(" 完成")

            print(f"  正在连接 {ip}:{port} ...", end="", flush=True)
            if connect_wireless(adb, ip, port):
                time.sleep(1)
                info = get_adb_device_info(adb, ip, port)
                if info:
                    print(f" 连接成功！model={info['model']}")
                    connected_ips.add(ip)
                else:
                    print(" 连接后未识别设备信息")
                    connected_ips.add(ip)
            else:
                print(" 连接失败")
            print()
    else:
        print("未发现 USB 连接的设备，将直接扫描局域网。")
        print()

    # ── 阶段 2：局域网扫描（逐网卡，避免多网卡只扫到虚拟网段）──
    for iface in list_ipv4_interfaces():
        print(f"本机网卡: {iface.label}")
    subnets = list_scan_subnets()
    if not subnets:
        print("[警告] 无法获取本机局域网地址，跳过局域网扫描。")
    for subnet in subnets:
        print(f"\n扫描地址段: {subnet}0/24")
        open_ips = scan_lan_for_adb(subnet, port)

        if open_ips:
            print(f"\n发现 {len(open_ips)} 个 IP 开放端口 {port}，尝试 adb connect：")
            for ip in open_ips:
                if ip in connected_ips:
                    print(f"  {ip} — 已在阶段1连接，跳过")
                    continue
                target = f"{ip}:{port}"
                print(f"  连接 {target} ...", end="", flush=True)
                if connect_wireless(adb, ip, port):
                    time.sleep(0.5)
                    info = get_adb_device_info(adb, ip, port)
                    if info:
                        print(f" 成功！ model={info['model']}")
                        connected_ips.add(ip)
                    else:
                        print(" 已连接但未识别设备信息")
                        connected_ips.add(ip)
                else:
                    print(" 失败（端口开放但非 ADB 服务）")
        else:
            print(f"  {subnet}0/24 内未发现端口 {port} 开放的设备。")

    if not connected_ips:
        print("\n提示：设备需先开启无线调试（USB 连接时运行此脚本，或在开发者选项中启用）。")
        print("      模拟器只监听 127.0.0.1，请在 UI 里用「本地扫描」发现。")

    # ── 汇总 ──
    print()
    print("=" * 40)
    if connected_ips:
        print(f"共连接 {len(connected_ips)} 台无线设备。")
    else:
        print("未发现可连接的设备。")
        print("请确认：设备与电脑在同一局域网，且已开启 ADB 无线调试。")
    print("\n当前已连接设备：")
    from lvjiang.core.android.wireless import run_adb
    print(run_adb(adb, "devices", "-l"))


if __name__ == "__main__":
    main()
