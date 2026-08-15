"""布局运行时数据模型

归一化坐标的运行时实例：Region / Point / Arrow / Panel / Layout / CanvasConfig。
以及 find 指令产出的 FoundRegion。

与 scene_definition（定义时类型）不同，这些类描述的是
「布局 JSON 中保存的具体坐标值」，由 layout_manager 加载并供引擎运行时使用。
"""

from dataclasses import asdict, dataclass, field

from .coord_types import CircleCoordRef, RectCoordRef


@dataclass
class CanvasConfig:
    """画布配置（布局级别）—— 定义截图中的纯内容区域（排除窗口边框）"""
    x_ratio: float = 0.0
    y_ratio: float = 0.0
    w_ratio: float = 1.0
    h_ratio: float = 1.0

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "CanvasConfig":
        return CanvasConfig(
            x_ratio=d.get("x_ratio", 0.0),
            y_ratio=d.get("y_ratio", 0.0),
            w_ratio=d.get("w_ratio", 1.0),
            h_ratio=d.get("h_ratio", 1.0),
        )


@dataclass
class FoundRegion:
    """find 指令产出的文字区域（画布归一化坐标）

    与 Region 不同：key 为动态生成的标识（或空字符串），
    坐标来自 OCR 识别到的文字 bbox，而非预定义的场景布局。
    可直接用于 click / drag 的目标。
    """
    x_ratio: float
    y_ratio: float
    w_ratio: float
    h_ratio: float
    text: str = ""  # 匹配到的文字内容（供调试日志使用）

    def center_ratios(self) -> tuple[float, float]:
        """区域中心的画布归一化坐标"""
        return self.x_ratio + self.w_ratio / 2, self.y_ratio + self.h_ratio / 2

    def to_coord_ref(self) -> RectCoordRef:
        """转换为 RectCoordRef（左上角 → 中心点）"""
        cx = self.x_ratio + self.w_ratio / 2
        cy = self.y_ratio + self.h_ratio / 2
        return RectCoordRef(cx=cx, cy=cy, w=self.w_ratio, h=self.h_ratio)


@dataclass
class Region:
    """单个区域实例（归一化坐标）

    仅存储位置数据，名称等元信息通过 key 从场景定义 (RegionDef) 获取。
    """
    key: str
    x_ratio: float
    y_ratio: float
    w_ratio: float
    h_ratio: float

    def to_dict(self) -> dict:
        return asdict(self)

    def to_coord_ref(self) -> RectCoordRef:
        """转换为 RectCoordRef（左上角 → 中心点）"""
        cx = self.x_ratio + self.w_ratio / 2
        cy = self.y_ratio + self.h_ratio / 2
        return RectCoordRef(cx=cx, cy=cy, w=self.w_ratio, h=self.h_ratio)

    @staticmethod
    def from_dict(d: dict) -> "Region":
        return Region(
            key=d["key"],
            x_ratio=d["x_ratio"],
            y_ratio=d["y_ratio"],
            w_ratio=d["w_ratio"],
            h_ratio=d["h_ratio"],
        )


@dataclass
class Point:
    """单个坐标点实例（归一化中心 + 半径）"""
    key: str
    cx_ratio: float
    cy_ratio: float
    r_ratio: float = 0.015

    def to_dict(self) -> dict:
        return asdict(self)

    def to_coord_ref(self) -> CircleCoordRef:
        """转换为 CircleCoordRef（已是中心点）"""
        return CircleCoordRef(cx=self.cx_ratio, cy=self.cy_ratio, r=self.r_ratio)

    @staticmethod
    def from_dict(d: dict) -> "Point":
        return Point(
            key=d["key"],
            cx_ratio=d["cx_ratio"],
            cy_ratio=d["cy_ratio"],
            r_ratio=d.get("r_ratio", 0.015),
        )


