"""高精度输入轨迹的数据格式与双文件保存事务。

``.lvtrace`` 保存统一时钟上的原始有序输入事件。鼠标移动保留设备报告的
整数增量，并在头部记录源画布尺寸；回放时按 ``dx/source_width`` 与
``dy/source_height`` 恢复归一化语义，避免先转小数造成逐包舍入损失。
"""

from __future__ import annotations

import hashlib
import json
import os
import zlib
from dataclasses import dataclass
from pathlib import Path

from .fs_util import atomic_write_bytes

TRACE_PLACEHOLDER = "__LVTRACE__"
TRACE_MAGIC = b"LVTRACE\x00"
TRACE_VERSION = 1
_VALID_KINDS = {"move", "button", "wheel", "key"}
# 鼠标键标准名：left/right/middle 三键 + x1/x2 侧键（前进/后退，仅 Windows
# 后端的 pynput.mouse.Button 才有对应枚举成员）。recorder.py 复用此集合
# 判定 pynput 按键事件是否值得录制，避免两处各写一份同样的白名单。
VALID_BUTTONS = {"left", "right", "middle", "x1", "x2"}


class InputTraceError(ValueError):
    """轨迹文件缺失、损坏或内容不合法。"""


@dataclass(frozen=True)
class InputTraceEvent:
    """相对录制起点的单个输入事件。

    ``values`` 的形状由 kind 决定：

    - move: ``(dx, dy)``
    - button: ``(button_name, is_down)``
    - wheel: ``(delta,)``
    - key: ``(key_name, is_down)``
    """

    at_us: int
    kind: str
    values: tuple


@dataclass(frozen=True)
class InputTrace:
    source_width: int
    source_height: int
    events: tuple[InputTraceEvent, ...]


def _validate_trace(trace: InputTrace) -> None:
    if trace.source_width <= 0 or trace.source_height <= 0:
        raise InputTraceError("轨迹源画布尺寸必须大于 0")
    previous = -1
    for index, event in enumerate(trace.events):
        if event.at_us < previous:
            raise InputTraceError(f"轨迹事件时间倒退: #{index}")
        if event.at_us < 0:
            raise InputTraceError(f"轨迹事件时间不能为负数: #{index}")
        if event.kind not in _VALID_KINDS:
            raise InputTraceError(f"未知轨迹事件类型: {event.kind}")
        expected = {"move": 2, "button": 2, "wheel": 1, "key": 2}[event.kind]
        if len(event.values) != expected:
            raise InputTraceError(
                f"轨迹事件参数数量错误: #{index} {event.kind}")
        if event.kind == "move":
            dx, dy = event.values
            if (not isinstance(dx, int) or isinstance(dx, bool)
                    or not isinstance(dy, int) or isinstance(dy, bool)):
                raise InputTraceError(f"轨迹移动量必须是整数: #{index}")
            if abs(dx) > trace.source_width or abs(dy) > trace.source_height:
                raise InputTraceError(f"轨迹移动量超出归一化范围: #{index}")
        elif event.kind == "button":
            button, is_down = event.values
            if (button not in VALID_BUTTONS
                    or not isinstance(is_down, bool)):
                raise InputTraceError(f"轨迹鼠标键事件不合法: #{index}")
        elif event.kind == "wheel":
            if (not isinstance(event.values[0], int)
                    or isinstance(event.values[0], bool)):
                raise InputTraceError(f"轨迹滚轮量必须是整数: #{index}")
        elif event.kind == "key":
            key, is_down = event.values
            if not isinstance(key, str) or not isinstance(is_down, bool):
                raise InputTraceError(f"轨迹键盘事件不合法: #{index}")
            # 延迟到函数体内 import：core.desktop 包 __init__ 会加载
            # send_input，而 send_input 反过来 import 本模块的 InputTrace——
            # 模块顶层 import 会在 input_trace 尚未初始化完时形成循环导入。
            from .desktop.win32_keyboard import normalize_key
            try:
                normalize_key(key)
            except ValueError as exc:
                raise InputTraceError(
                    f"轨迹键盘事件按键名未知: #{index} {exc}") from exc
        previous = event.at_us


