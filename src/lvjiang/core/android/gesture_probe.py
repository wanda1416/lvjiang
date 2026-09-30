"""并发手势探针 — 在真机上验证输入时间线能不能落地

`timeline` 块在设备端编译成一个多 stroke 手势（见
docs/20-requirements/15-input-timeline.md）。框架层支持是明确的，但三件事只有真机
能回答：

1. 本机 ``getMaxStrokeCount()`` 的实际值（AOSP 为 10，厂商 ROM 可能不同）；
2. 游戏是否接受注入的多点触控；
3. 真实触摸取消整组手势的表现。

所以把它做成独立探针而不是塞进工作流：验证的是通道能力，不该混进业务脚本，
失败时也不该让人怀疑是脚本写错了。

命令行::

    python -m lvjiang.core.android.gesture_probe [-s SERIAL] [--hold 2.0]

探针发两路并发触点：左下角按住 ``--hold`` 秒（模拟推摇杆），期间在右下角点两次
（模拟按键）。停在游戏里跑就能直接看出角色是否一边移动一边跳。
"""

from __future__ import annotations

import argparse
import sys

from loguru import logger

from ..timeline import TimelineStep
from .agent import connect_agent
from .device import AdbDevice


def probe(serial: str = "", hold: float = 2.0) -> int:
    """发一组两路并发手势，返回进程退出码"""
    device = AdbDevice(serial or None)
    agent = connect_agent(device)
    if agent is None:
        print("连不上设备端代理：确认手机已装律匠 app 并开启无障碍服务", file=sys.stderr)
        return 2

    status = agent.status or {}
    screen = status.get("screen") or {}
    width = int(screen.get("w") or 0)
    height = int(screen.get("h") or 0)
    if not width or not height:
        print(f"拿不到屏幕尺寸：{screen}", file=sys.stderr)
        return 2
    print(f"设备 app={status.get('app')} 协议={status.get('protocol')} "
          f"屏幕={width}x{height}")

    # 左下角推住、右下角点两次：位置刻意避开顶部状态栏与中心，减少误触
    push_x, push_y = int(width * 0.15), int(height * 0.75)
    tap_x, tap_y = int(width * 0.85), int(height * 0.75)
    steps = [
        TimelineStep(0.0, "touch", label="推住", x1=push_x, y1=push_y,
                     x2=push_x, y2=push_y, hold=hold),
        TimelineStep(hold * 0.25, "touch", label="点击1",
                     x1=tap_x, y1=tap_y, x2=tap_x, y2=tap_y, hold=0.05),
        TimelineStep(hold * 0.6, "touch", label="点击2",
                     x1=tap_x, y1=tap_y, x2=tap_x, y2=tap_y, hold=0.05),
    ]
    print(f"下发：({push_x},{push_y}) 按住 {hold}s，期间 ({tap_x},{tap_y}) 点两次")
    try:
        agent.run_timeline(steps)
    except Exception as exc:  # noqa: BLE001 — 探针要把任何失败原样展示
        print(f"✘ 失败：{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    finally:
        agent.close()
    print("✔ 设备端回报手势已完成。请确认画面里两路动作是否真的同时发生——"
          "回报成功只说明系统接受了注入，游戏是否响应要靠眼睛看")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="在真机上验证多路并发手势（输入时间线）")
    parser.add_argument("-s", "--serial", default="", help="adb 设备序列号")
    parser.add_argument("--hold", type=float, default=2.0,
                        help="推住时长（秒），默认 2.0")
    args = parser.parse_args(argv)
    logger.remove()
    logger.add(sys.stderr, level="INFO")
    return probe(args.serial, args.hold)


if __name__ == "__main__":
    raise SystemExit(main())