@dataclass
class Arrow:
    """单个方向实例（从 from point 指向终点）

    终点互斥二态：
    - 吸附态：to_key 非空，终点绑定到另一个 point，随其移动
    - 绝对态：to_cx_ratio/to_cy_ratio 非空，终点为固定归一化坐标
    """
    key: str
    from_key: str
    to_key: str | None = None
    to_cx_ratio: float | None = None
    to_cy_ratio: float | None = None

    def to_dict(self) -> dict:
        d: dict = {"key": self.key, "from_key": self.from_key}
        if self.to_key is not None:
            d["to_key"] = self.to_key
        else:
            d["to_cx_ratio"] = self.to_cx_ratio
            d["to_cy_ratio"] = self.to_cy_ratio
        return d

    @staticmethod
    def from_dict(d: dict) -> "Arrow":
        return Arrow(
            key=d["key"],
            from_key=d["from_key"],
            to_key=d.get("to_key"),
            to_cx_ratio=d.get("to_cx_ratio"),
            to_cy_ratio=d.get("to_cy_ratio"),
        )


@dataclass
class Panel:
    """单个 panel 实例（归一化矩形区域 + 声明式网格参数）

    与 Region 类似，panel 在布局级别绑定一个矩形区域；
    额外携带 cols/rows，用于运行时图像自校准。
    span（间距）由校准算法自动检测，无需手动指定。
    min_visible 控制行计入有效的最小可见比例（0.5-1.0，默认 0.95）：
    调低可减少滚动半截行导致的少检一行，但必须 > 0.5 保证行中心可点击。

    calibration 校准模式：
    - "image"：仅图像检测，失败返回 None（当前默认行为）
    - "even"：跳过图像检测，直接按 rows/cols 等分 panel 区域
    - "auto"：先尝试图像检测，失败时降级为等分（默认值）

    scroll_direction 滚动方向：
    - "vertical"：纵向滚动（默认），rows 允许 expected-1
    - "horizontal"：横向滚动，cols 允许 expected-1
    - "both"：双向滚动，rows/cols 都允许 expected-1
    - "none"：固定网格，rows/cols 必须精确匹配
    约束：rows=1 时禁止 vertical/both，cols=1 时禁止 horizontal/both
    """
    key: str
    x_ratio: float
    y_ratio: float
    w_ratio: float
    h_ratio: float
    cols: int = 6
    rows: int = 3
    min_visible: float = 0.95
    calibration: str = "auto"  # "image" | "even" | "auto"
    scroll_direction: str = "vertical"  # "vertical" | "horizontal" | "both" | "none"

    _VALID_SCROLL_DIRECTIONS = ("vertical", "horizontal", "both", "none")

    def __post_init__(self):
        if self.scroll_direction not in self._VALID_SCROLL_DIRECTIONS:
            raise ValueError(
                f"scroll_direction 必须为 {self._VALID_SCROLL_DIRECTIONS} 之一，"
                f"got {self.scroll_direction!r}"
            )
        if self.rows == 1 and self.scroll_direction in ("vertical", "both"):
            raise ValueError(
                f"rows=1 时 scroll_direction 不能为 {self.scroll_direction!r}（无内容可纵向滚动）"
            )
        if self.cols == 1 and self.scroll_direction in ("horizontal", "both"):
            raise ValueError(
                f"cols=1 时 scroll_direction 不能为 {self.scroll_direction!r}（无内容可横向滚动）"
            )

    def to_dict(self) -> dict:
        return asdict(self)

    def to_coord_ref(self) -> RectCoordRef:
        """转换为 RectCoordRef（左上角 → 中心点）"""
        cx = self.x_ratio + self.w_ratio / 2
        cy = self.y_ratio + self.h_ratio / 2
        return RectCoordRef(cx=cx, cy=cy, w=self.w_ratio, h=self.h_ratio)

    @staticmethod
    def from_dict(d: dict) -> "Panel":
        return Panel(
            key=d["key"],
            x_ratio=d["x_ratio"],
            y_ratio=d["y_ratio"],
            w_ratio=d["w_ratio"],
            h_ratio=d["h_ratio"],
            cols=int(d.get("cols", 6)),
            rows=int(d.get("rows", 3)),
            min_visible=float(d.get("min_visible", 0.95)),
            calibration=str(d.get("calibration", "auto")),
            scroll_direction=str(d.get("scroll_direction", "vertical")),
        )


