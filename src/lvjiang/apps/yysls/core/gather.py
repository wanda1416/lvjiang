"""采集路线与录制草稿；屏幕操作序列不混入世界坐标地图模型。"""
from __future__ import annotations

import base64
import json
import math
import re
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from pathlib import Path
from uuid import uuid4

import cv2
import numpy as np

from ....core.config.resolver import LOCAL_CONFIG_DIR
from ....core.fs_util import atomic_write_text
from ....workflows.metadata import parse_metadata

SCENE = "map_gather"
SIGNATURE_SHAPE = (32, 48)
GATHER_WORKFLOWS_DIR = LOCAL_CONFIG_DIR / "workflows" / "gather"


def viewport_signature(image: np.ndarray) -> str:
    gray = cv2.cvtColor(image[:, :, :3], cv2.COLOR_BGR2GRAY)
    small = cv2.resize(gray, SIGNATURE_SHAPE[::-1], interpolation=cv2.INTER_AREA)
    return base64.b64encode(small.tobytes()).decode("ascii")


def signature_array(value: str) -> np.ndarray:
    raw = base64.b64decode(value, validate=True)
    if len(raw) != math.prod(SIGNATURE_SHAPE):
        raise ValueError("采集视口记录不完整，请重新录制")
    return np.frombuffer(raw, dtype=np.uint8).reshape(SIGNATURE_SHAPE)


def viewport_difference(left: str, right: str) -> float:
    return float(cv2.absdiff(signature_array(left), signature_array(right)).mean() / 255)


def crop_region(frame: np.ndarray, layout, key: str) -> np.ndarray:
    region = next((r for r in layout.get_scene_regions(SCENE) if r.key == key), None)
    if region is None or region.disabled or not region.has_position:
        raise ValueError(f"采集场景缺少可用标定：{key}，请在场景编辑器中标定")
    canvas = layout.get_canvas()
    h, w = frame.shape[:2]
    x1 = round((canvas.x_ratio + region.x_ratio * canvas.w_ratio) * w)
    y1 = round((canvas.y_ratio + region.y_ratio * canvas.h_ratio) * h)
    x2 = round(x1 + region.w_ratio * canvas.w_ratio * w)
    y2 = round(y1 + region.h_ratio * canvas.h_ratio * h)
    if not (0 <= x1 < x2 <= w and 0 <= y1 < y2 <= h):
        raise ValueError(f"采集区域超出截图：{key}，请重新标定")
    return frame[y1:y2, x1:x2]


@dataclass
class GatherStep:
    x: float
    y: float
    viewport: str = ""
    travel_seconds: float = 0

    def validate(self) -> None:
        if not all(math.isfinite(v) for v in (self.x, self.y, self.travel_seconds)):
            raise ValueError("采集点坐标或耗时无效")
        if not (0 <= self.x <= 1 and 0 <= self.y <= 1 and 0 <= self.travel_seconds <= 1800):
            raise ValueError("采集点坐标或耗时超出范围")
        if self.viewport:
            signature_array(self.viewport)


@dataclass
class GatherRoute:
    key: str = field(default_factory=lambda: uuid4().hex)
    name: str = "新采集路线"
    start_note: str = ""
    layout_key: str = ""
    map_key: str = "M"
    travel_key: str = "V"
    confirm_key: str = "F"
    gather_key: str = "1"
    travel_timeout: float = 120
    gather_seconds: float = 6
    steps: list[GatherStep] = field(default_factory=list)

    def validate(self, *, runnable: bool = False) -> None:
        from ....core.key_names import normalize_pressable
        if not self.key or not self.name.strip():
            raise ValueError("请填写路线名称")
        for key in (self.map_key, self.travel_key, self.confirm_key, self.gather_key):
            normalize_pressable(key)
        if not (math.isfinite(self.travel_timeout) and 10 <= self.travel_timeout <= 1800):
            raise ValueError("识途超时必须在 10–1800 秒之间")
        if not (math.isfinite(self.gather_seconds) and 1 <= self.gather_seconds <= 120):
            raise ValueError("采集等待必须在 1–120 秒之间")
        if runnable and (not self.steps or not self.layout_key or not self.start_note.strip()):
            raise ValueError("请录制采集点并填写固定起点说明")
        for step in self.steps:
            step.validate()

    @classmethod
    def from_dict(cls, data: dict) -> GatherRoute:
        value = deepcopy(data)
        value["steps"] = [GatherStep(**step) for step in value.get("steps", [])]
        route = cls(**value)
        route.validate()
        return route


def gather_step_dsl(route: GatherRoute, step: GatherStep) -> list[str]:
    """把一个领域采集点编译为干净的 WF 动作，不包含原始输入噪声。"""
    return [
        f"press {json.dumps(route.map_key)}",
        f"click ({step.x:.6f}, {step.y:.6f})",
        f"press {json.dumps(route.travel_key)}",
        "wait 0.800",
        f"press {json.dumps(route.confirm_key)}",
        f"wait {step.travel_seconds:.3f}",
        f"press {json.dumps(route.gather_key)}",
        f"wait {route.gather_seconds:.3f}",
    ]