def encode_input_trace(trace: InputTrace) -> bytes:
    """编码为带 magic/version 的确定性 zlib 压缩数据。"""
    _validate_trace(trace)
    payload = {
        "source": [trace.source_width, trace.source_height],
        "events": [
            [event.at_us, event.kind, *event.values]
            for event in trace.events
        ],
    }
    raw = json.dumps(
        payload, ensure_ascii=False, separators=(",", ":"),
    ).encode("utf-8")
    return TRACE_MAGIC + bytes([TRACE_VERSION]) + zlib.compress(raw, level=9)


def decode_input_trace(data: bytes) -> InputTrace:
    """解码并完整校验 `.lvtrace` 内容。"""
    prefix_size = len(TRACE_MAGIC) + 1
    if len(data) <= prefix_size or not data.startswith(TRACE_MAGIC):
        raise InputTraceError("不是有效的 lvtrace 文件")
    version = data[len(TRACE_MAGIC)]
    if version != TRACE_VERSION:
        raise InputTraceError(
            f"不支持的 lvtrace 版本: {version}（当前 {TRACE_VERSION}）")
    try:
        payload = json.loads(zlib.decompress(data[prefix_size:]).decode("utf-8"))
        source_width, source_height = payload["source"]
        events = tuple(
            InputTraceEvent(
                at_us=int(row[0]),
                kind=str(row[1]),
                values=tuple(row[2:]),
            )
            for row in payload["events"]
        )
        trace = InputTrace(
            source_width=int(source_width),
            source_height=int(source_height),
            events=events,
        )
    except (KeyError, TypeError, ValueError, zlib.error, json.JSONDecodeError) as exc:
        raise InputTraceError(f"lvtrace 内容损坏: {exc}") from exc
    _validate_trace(trace)
    return trace


def load_input_trace(path: str | Path) -> InputTrace:
    path = Path(path)
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise InputTraceError(f"无法读取输入轨迹: {path} ({exc})") from exc
    return decode_input_trace(data)


def _atomic_write(path: Path, data: bytes) -> None:
    """在目标目录写临时文件并用 os.replace 原子替换单个文件。"""
    atomic_write_bytes(path, data, prefix=f".{path.name}.", fsync=True)


def save_input_trace_bundle(
    wf_path: str | Path,
    wf_template: str,
    trace: InputTrace,
    *,
    workflows_root: str | Path | None = None,
) -> tuple[Path, Path, str]:
    """一次保存 WF 与配套轨迹，返回 ``(wf, trace, final_text)``。

    轨迹先写、WF 后写。WF 是对外可见的提交点，因此任何失败都不会让新 WF
    指向尚未存在的轨迹。轨迹以内容哈希命名且不可变；进程在两次 replace
    之间崩溃时最多留下一个无引用轨迹。
    """
    wf_path = Path(wf_path).resolve()
    if wf_path.suffix.lower() != ".wf":
        raise InputTraceError("高精度录制必须保存为 .wf 文件")
    if TRACE_PLACEHOLDER not in wf_template:
        raise InputTraceError("WF 模板缺少输入轨迹占位符")

    encoded = encode_input_trace(trace)
    digest = hashlib.sha256(encoded).hexdigest()[:16]

    trace_root: Path
    if workflows_root is not None:
        candidate = Path(workflows_root).resolve()
        try:
            wf_path.relative_to(candidate)
        except ValueError:
            trace_root = wf_path.parent / "lvtrace"
        else:
            trace_root = candidate / "lvtrace"
    else:
        trace_root = wf_path.parent / "lvtrace"

    trace_path = trace_root / f"{digest}.lvtrace"
    trace_ref = Path(os.path.relpath(trace_path, wf_path.parent)).as_posix()
    final_text = wf_template.replace(TRACE_PLACEHOLDER, trace_ref)
    if not final_text.endswith("\n"):
        final_text += "\n"

    trace_created = not trace_path.exists()
    try:
        if trace_created:
            _atomic_write(trace_path, encoded)
        _atomic_write(wf_path, final_text.encode("utf-8"))
    except BaseException:
        if trace_created:
            try:
                trace_path.unlink(missing_ok=True)
            except OSError:
                pass
        raise
    return wf_path, trace_path, final_text


__all__ = [
    "InputTrace",
    "InputTraceError",
    "InputTraceEvent",
    "TRACE_PLACEHOLDER",
    "VALID_BUTTONS",
    "decode_input_trace",
    "encode_input_trace",
    "load_input_trace",
    "save_input_trace_bundle",
]