@dataclass
class Layout:
    """一个布局：包含画布配置 + 所有场景的区域定义"""
    name: str = ""
    canvas: CanvasConfig = field(default_factory=CanvasConfig)
    scenes: dict[str, list[Region]] = field(default_factory=dict)
    points: dict[str, list[Point]] = field(default_factory=dict)
    arrows: dict[str, list[Arrow]] = field(default_factory=dict)
    panels: dict[str, list[Panel]] = field(default_factory=dict)
    # scenes = {"equip_detail": [Region, ...], "equip_tune": [Region, ...]}

    def get_scene_regions(self, scene_key: str) -> list[Region]:
        return self.scenes.get(scene_key, [])

    def set_scene_regions(self, scene_key: str, regions: list[Region]):
        self.scenes[scene_key] = regions

    def get_scene_points(self, scene_key: str) -> list[Point]:
        return self.points.get(scene_key, [])

    def set_scene_points(self, scene_key: str, points: list[Point]):
        self.points[scene_key] = points

    def get_scene_arrows(self, scene_key: str) -> list[Arrow]:
        return self.arrows.get(scene_key, [])

    def set_scene_arrows(self, scene_key: str, arrows: list[Arrow]):
        self.arrows[scene_key] = arrows

    def get_scene_panels(self, scene_key: str) -> list[Panel]:
        return self.panels.get(scene_key, [])

    def set_scene_panels(self, scene_key: str, panels: list[Panel]):
        self.panels[scene_key] = panels

    def get_canvas(self) -> CanvasConfig:
        return self.canvas

    def set_canvas(self, canvas: CanvasConfig):
        self.canvas = canvas

    def to_dict(self) -> dict:
        # 汇总所有出现过的场景 key
        scene_keys = set(self.scenes) | set(self.points) | set(self.arrows) | set(self.panels)
        scenes_out: dict[str, dict] = {}
        for sk in scene_keys:
            entry: dict = {}
            regions = self.scenes.get(sk) or []
            entry["regions"] = [r.to_dict() for r in regions]
            pts = self.points.get(sk) or []
            if pts:
                entry["points"] = [p.to_dict() for p in pts]
            arrs = self.arrows.get(sk) or []
            if arrs:
                entry["arrows"] = [a.to_dict() for a in arrs]
            pnls = self.panels.get(sk) or []
            if pnls:
                entry["panels"] = [p.to_dict() for p in pnls]
            scenes_out[sk] = entry
        return {
            "canvas": self.canvas.to_dict(),
            "scenes": scenes_out,
        }

    @staticmethod
    def from_dict(name: str, d: dict) -> "Layout":
        # 解析 canvas
        canvas = CanvasConfig()
        if "canvas" in d and isinstance(d["canvas"], dict):
            canvas = CanvasConfig.from_dict(d["canvas"])
        # 解析各场景 regions / points / arrows / panels
        scenes: dict[str, list[Region]] = {}
        points: dict[str, list[Point]] = {}
        arrows: dict[str, list[Arrow]] = {}
        panels: dict[str, list[Panel]] = {}

        def _parse_scene_entry(scene_key: str, scene_data: dict):
            if "regions" in scene_data:
                scenes[scene_key] = [Region.from_dict(r) for r in scene_data["regions"]]
            if "points" in scene_data:
                points[scene_key] = [Point.from_dict(p) for p in scene_data["points"]]
            if "arrows" in scene_data:
                arrows[scene_key] = [Arrow.from_dict(a) for a in scene_data["arrows"]]
            if "panels" in scene_data:
                panels[scene_key] = [Panel.from_dict(p) for p in scene_data["panels"]]

        scenes_data = d.get("scenes", {})
        if isinstance(scenes_data, dict):
            for scene_key, scene_data in scenes_data.items():
                if isinstance(scene_data, dict):
                    _parse_scene_entry(scene_key, scene_data)
        return Layout(name=name, canvas=canvas, scenes=scenes, points=points, arrows=arrows, panels=panels)