def route_to_dsl(route: GatherRoute) -> str:
    """生成可直接执行和编辑的标准 WF 文件内容。"""
    route.validate(runnable=True)
    meta = [
        f"#% id: gather_{route.key}",
        f"#% name: {json.dumps(route.name, ensure_ascii=False)}",
        "#% runnable: true",
        "#% batchable: false",
        "#% scope: dedicated",
        "#% env: [desktop]",
        f"#% note: {json.dumps(route.start_note, ensure_ascii=False)}",
        f"# gather-layout: {json.dumps(route.layout_key, ensure_ascii=False)}",
        f"# gather-travel-timeout: {route.travel_timeout:g}",
        "",
    ]
    body = [line for step in route.steps for line in gather_step_dsl(route, step)]
    return "\n".join(meta + body) + "\n"


class GatherStore:
    """以 ``config/local/workflows/gather/*.wf`` 作为路线唯一来源。"""

    def __init__(self, root: Path | None = None):
        self.root = root or GATHER_WORKFLOWS_DIR

    @staticmethod
    def _parse(path: Path) -> GatherRoute:
        text = path.read_text(encoding="utf-8-sig")
        meta = parse_metadata(text)
        layout_match = re.search(r'^# gather-layout:\s*(.+)$', text, re.MULTILINE)
        timeout_match = re.search(r'^# gather-travel-timeout:\s*([0-9.]+)$', text, re.MULTILINE)
        if layout_match is None or timeout_match is None:
            raise ValueError("缺少采集路线元数据")
        layout_key = json.loads(layout_match.group(1))
        commands = [
            line.strip() for line in text.splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
        if not commands or len(commands) % 8:
            raise ValueError("采集 WF 必须由每点 8 条标准动作组成")
        route = GatherRoute(
            key=path.stem,
            name=meta.get("name") or path.stem,
            start_note=meta.get("note") or "",
            layout_key=layout_key,
            travel_timeout=float(timeout_match.group(1)),
        )
        for offset in range(0, len(commands), 8):
            block = commands[offset:offset + 8]
            press_map = re.fullmatch(r'press\s+"([^"]+)"', block[0])
            click = re.fullmatch(r"click\s+\(([0-9.]+),\s*([0-9.]+)\)", block[1])
            press_travel = re.fullmatch(r'press\s+"([^"]+)"', block[2])
            confirm_wait = re.fullmatch(r"wait\s+([0-9.]+)", block[3])
            press_confirm = re.fullmatch(r'press\s+"([^"]+)"', block[4])
            travel_wait = re.fullmatch(r"wait\s+([0-9.]+)", block[5])
            press_gather = re.fullmatch(r'press\s+"([^"]+)"', block[6])
            gather_wait = re.fullmatch(r"wait\s+([0-9.]+)", block[7])
            matches = (press_map, click, press_travel, confirm_wait,
                       press_confirm, travel_wait, press_gather, gather_wait)
            if any(match is None for match in matches):
                raise ValueError(f"第 {offset // 8 + 1} 个采集点不是标准采集序列")
            assert press_map is not None
            assert click is not None
            assert press_travel is not None
            assert confirm_wait is not None
            assert press_confirm is not None
            assert travel_wait is not None
            assert press_gather is not None
            assert gather_wait is not None
            if abs(float(confirm_wait.group(1)) - 0.8) > 0.001:
                raise ValueError("V 与 F 之间的等待必须为 0.8 秒")
            keys = (press_map.group(1), press_travel.group(1),
                    press_confirm.group(1), press_gather.group(1))
            waits = (float(travel_wait.group(1)), float(gather_wait.group(1)))
            if route.steps and keys != (route.map_key, route.travel_key,
                                        route.confirm_key, route.gather_key):
                raise ValueError("同一路线各采集点的按键必须一致")
            if route.steps and abs(waits[1] - route.gather_seconds) > 0.001:
                raise ValueError("同一路线各采集点的采集等待必须一致")
            route.map_key, route.travel_key, route.confirm_key, route.gather_key = keys
            route.gather_seconds = waits[1]
            route.steps.append(GatherStep(float(click.group(1)), float(click.group(2)),
                                          travel_seconds=waits[0]))
        route.validate(runnable=True)
        return route

    def routes(self) -> tuple[list[GatherRoute], list[str]]:
        routes, errors = [], []
        for path in sorted(self.root.glob("*.wf")) if self.root.exists() else []:
            try:
                routes.append(self._parse(path))
            except (ValueError, TypeError, KeyError, OSError, json.JSONDecodeError) as exc:
                errors.append(f"采集脚本 {path.name} 无法读取：{exc}")
        return routes, errors

    def save(self, route: GatherRoute, previous: dict | None) -> dict:
        route.validate(runnable=True)
        value = asdict(route)
        path = self.root / f"{route.key}.wf"
        if path.exists():
            current = asdict(self._parse(path))
            if current != previous:
                raise ValueError("这条路线已被其他窗口修改，请重新打开后编辑")
        elif previous is not None:
            raise ValueError("这条路线已被其他窗口删除，请重新打开后编辑")
        atomic_write_text(path, route_to_dsl(route), prefix=f".{route.key}.")
        return deepcopy(value)


class GatherClickBuffer:
    """在 F1 时按当前鼠标位置生成一个语义采集点。"""

    def mark(self, x: float, y: float) -> GatherStep:
        if not (0 <= x <= 1 and 0 <= y <= 1):
            raise ValueError("鼠标不在游戏画布内，请悬停到采集物图标后重试")
        return GatherStep(x, y)
