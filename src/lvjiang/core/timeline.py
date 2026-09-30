"""输入时间线 — 若干路输入各自在时间线上何时开始、持续多久

DSL 的 ``timeline`` 块编译成一串 :class:`TimelineStep`，再交给输入后端落地。
步骤本身与后端无关：只说"第 t 秒，这一路做什么"，怎么并发是后端的事。

设计边界见 docs/20-requirements/15-input-timeline.md，其中两条决定了本模块的形状：

- **偏移是相对块起点的绝对值**，不是间隔。并发的正确性只取决于各路何时开始。
- **整块原子**：a11y 多 stroke 手势任一路失败，系统回调是整组取消。所以校验
  放在下发之前——宁可不执行，也不要留下"摇杆推住了、点击没落地而手指还按着"。
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

#: a11y 单次 dispatchGesture 的总时长上限（秒）。超了整组手势直接失败，
#: 所以在下发前就拒绝，并在消息里点明是这条限制。
MAX_TIMELINE_SECONDS = 60.0

#: GestureDescription.getMaxStrokeCount() 在 AOSP 是 10。设备端会用真实值再校验
#: 一次；这里的常量只用于在 PC 侧提前给出可读的拒绝理由。
MAX_TOUCH_STROKES = 10


@dataclass(frozen=True)
class TimelineStep:
    """时间线上的一路输入。

    ``kind`` 只有两类，对应两种后端原语：

    - ``"key"``：按键（桌面 down/up）。设备端没有可保持的按键，见模块说明。
    - ``"touch"``：触点。``(x1, y1) == (x2, y2)`` 表示按住不动，否则是滑动；
      ``move`` 是滑到终点用多久，``hold`` 是到达后按住多久。
    """

    offset: float           # 相对块起点的秒数
    kind: str               # "key" | "touch"
    label: str = ""         # 日志用的人类可读名
    key: str = ""           # kind="key"
    hold: float = 0.0       # 按住时长（key 与 touch 共用）
    x1: int = 0
    y1: int = 0
    x2: int = 0
    y2: int = 0
    move: float = 0.0       # kind="touch" 的移动段时长

    @property
    def is_touch(self) -> bool:
        return self.kind == "touch"

    @property
    def end(self) -> float:
        """这一路占用到第几秒"""
        return self.offset + self.move + self.hold


def validate_timeline(steps: list[TimelineStep], *, touch: bool) -> None:
    """下发前校验；不合法抛 ValueError，调用方转成面向用户的报错。

    Args:
        touch: 目标后端是否用触点表达（设备端为真）。触点受 a11y 的 stroke
            数上限约束，按键没有这个限制。
    """
    if not steps:
        raise ValueError("时间线没有任何步骤")
    for step in steps:
        if step.offset < 0:
            raise ValueError(f"时间线偏移不能为负：{step.label or step.kind} @{step.offset}")
        if step.hold < 0 or step.move < 0:
            raise ValueError(f"时间线时长不能为负：{step.label or step.kind}")
        if step.kind not in ("key", "touch"):
            raise ValueError(f"未知的时间线步骤类型：{step.kind}")
    span = max(step.end for step in steps)
    if span > MAX_TIMELINE_SECONDS:
        raise ValueError(
            f"时间线总长 {span:.1f}s 超过上限 {MAX_TIMELINE_SECONDS:.0f}s"
            "（单次手势的系统限制）；请拆成多个 timeline 块"
        )
    if touch:
        strokes = sum(1 for step in steps if step.is_touch)
        if strokes > MAX_TOUCH_STROKES:
            raise ValueError(
                f"时间线有 {strokes} 路触点，超过同时可注入的上限 "
                f"{MAX_TOUCH_STROKES}（系统手势限制）"
            )


def describe_timeline(steps: list[TimelineStep]) -> str:
    """一行日志：别打印超长结构，只给能对账的要点"""
    span = max((step.end for step in steps), default=0.0)
    parts = [f"@{s.offset:g}{s.label and ' ' + s.label}" for s in steps]
    return f"{len(steps)} 路 / {span:g}s：" + "、".join(parts)


@dataclass(frozen=True)
class TimelineEvent:
    """时间线展开后的瞬时事件：第 ``at`` 秒对 ``step`` 做 ``op``。

    展开成事件而不是"每路一个线程"：事件本身是瞬时的，一个循环按时刻依次下发
    就能得到并发效果，不引入线程与竞态。
    """

    at: float
    op: str                 # "key_down" | "key_up"
    step: TimelineStep


#: press 无 hold 时的按压间隔（秒）。与桌面后端 press 的 down→up 间隔同量级，
#: 太短的话部分游戏读不到这次按键。
DEFAULT_TAP_SECONDS = 0.05


def expand_key_events(steps: list[TimelineStep]) -> list[TimelineEvent]:
    """把按键类步骤展开成按时刻排序的 down/up 事件。

    ``hold`` 为 0 时按 :data:`DEFAULT_TAP_SECONDS` 给一次可被识别的按压。
    """
    events: list[TimelineEvent] = []
    for step in steps:
        if step.kind != "key":
            continue
        hold = step.hold if step.hold > 0 else DEFAULT_TAP_SECONDS
        events.append(TimelineEvent(step.offset, "key_down", step))
        events.append(TimelineEvent(step.offset + hold, "key_up", step))
    # 同一时刻先抬后按：同一个键紧邻的 up→down 不能反过来，否则会丢一次按键
    events.sort(key=lambda e: (e.at, 0 if e.op == "key_up" else 1))
    return events


def run_key_timeline(
    steps: list[TimelineStep],
    key_down: Callable[[str], None],
    key_up: Callable[[str], None],
    *,
    stop_check: Callable[[], bool] | None = None,
) -> None:
    """按时刻下发按键时间线；桌面两个后端共用。

    单调时钟为基准逐事件等待:事件本身是瞬时的,一个循环就能得到并发效果。
    时钟用 ``perf_counter`` 而不是累加 sleep——累加会把每次 sleep 的系统误差
    滚成整条时间线的漂移。

    **任何退出路径都要把按下的键放掉**:停止请求、异常都一样。不放的话角色会
    一直往一个方向走,而用户以为任务已经停了。
    """
    events = expand_key_events(steps)
    pressed: set[str] = set()
    started = time.perf_counter()
    try:
        for event in events:
            remaining = event.at - (time.perf_counter() - started)
            while remaining > 0:
                if stop_check is not None and stop_check():
                    return
                time.sleep(min(remaining, 0.02))
                remaining = event.at - (time.perf_counter() - started)
            if stop_check is not None and stop_check():
                return
            if event.op == "key_down":
                key_down(event.step.key)
                pressed.add(event.step.key)
            else:
                key_up(event.step.key)
                pressed.discard(event.step.key)
    finally:
        for key in sorted(pressed):
            try:
                key_up(key)
            except Exception:  # noqa: BLE001 — 清理路径不能因单个键失败而漏掉其余
                pass
