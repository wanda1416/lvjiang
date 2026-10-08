"""设备体检：把设备端上报的状态翻译成"哪里不对、怎么办"。

设备端 `status` 早就上报了协议版本、app 版本、无障碍/Shizuku 可用性、屏幕与标定
状态，但 PC 侧一个字都没显示过——用户看到的只有"连不上设备"。这里负责把它们摊开
成可读结论，并生成一段可直接粘进反馈的报告（字段对齐快速开始 1.6 的要求）。
"""
from __future__ import annotations

from dataclasses import dataclass

from ...i18n import tr

#: PC 侧当前实现的协议版本。不匹配时必须说清该升哪一边，而不是只报"连不上"。
_OK = "ok"
_WARN = "warn"
_BAD = "bad"


@dataclass(frozen=True)
class CheckItem:
    """一条体检结论。"""

    name: str
    value: str
    level: str = _OK
    hint: str = ""

    @property
    def ok(self) -> bool:
        return self.level == _OK


def _aspect(width: int, height: int) -> float:
    long_side, short_side = max(width, height), min(width, height)
    return (long_side / short_side) if short_side else 0.0


def build_checks(status: dict, pc_version: str,
                 pc_protocol: int) -> list[CheckItem]:
    """把一次 status 翻译成体检清单。status 为空表示未连接。"""
    if not status:
        return [CheckItem(
            tr("设备端代理"), tr("未连接"), _BAD,
            tr("确认手机已安装律匠 app、与本机同一局域网或已用 USB 连接，"
               "并在 app 内开启无障碍服务"))]

    items: list[CheckItem] = []

    app_version = str(status.get("app") or "")
    if not app_version:
        items.append(CheckItem(
            tr("设备端版本"), tr("未上报"), _WARN,
            tr("旧版 app 不上报版本，建议升级到与 PC 相同的版本")))
    elif app_version == pc_version:
        items.append(CheckItem(tr("设备端版本"), app_version, _OK))
    else:
        items.append(CheckItem(
            tr("设备端版本"), f"{app_version} ≠ PC {pc_version}", _WARN,
            tr("版本不一致时协议和随包配置都可能对不上。建议把低的那一边升到高的")))

    device_protocol = status.get("protocol")
    if device_protocol == pc_protocol:
        items.append(CheckItem(tr("协议版本"), str(device_protocol), _OK))
    else:
        newer = tr("手机") if (
            isinstance(device_protocol, int) and device_protocol > pc_protocol
        ) else tr("电脑")
        items.append(CheckItem(
            tr("协议版本"), f"{device_protocol} ≠ PC {pc_protocol}", _BAD,
            tr("协议不匹配会直接连不上。{newer}端较新，请升级另一边")
            .format(newer=newer)))

    if status.get("a11y"):
        items.append(CheckItem(tr("无障碍服务"), tr("已开启"), _OK))
    else:
        items.append(CheckItem(
            tr("无障碍服务"), tr("未开启"), _BAD,
            tr("手势、点击与截图都依赖它。在 app 内按引导开启；部分系统会在"
               "「受限设置」里额外拦一道，需要先允许")))

    if status.get("shizuku_granted"):
        items.append(CheckItem(tr("Shizuku"), tr("已授权"), _OK))
    elif status.get("shizuku"):
        items.append(CheckItem(
            tr("Shizuku"), tr("在运行但未授权"), _WARN,
            tr("只影响 shell 通道；手势与无障碍截图不需要它")))
    else:
        items.append(CheckItem(
            tr("Shizuku"), tr("未运行"), _OK,
            tr("可选能力，不装也能用无障碍通道")))

    strokes = status.get("max_strokes")
    if isinstance(strokes, int) and strokes > 0:
        level = _OK if strokes >= 2 else _BAD
        items.append(CheckItem(
            tr("并发手势上限"), tr("{n} 路").format(n=strokes), level,
            "" if strokes >= 2 else tr("本机不支持多路并发，输入时间线无法落地")))
    else:
        items.append(CheckItem(
            tr("并发手势上限"), tr("未上报"), _WARN,
            tr("旧版 app 不上报该值，升级后可见")))

    screen = status.get("screen") or {}
    width, height = int(screen.get("w") or 0), int(screen.get("h") or 0)
    if width and height:
        ratio = _aspect(width, height)
        items.append(CheckItem(
            tr("屏幕"),
            f"{width}×{height}（{tr('旋转')} {screen.get('rotation', 0)}°，"
            f"{tr('比例')} {ratio:.2f}）", _OK))
    else:
        items.append(CheckItem(tr("屏幕"), tr("未上报"), _WARN))

    if status.get("calib_identity", True):
        items.append(CheckItem(
            tr("屏幕标定"), tr("恒等（未标定）"), _OK,
            tr("截图坐标与输入坐标一致。非 20:9 机型若点位整体偏移，"
               "在手机端悬浮球里做一次屏幕标定")))
    else:
        items.append(CheckItem(tr("屏幕标定"), tr("已标定"), _OK))

    # 体检自己就占着一条连接，设备端上报的是含它在内的总数；这里减掉自己，
    # 只告诉用户"除本窗口外还有几路"。同机另开一个窗口和另一台电脑都会让这个
    # 数字变大，无法从连接数区分，所以只陈述现象，不断言来源。
    connections = status.get("pc_connections")
    others = connections - 1 if isinstance(connections, int) else 0
    if others > 0:
        items.append(CheckItem(
            tr("其他连接"), str(others), _WARN,
            tr("除本窗口外还有 {n} 路连接连着这台手机，可能来自另一个窗口"
               "或另一台电脑，操作会互相打断").format(n=others)))

    last_op = status.get("last_op")
    if last_op:
        ok = status.get("last_op_ok")
        items.append(CheckItem(
            tr("最近一次操作"),
            f"{last_op} → {tr('成功') if ok else tr('失败')}",
            _OK if ok else _WARN,
            "" if ok else tr("设备端收到了指令但执行失败，展开日志看原因")))
    return items


