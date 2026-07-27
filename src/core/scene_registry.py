"""POI 区域配置 - 数据模型 + 场景注册表全局函数"""

from dataclasses import dataclass, field, asdict
from pathlib import Path

from loguru import logger

from ..constants import SYSTEM_SCENES_DIR, SCENES_CONFIG_PATH
from .scene_loader import SceneRegistry, RegionDef, PointDef, PanelDef, SceneDef


def _load_scene_order() -> list[str] | None:
    """从 scenes.yaml 读取场景加载顺序"""
    if not SCENES_CONFIG_PATH.exists():
        return None
    try:
        import yaml
        data = yaml.safe_load(SCENES_CONFIG_PATH.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return None
        layout_scenes = data.get("layout_scenes")
        if isinstance(layout_scenes, dict):
            # 从分组结构中提取场景顺序
            order = []
            for scenes in layout_scenes.values():
                order.extend(scenes)
            return order
        return None
    except Exception as e:
        logger.warning(f"读取 layout_scenes 失败: {e}")
        return None


def _load_group_config() -> tuple[dict[str, list[str]] | None, dict[str, str] | None]:
    """从 scenes.yaml 读取分组配置，返回 (group_config, group_names)"""
    if not SCENES_CONFIG_PATH.exists():
        return None, None
    try:
        import yaml
        data = yaml.safe_load(SCENES_CONFIG_PATH.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return None, None
        layout_scenes = data.get("layout_scenes")
        # 新格式：dict of groups
        if isinstance(layout_scenes, dict):
            group_names = data.get("group_names")
            if not isinstance(group_names, dict):
                group_names = None
            return layout_scenes, group_names
        return None, None
    except Exception as e:
        logger.warning(f"读取分组配置失败: {e}")
        return None, None


# ─── 场景注册表（从 YAML 加载） ─────────────────────────

_scene_order = _load_scene_order()
_group_config, _group_names = _load_group_config()
_registry = SceneRegistry(
    SYSTEM_SCENES_DIR,
    scene_order=_scene_order,
    group_config=_group_config,
    group_names=_group_names,
)

# 场景 → (场景中文名, [(region_key, region_name), ...])
SCENE_REGIONS: dict[str, tuple[str, list[tuple[str, str]]]] = {}

# 场景 → [(point_key, point_name), ...]（来自 YAML 类型定义）
SCENE_POINTS: dict[str, list[tuple[str, str]]] = {}

# 场景 → [(panel_key, panel_name), ...]（来自 YAML 类型定义）
SCENE_PANELS: dict[str, list[tuple[str, str]]] = {}

# 分组缓存
SCENE_GROUPS_META: dict[str, str] = {}   # group_key -> group_name
GROUP_SCENES: dict[str, list[str]] = {}  # group_key -> [scene_key, ...]
GROUP_ORDER: list[str] = []              # 分组顺序


def _rebuild_scene_globals():
    """从 _registry 重建 SCENE_REGIONS / SCENE_POINTS / SCENE_PANELS / 分组缓存等全局字典"""
    global SCENE_REGIONS, SCENE_POINTS, SCENE_PANELS
    global SCENE_GROUPS_META, GROUP_SCENES, GROUP_ORDER
    SCENE_REGIONS.clear()
    SCENE_REGIONS.update({
        key: (scene.name, [(r.key, r.name) for r in scene.regions])
        for key, scene in _registry.all_scenes().items()
    })
    SCENE_POINTS.clear()
    SCENE_POINTS.update({
        key: [(p.key, p.name) for p in scene.points]
        for key, scene in _registry.all_scenes().items()
    })
    SCENE_PANELS.clear()
    SCENE_PANELS.update({
        key: [(p.key, p.name) for p in scene.panels]
        for key, scene in _registry.all_scenes().items()
    })
    # 重建分组缓存
    SCENE_GROUPS_META.clear()
    GROUP_SCENES.clear()
    GROUP_ORDER.clear()
    for gk, gname in _registry.get_groups():
        SCENE_GROUPS_META[gk] = gname
        GROUP_SCENES[gk] = _registry.get_group_scenes(gk)
        GROUP_ORDER.append(gk)


# 初始化
_rebuild_scene_globals()

# 启动校验：至少存在一个分组
if not _registry.get_groups():
    raise RuntimeError("scenes.yaml 必须至少包含一个场景分组，请检查配置")


def get_registry() -> SceneRegistry:
    """获取场景注册表实例（供 UI 层调用 CRUD 方法）"""
    return _registry


def reload_scene_registry():
    """重新加载场景注册表（场景/分组增删后调用）"""
    global _registry
    scene_order = _load_scene_order()
    group_config, group_names = _load_group_config()
    _registry = SceneRegistry(
        SYSTEM_SCENES_DIR,
        scene_order=scene_order,
        group_config=group_config,
        group_names=group_names,
    )
    _rebuild_scene_globals()


def sync_group_cache():
    """仅刷新分组缓存（分组变更后调用）"""
    global SCENE_GROUPS_META, GROUP_SCENES, GROUP_ORDER
    SCENE_GROUPS_META.clear()
    GROUP_SCENES.clear()
    GROUP_ORDER.clear()
    for gk, gname in _registry.get_groups():
        SCENE_GROUPS_META[gk] = gname
        GROUP_SCENES[gk] = _registry.get_group_scenes(gk)
        GROUP_ORDER.append(gk)


def get_group_name(group_key: str) -> str:
    """获取分组名称"""
    return SCENE_GROUPS_META.get(group_key, group_key)


def get_scene_group(scene_key: str) -> str | None:
    """获取场景所在分组 key"""
    for gk, scenes in GROUP_SCENES.items():
        if scene_key in scenes:
            return gk
    return None


def sync_scene_cache(scene_key: str):
    """仅刷新单个场景的全局缓存（区域/坐标/面板编辑后调用，避免全量重载）"""
    global SCENE_REGIONS, SCENE_POINTS, SCENE_PANELS
    scene = _registry.get_scene(scene_key)
    if scene:
        SCENE_REGIONS[scene_key] = (
            scene.name,
            [(r.key, r.name) for r in scene.regions],
        )
        SCENE_POINTS[scene_key] = [(p.key, p.name) for p in scene.points]
        SCENE_PANELS[scene_key] = [(p.key, p.name) for p in scene.panels]
    else:
        SCENE_REGIONS.pop(scene_key, None)
        SCENE_POINTS.pop(scene_key, None)
        SCENE_PANELS.pop(scene_key, None)


def get_scene_name(scene_key: str) -> str:
    if scene_key in SCENE_REGIONS:
        return SCENE_REGIONS[scene_key][0]
    return scene_key


def get_scene_regions(scene_key: str) -> list[tuple[str, str]]:
    """获取场景的 (key, name) 区域列表"""
    if scene_key in SCENE_REGIONS:
        return SCENE_REGIONS[scene_key][1]
    return []


def get_button_regions(scene_key: str) -> set[str]:
    """获取场景的纯功能按钮区域集合（is_clickable 且非 is_text）"""
    scene = _registry.get_scene(scene_key)
    if not scene:
        return set()
    return {r.key for r in scene.regions if r.is_clickable and not r.is_text}


def get_region_name(scene_key: str, region_key: str) -> str:
    """通过 scene_key + region_key 查找区域中文名"""
    scene = _registry.get_scene(scene_key)
    if scene:
        for r in scene.regions:
            if r.key == region_key:
                return r.name
    return region_key


def get_region_defs(scene_key: str) -> list[RegionDef]:
    """获取场景的完整区域定义列表"""
    scene = _registry.get_scene(scene_key)
    if not scene:
        return []
    return list(scene.regions)


def get_scene_point_pairs(scene_key: str) -> list[tuple[str, str]]:
    """获取场景的 (key, name) 坐标点列表（来自 YAML 定义）"""
    return SCENE_POINTS.get(scene_key, [])


def get_point_defs(scene_key: str) -> list[PointDef]:
    """获取场景的完整坐标点定义列表"""
    scene = _registry.get_scene(scene_key)
    if not scene:
        return []
    return list(scene.points)


def get_point_def(scene_key: str, point_key: str) -> PointDef | None:
    """获取场景内指定 point 的类型定义"""
    scene = _registry.get_scene(scene_key)
    if not scene:
        return None
    return next((p for p in scene.points if p.key == point_key), None)


def get_scene_panel_pairs(scene_key: str) -> list[tuple[str, str]]:
    """获取场景的 (key, name) 面板列表（来自 YAML 定义）"""
    return SCENE_PANELS.get(scene_key, [])


def get_panel_defs(scene_key: str) -> list[PanelDef]:
    """获取场景的完整面板定义列表"""
    scene = _registry.get_scene(scene_key)
    if not scene:
        return []
    return list(scene.panels)


def get_panel_def(scene_key: str, panel_key: str) -> PanelDef | None:
    """获取场景内指定 panel 的类型定义"""
    scene = _registry.get_scene(scene_key)
    if not scene:
        return None
    return next((p for p in scene.panels if p.key == panel_key), None)


# ─── 数据类 ──────────────────────────────────────────────

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
    """
    key: str
    x_ratio: float
    y_ratio: float
    w_ratio: float
    h_ratio: float
    cols: int = 6
    rows: int = 3
    min_visible: float = 0.95

    def to_dict(self) -> dict:
        return asdict(self)

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
