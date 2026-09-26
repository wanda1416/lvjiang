"""采集路线与录制草稿；屏幕操作序列不混入世界坐标地图模型。"""
from __future__ import annotations

import base64
import math
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from uuid import uuid4

import cv2
import numpy as np

from ....core.config.session import SessionStore, get_session_store

NODE = "yysls_gather"
SCENE = "map_gather"
SIGNATURE_SHAPE = (32, 48)


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
    viewport: str
    travel_seconds: float = 0

    def validate(self) -> None:
        if not all(math.isfinite(v) for v in (self.x, self.y, self.travel_seconds)):
            raise ValueError("采集点坐标或耗时无效")
        if not (0 <= self.x <= 1 and 0 <= self.y <= 1 and 0 <= self.travel_seconds <= 1800):
            raise ValueError("采集点坐标或耗时超出范围")
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
        f"press {route.map_key}",
        f"click ({step.x:.6f}, {step.y:.6f})",
        f"press {route.travel_key}",
        "wait 0.800",
        f"press {route.confirm_key}",
        f"wait {step.travel_seconds:.3f}",
        f"press {route.gather_key}",
        f"wait {route.gather_seconds:.3f}",
    ]


def route_to_dsl(route: GatherRoute) -> str:
    """生成可审阅的语义 WF；实际任务额外保留视口与到达安全检查。"""
    route.validate(runnable=True)
    return "\n".join(line for step in route.steps for line in gather_step_dsl(route, step)) + "\n"


class GatherStore:
    """每次只合并一条路线；旧编辑窗口不得覆盖别的路线或新的同名版本。"""

    def __init__(self, session: SessionStore | None = None):
        self.session = session or get_session_store()

    def routes(self) -> tuple[list[GatherRoute], list[str]]:
        node = self.session.get_node(NODE, {})
        if not isinstance(node, dict) or node.get("schema_version", 1) != 1:
            return [], ["采集路线格式不受支持，请检查配置版本"]
        if not isinstance(node.get("routes", {}), dict):
            return [], ["采集路线列表格式错误"]
        routes, errors = [], []
        for key, data in node.get("routes", {}).items():
            try:
                route = GatherRoute.from_dict(data)
                if route.key != key:
                    raise ValueError("路线标识不一致")
                routes.append(route)
            except (ValueError, TypeError, KeyError) as exc:
                errors.append(f"一条采集路线无法读取：{exc}")
        return routes, errors

    def save(self, route: GatherRoute, previous: dict | None) -> dict:
        route.validate()
        value = asdict(route)

        def mutate(node):
            node = deepcopy(node or {})
            if not isinstance(node, dict) or node.get("schema_version", 1) != 1:
                raise ValueError("采集路线格式不受支持，未写入任何变更")
            routes = node.setdefault("routes", {})
            if not isinstance(routes, dict):
                raise ValueError("采集路线列表格式错误，未写入任何变更")
            if routes.get(route.key) != previous:
                raise ValueError("这条路线已被其他窗口修改，请重新打开后编辑")
            routes[route.key] = value
            node["schema_version"] = 1
            return node

        try:
            self.session.mutate_node(NODE, mutate)
        except ValueError:
            # 并行保存冲突后，下一次打开必须能看到磁盘上的新版本。
            self.session.reload()
            raise
        return deepcopy(value)


class GatherClickBuffer:
    """保存最新地图画面，并在 F1 时按当前鼠标位置生成采集点。"""

    def __init__(self):
        self.latest: tuple[float, str] | None = None

    def update_frame(self, timestamp: float, viewport: str) -> None:
        self.latest = (timestamp, viewport)

    def mark(self, x: float, y: float, timestamp: float) -> GatherStep:
        if self.latest is None or not 0 <= timestamp - self.latest[0] <= 0.75:
            raise ValueError("暂未取得当前地图画面，请保持地图打开后重试")
        if not (0 <= x <= 1 and 0 <= y <= 1):
            raise ValueError("鼠标不在游戏画布内，请悬停到采集物图标后重试")
        return GatherStep(x, y, self.latest[1])
