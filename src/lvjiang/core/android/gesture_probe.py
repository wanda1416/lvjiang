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
import time
from dataclasses import dataclass

from loguru import logger

from ..timeline import TimelineStep
from .agent import connect_agent
from .device import AdbDevice


@dataclass(frozen=True)
class ProbeOutcome:
    """一次并发手势探针的结果。供 CLI 与「移动设备」工具共用。"""

    ok: bool
    message: str
    #: 实际下发的两个落点，便于在界面上告诉用户该盯哪里
    push_point: tuple[int, int] = (0, 0)
    tap_point: tuple[int, int] = (0, 0)


def build_probe_steps(
    width: int, height: int, hold: float, taps: int = 2,
) -> tuple[list[TimelineStep], tuple[int, int], tuple[int, int]]:
    """构造"左下角按住、期间右下角连点"的两路并发时间线。

    落点刻意避开顶部状态栏与屏幕中心：状态栏会被系统拦，中心区常有游戏的主要
    交互控件，误触代价高。
    """
    push = (int(width * 0.15), int(height * 0.75))
    tap = (int(width * 0.85), int(height * 0.75))
    steps = [
        TimelineStep(0.0, "touch", label="推住", x1=push[0], y1=push[1],
                     x2=push[0], y2=push[1], hold=hold),
    ]
    for index in range(max(1, taps)):
        offset = hold * (0.25 + 0.35 * index)
        steps.append(TimelineStep(
            offset, "touch", label=f"点击{index + 1}",
            x1=tap[0], y1=tap[1], x2=tap[0], y2=tap[1], hold=0.05))
    return steps, push, tap


def run_concurrent_probe(agent, width: int, height: int,
                         hold: float = 2.0) -> ProbeOutcome:
    """在已连接的 agent 上跑一次并发手势探针。不负责连接与关闭。

    设备端回报成功只说明系统接受了注入；游戏是否响应只能靠眼睛看，所以结论
    文案必须把这两件事分开说，不能让人以为"成功"等于"游戏动了"。
    """
    steps, push, tap = build_probe_steps(width, height, hold)
    try:
        agent.run_timeline(steps)
    except Exception as exc:  # noqa: BLE001 — 探针要把任何失败原样展示
        return ProbeOutcome(
            ok=False, message=f"{type(exc).__name__}: {exc}",
            push_point=push, tap_point=tap)
    return ProbeOutcome(
        ok=True,
        message=("设备端回报手势已完成。请确认画面里两路动作是否真的同时发生"
                 "——回报成功只说明系统接受了注入，游戏是否响应要靠眼睛看"),
        push_point=push, tap_point=tap)


def screen_size(agent) -> tuple[int, int]:
    """从 agent 状态取当前朝向的屏幕尺寸；拿不到返回 (0, 0)。"""
    screen = (agent.status or {}).get("screen") or {}
    try:
        return int(screen.get("w") or 0), int(screen.get("h") or 0)
    except (TypeError, ValueError):
        return 0, 0


def measure_round_trip(agent, rounds: int = 5) -> list[float]:
    """量 PC ↔ 设备端的往返耗时（毫秒），用来回答"是不是手机太慢"。

    用 calib_get 而不是业务操作：它只读不写，重复跑没有副作用。
    """
    samples: list[float] = []
    for _ in range(max(1, rounds)):
        started = time.perf_counter()
        try:
            agent.calib_get()
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"往返探测失败: {exc}")
            break
        samples.append((time.perf_counter() - started) * 1000)
    return samples


def probe(serial: str = "", hold: float = 2.0) -> int:
    """命令行入口：连设备、跑一次探针、打印结果，返回进程退出码"""
    device = AdbDevice(serial or None)
    agent = connect_agent(device)
    if agent is None:
        print("连不上设备端代理：确认手机已装律匠 app 并开启无障碍服务", file=sys.stderr)
        return 2

    width, height = screen_size(agent)
    if not width or not height:
        print(f"拿不到屏幕尺寸：{(agent.status or {}).get('screen')}", file=sys.stderr)
        agent.close()
        return 2
    status = agent.status or {}
    print(f"设备 app={status.get('app')} 协议={status.get('protocol')} "
          f"屏幕={width}x{height} 手势上限={agent.max_strokes or '未上报'}")

    outcome = run_concurrent_probe(agent, width, height, hold)
    print(f"下发：{outcome.push_point} 按住 {hold}s，"
          f"期间 {outcome.tap_point} 点两次")
    agent.close()
    if not outcome.ok:
        print(f"✘ 失败：{outcome.message}", file=sys.stderr)
        return 1
    print(f"✔ {outcome.message}")
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
