"""自动扫描局域网内的安卓设备，通过 ADB 无线连接

流程：
1. 列出当前 USB 连接的设备，获取 IP 并开启 TCP 模式，切换为无线连接
2. 扫描局域网，尝试 adb connect 固定端口，发现已开启无线调试的设备
3. 汇总所有已连接设备

用法：
    python scripts/adb_wireless.py          # 默认端口 5555
    python scripts/adb_wireless.py 5555     # 指定端口
"""

import shutil
import socket
import subprocess
import sys
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed


ADB_PORT = 5555


def resolve_adb() -> str:
    """解析 adb 可执行路径"""
    found = shutil.which("adb")
    if found:
        return found
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


def run_adb(adb: str, *args: str, timeout: float = 10) -> str:
    """执行 adb 命令，返回 stdout"""
    r = subprocess.run(
        [adb, *args],
        capture_output=True, text=True, timeout=timeout,
    )
    return r.stdout.strip()


def list_usb_devices(adb: str) -> list[dict]:
    """列出当前 USB 连接的设备（排除已经是网络连接的）"""
    out = run_adb(adb, "devices", "-l")
    devices = []
    for line in out.splitlines()[1:]:
        line = line.strip()
        if not line or "device" not in line.split():
            continue
        parts = line.split()
        serial = parts[0]
        # 跳过已经是网络连接的设备（含冒号说明是 ip:port）
        if ":" in serial:
            continue
        model = ""
        m = re.search(r"model:(\S+)", line)
        if m:
            model = m.group(1)
        transport = ""
        m2 = re.search(r"transport_id:(\S+)", line)
        if m2:
            transport = m2.group(1)
        devices.append({"serial": serial, "model": model, "transport_id": transport})
    return devices


def get_device_ip(adb: str, serial: str) -> str | None:
    """通过 adb shell 获取设备局域网 IP"""
    # 优先尝试 ip route（Android 较新版本）
    for cmd_args in [
        ["shell", "ip", "-4", "addr", "show", "wlan0"],
        ["shell", "ifconfig", "wlan0"],
    ]:
        try:
            out = subprocess.run(
                [adb, "-s", serial, *cmd_args],
                capture_output=True, text=True, timeout=5,
            ).stdout
            m = re.search(r"inet\s+(\d+\.\d+\.\d+\.\d+)", out)
            if m:
                return m.group(1)
        except Exception:
            continue
    return None


def enable_tcpip(adb: str, serial: str, port: int) -> bool:
    """开启设备的 ADB TCP 模式"""
    out = run_adb(adb, "-s", serial, "tcpip", str(port))
    return "restarting" in out.lower() or port in out


def connect_wireless(adb: str, ip: str, port: int, timeout: float = 5) -> bool:
    """尝试 adb connect ip:port"""
    target = f"{ip}:{port}"
    try:
        out = subprocess.run(
            [adb, "connect", target],
            capture_output=True, text=True, timeout=timeout,
        ).stdout.strip()
        return "connected" in out.lower()
    except Exception:
        return False


def disconnect_usb(adb: str, serial: str) -> None:
    """断开 USB 设备（disconnect）"""
    run_adb(adb, "disconnect", serial)


def verify_wireless(adb: str, ip: str, port: int) -> bool:
    """验证无线连接是否在线"""
    out = run_adb(adb, "devices", "-l")
    target = f"{ip}:{port}"
    for line in out.splitlines():
        if target in line and "device" in line.split():
            return True
    return False


def get_local_subnet() -> str | None:
    """获取本机局域网子网前缀（如 '192.168.1.'）"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
        s.close()
        parts = local_ip.split(".")
        if len(parts) == 4:
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


def scan_lan_for_adb(subnet: str, port: int, max_workers: int = 50) -> list[str]:
    """扫描局域网子网，返回端口开放的所有 IP"""
    candidates = [f"{subnet}{i}" for i in range(1, 255)]
    found: list[str] = []
    print(f"  正在扫描 {subnet}0/24 的 {port} 端口 ...")
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(probe_port, ip, port): ip for ip in candidates}
        for future in as_completed(futures):
            if future.result():
                ip = futures[future]
                found.append(ip)
                print(f"    发现 {ip}:{port} 开放")
    return sorted(found, key=lambda ip: int(ip.split(".")[-1]))


def get_adb_device_info(adb: str, ip: str, port: int) -> dict | None:
    """adb connect 后获取设备信息"""
    target = f"{ip}:{port}"
    # 从 adb devices -l 中解析
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


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else ADB_PORT
    adb = resolve_adb()
    print(f"ADB 路径: {adb}")
    print(f"目标端口: {port}")
    print()

    connected_ips: set[str] = set()  # 已成功连接的 IP，避免重复

    # ── 阶段 1：处理 USB 设备 ──
    usb_devices = list_usb_devices(adb)
    if usb_devices:
        print(f"发现 {len(usb_devices)} 台 USB 设备：")
        for i, d in enumerate(usb_devices):
            print(f"  [{i}] {d['serial']}  model={d['model']}")
        print()

        for d in usb_devices:
            serial = d["serial"]
            print(f"── 处理设备 {serial} ──")

            ip = get_device_ip(adb, serial)
            if not ip:
                print(f"  [失败] 无法获取设备 IP，跳过")
                continue
            print(f"  设备 IP: {ip}")

            if not enable_tcpip(adb, serial, port):
                print(f"  [失败] 无法开启 tcpip 模式，跳过")
                continue
            print(f"  已开启 ADB TCP 模式 (端口 {port})")

            print("  等待设备 ADB daemon 重启...", end="", flush=True)
            time.sleep(2)
            print(" 完成")

            print(f"  正在连接 {ip}:{port} ...", end="", flush=True)
            if connect_wireless(adb, ip, port):
                time.sleep(1)
                if verify_wireless(adb, ip, port):
                    print(f" 连接成功！可以拔掉 USB 线了。")
                    connected_ips.add(ip)
                else:
                    print(f" 连接后验证失败")
            else:
                print(f" 连接失败")
            print()
    else:
        print("未发现 USB 连接的设备，将直接扫描局域网。")
        print()

    # ── 阶段 2：局域网扫描 ──
    subnet = get_local_subnet()
    if not subnet:
        print("[警告] 无法获取本机局域网地址，跳过局域网扫描。")
    else:
        print(f"本机局域网地址段: {subnet}0/24")
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
                        print(f" 已连接但未识别设备信息")
                        connected_ips.add(ip)
                else:
                    print(f" 失败（端口开放但非 ADB 服务）")
        else:
            print(f"\n局域网内未发现端口 {port} 开放的设备。")
            print("提示：设备需先开启无线调试（USB 连接时运行此脚本，或在开发者选项中启用）。")

    # ── 汇总 ──
    print()
    print("=" * 40)
    if connected_ips:
        print(f"共连接 {len(connected_ips)} 台无线设备。")
    else:
        print("未发现可连接的设备。")
        print("请确认：设备与电脑在同一局域网，且已开启 ADB 无线调试。")
    print("\n当前已连接设备：")
    print(run_adb(adb, "devices", "-l"))


if __name__ == "__main__":
    main()
