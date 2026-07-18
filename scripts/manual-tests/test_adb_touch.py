"""ADB minitouch 触摸联调脚本（需真机）

连接第一台 adb 设备，push minitouch，启动后在屏幕中心点一次、再横向滑一次。

用法: python scripts/manual-tests/test_adb_touch.py
"""

import sys
import time
from pathlib import Path

# 项目根目录（scripts/manual-tests/ → 上两级）
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from lvjiang.core.adb import AdbDevice, list_adb_devices
from lvjiang.core.adb.bootstrap import bootstrap_minitouch, BootstrapError
from lvjiang.core.adb.minitouch import MinitouchClient


def main():
    devices = list_adb_devices()
    if not devices:
        print("未找到 ADB 设备，请确认已连接并授权")
        sys.exit(1)
    serial = devices[0]["serial"]
    print(f"使用设备: {serial} {devices[0].get('model', '')}")

    device = AdbDevice(serial=serial)
    w, h = device.get_resolution()
    print(f"分辨率: {w}x{h}  abi={device.get_abi()}")

    try:
        bootstrap_minitouch(device)
    except BootstrapError as e:
        print(f"[错误] {e}")
        sys.exit(1)

    client = MinitouchClient(device)
    if not client.start():
        print("[错误] minitouch 启动失败")
        sys.exit(1)

    try:
        cx, cy = w // 2, h // 2
        print(f"点击屏幕中心 ({cx}, {cy})")
        client.tap(cx, cy)
        time.sleep(1.0)

        x1, x2 = int(w * 0.2), int(w * 0.8)
        print(f"横向滑动 ({x1}, {cy}) -> ({x2}, {cy})，时长 0.4s")
        client.swipe(x1, cy, x2, cy, duration=0.4)
        print("完成")
    finally:
        client.stop()


if __name__ == "__main__":
    main()
