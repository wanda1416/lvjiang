"""ADB minicap 截图联调脚本（需真机）

连接第一台 adb 设备，push minicap，启动流并截一帧存盘。

用法: python scripts/manual-tests/test_adb_capture.py [输出路径]
默认输出: adb_capture.png（当前目录）
"""

import sys
from pathlib import Path

# 项目根目录（scripts/manual-tests/ → 上两级）
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from lvjiang.core.adb import AdbDevice, list_adb_devices
from lvjiang.core.adb.bootstrap import bootstrap_minicap, BootstrapError
from lvjiang.core.adb.capture import AdbCapture


def main():
    out_path = sys.argv[1] if len(sys.argv) > 1 else "adb_capture.png"

    devices = list_adb_devices()
    if not devices:
        print("未找到 ADB 设备，请确认已连接并授权")
        sys.exit(1)
    serial = devices[0]["serial"]
    print(f"使用设备: {serial} {devices[0].get('model', '')}")

    device = AdbDevice(serial=serial)
    w, h = device.get_resolution()
    print(f"分辨率: {w}x{h}  abi={device.get_abi()}  sdk={device.get_sdk()}")

    try:
        bootstrap_minicap(device)
    except BootstrapError as e:
        print(f"[错误] {e}")
        sys.exit(1)

    capture = AdbCapture(device)
    if not capture.start():
        print("[错误] minicap 流启动失败")
        sys.exit(1)

    try:
        img = capture.capture(timeout=8.0)
        if img is None:
            print("[错误] 截图失败")
            sys.exit(1)
        print(f"截到一帧: {img.shape[1]}x{img.shape[0]}")
        if capture.capture_to_file(out_path):
            print(f"已保存: {Path(out_path).resolve()}")
        else:
            print("[错误] 保存失败")
    finally:
        capture.stop()


if __name__ == "__main__":
    main()