def capability_notes(status: dict) -> list[str]:
    """当前通道能跑什么、不能跑什么。

    `#% requires: [device_gesture]` 的脚本只有无障碍通道支持，ADB / Shizuku
    会在**加载期**被拒——用户现在只能跑到报错才知道，这里提前讲清。
    """
    notes: list[str] = []
    if not status:
        return [tr("未连接设备，无法判断可用能力")]
    if status.get("a11y"):
        notes.append(tr("✅ 支持输入时间线（并发手势）：声明 "
                        "#% requires: [device_gesture] 的脚本可以运行"))
    else:
        notes.append(tr("❌ 不支持输入时间线：声明 "
                        "#% requires: [device_gesture] 的脚本会在加载时被拒绝，"
                        "需要先开启无障碍服务"))
    if status.get("shizuku_granted"):
        notes.append(tr("✅ Shizuku 已授权：shell 通道可用"))
    else:
        notes.append(tr("ℹ️ shell 通道不可用（Shizuku 未授权）。"
                        "普通点击与截图不受影响"))
    return notes


def build_report(status: dict, pc_version: str, pc_protocol: int,
                 serial: str, extra: dict | None = None) -> str:
    """生成可直接粘进反馈的诊断报告。

    字段对齐快速开始 1.6「一次性提供哪些信息」，省掉开发者逐条追问。
    """
    lines = [
        tr("律匠 移动设备诊断报告"),
        f"PC {tr('版本')}: {pc_version}（{tr('协议')} {pc_protocol}）",
        f"{tr('设备序列号')}: {serial or tr('未连接')}",
        "",
    ]
    for item in build_checks(status, pc_version, pc_protocol):
        mark = {"ok": "OK ", "warn": "警告", "bad": "错误"}.get(item.level, "")
        lines.append(f"[{mark}] {item.name}: {item.value}")
        if item.hint and not item.ok:
            lines.append(f"       → {item.hint}")
    lines.append("")
    lines.extend(capability_notes(status))
    for key, value in (extra or {}).items():
        lines.append(f"{key}: {value}")
    return "\n".join(lines)
