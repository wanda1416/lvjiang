"""POI 区域配置 - 布局→场景 层级结构 + 相对比例坐标 + JSON 持久化"""

import json
import shutil
from dataclasses import dataclass, field, asdict
from pathlib import Path

import numpy as np
from loguru import logger

from ..constants import CONFIG_DIR, LOCAL_CONFIG_DIR, SYSTEM_SCENES_DIR, APP_CONFIG_PATH, PREFERENCES_PATH, SESSION_PATH
from .scene_loader import SceneRegistry, RegionDef, PointDef, SceneDef, FieldDef


def _load_scene_order() -> list[str] | None:
    """从 app.yaml 读取场景加载顺序"""
    if not APP_CONFIG_PATH.exists():
        return None
    try:
        import yaml
        data = yaml.safe_load(APP_CONFIG_PATH.read_text(encoding="utf-8"))
        order = data.get("layout_scenes") if isinstance(data, dict) else None
        return order if isinstance(order, list) else None
    except Exception as e:
        logger.warning(f"读取 layout_scenes 失败: {e}")
        return None


# ─── 场景注册表（从 YAML 加载） ─────────────────────────

_registry = SceneRegistry(SYSTEM_SCENES_DIR, scene_order=_load_scene_order())

# 场景 → (场景中文名, [(region_key, region_name), ...])
SCENE_REGIONS: dict[str, tuple[str, list[tuple[str, str]]]] = {
    key: (scene.name, [(r.key, r.name) for r in scene.regions])
    for key, scene in _registry.all_scenes().items()
}

# 向后兼容别名
FIELD_GROUPS = SCENE_REGIONS

_wpn = _registry.get_scene("equip_weapon_detail")
EQUIP_REGIONS = [(r.key, r.name) for r in _wpn.regions] if _wpn else []
# 向后兼容别名
EQUIP_FIELDS = EQUIP_REGIONS

# 场景 → [(point_key, point_name), ...]（来自 YAML 类型定义）
SCENE_POINTS: dict[str, list[tuple[str, str]]] = {
    key: [(p.key, p.name) for p in scene.points]
    for key, scene in _registry.all_scenes().items()
}


def get_scene_name(scene_key: str) -> str:
    if scene_key in SCENE_REGIONS:
        return SCENE_REGIONS[scene_key][0]
    return scene_key


def get_scene_regions(scene_key: str) -> list[tuple[str, str]]:
    """获取场景的 (key, name) 区域列表"""
    if scene_key in SCENE_REGIONS:
        return SCENE_REGIONS[scene_key][1]
    return []


# 向后兼容别名
get_scene_fields = get_scene_regions


def get_button_regions(scene_key: str) -> set[str]:
    """获取场景的纯功能按钮区域集合（is_clickable 且非 is_text）"""
    scene = _registry.get_scene(scene_key)
    if not scene:
        return set()
    return {r.key for r in scene.regions if r.is_clickable and not r.is_text}


# 向后兼容别名
get_button_fields = get_button_regions


def get_region_defs(scene_key: str) -> list[RegionDef]:
    """获取场景的完整区域定义列表"""
    scene = _registry.get_scene(scene_key)
    if not scene:
        return []
    return list(scene.regions)


# 向后兼容别名
get_field_defs = get_region_defs


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


# ─── 路径常量 ────────────────────────────────────────────

LAYOUTS_DIR = LOCAL_CONFIG_DIR / "layouts"
SCREENSHOTS_DIR = LOCAL_CONFIG_DIR / "screenshots"


def _safe_name(name: str) -> str:
    """将名称转为文件系统安全的字符串"""
    return "".join(c if c.isalnum() or c in "-_ " else "_" for c in name)


def layout_screenshots_dir(layout_name: str) -> Path:
    """获取布局的截图目录"""
    return SCREENSHOTS_DIR / _safe_name(layout_name)


def load_scene_screenshot(layout_name: str, scene_key: str) -> np.ndarray | None:
    """读取布局下某场景的截图，不存在返回 None（支持中文路径）"""
    path = layout_screenshots_dir(layout_name) / f"{scene_key}.png"
    if not path.exists():
        return None
    try:
        import cv2
        # cv2.imread 不支持中文路径，用 np.fromfile + imdecode
        data = path.read_bytes()
        buf = np.frombuffer(data, dtype=np.uint8)
        img = cv2.imdecode(buf, cv2.IMREAD_UNCHANGED)
        if img is not None:
            # BGR -> RGB
            if len(img.shape) == 3 and img.shape[2] >= 3:
                img = img[:, :, :3][:, :, ::-1]
        return img
    except Exception as e:
        logger.error(f"读取截图失败 {path}: {e}")
        return None


def save_scene_screenshot(layout_name: str, scene_key: str, image: np.ndarray):
    """保存场景截图（支持中文路径）"""
    import cv2
    d = layout_screenshots_dir(layout_name)
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{scene_key}.png"
    # RGB -> BGR
    if len(image.shape) == 3 and image.shape[2] >= 3:
        image = image[:, :, :3][:, :, ::-1]
    # cv2.imwrite 不支持中文路径，用 imencode + 文件写入
    success, buf = cv2.imencode('.png', image)
    if success:
        path.write_bytes(buf.tobytes())
        logger.info(f"截图已保存: {path}")
    else:
        logger.error(f"截图编码失败: {path}")


def copy_screenshots(src_layout: str, dst_layout: str):
    """复制整个截图目录（另存为时用）"""
    src = layout_screenshots_dir(src_layout)
    dst = layout_screenshots_dir(dst_layout)
    if not src.exists():
        return
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)
    logger.info(f"截图已复制: {src_layout} -> {dst_layout}")


def delete_screenshots(layout_name: str):
    """删除布局的截图目录"""
    d = layout_screenshots_dir(layout_name)
    if d.exists():
        shutil.rmtree(d)
        logger.info(f"截图目录已删除: {d}")


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
    """单个区域定义（相对比例坐标）"""
    key: str
    name: str
    x_ratio: float
    y_ratio: float
    w_ratio: float
    h_ratio: float

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "Region":
        return Region(**d)


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
class Layout:
    """一个布局：包含画布配置 + 所有场景的区域定义"""
    name: str = ""
    canvas: CanvasConfig = field(default_factory=CanvasConfig)
    scenes: dict[str, list[Region]] = field(default_factory=dict)
    points: dict[str, list[Point]] = field(default_factory=dict)
    arrows: dict[str, list[Arrow]] = field(default_factory=dict)
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

    def get_canvas(self) -> CanvasConfig:
        return self.canvas

    def set_canvas(self, canvas: CanvasConfig):
        self.canvas = canvas

    def to_dict(self) -> dict:
        # 汇总所有出现过的场景 key
        scene_keys = set(self.scenes) | set(self.points) | set(self.arrows)
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
            scenes_out[sk] = entry
        return {
            "canvas": self.canvas.to_dict(),
            "scenes": scenes_out,
        }

    @staticmethod
    def from_dict(name: str, d: dict) -> "Layout":
        # 解析 canvas（可选，向后兼容）
        canvas = CanvasConfig()
        if "canvas" in d and isinstance(d["canvas"], dict):
            canvas = CanvasConfig.from_dict(d["canvas"])
        # 解析各场景 regions / points / arrows
        scenes: dict[str, list[Region]] = {}
        points: dict[str, list[Point]] = {}
        arrows: dict[str, list[Arrow]] = {}

        def _parse_scene_entry(scene_key: str, scene_data: dict):
            if "regions" in scene_data:
                scenes[scene_key] = [Region.from_dict(r) for r in scene_data["regions"]]
            if "points" in scene_data:
                points[scene_key] = [Point.from_dict(p) for p in scene_data["points"]]
            if "arrows" in scene_data:
                arrows[scene_key] = [Arrow.from_dict(a) for a in scene_data["arrows"]]

        if "scenes" in d and isinstance(d["scenes"], dict):
            # 新格式：scenes 包裹
            for scene_key, scene_data in d["scenes"].items():
                if isinstance(scene_data, dict):
                    _parse_scene_entry(scene_key, scene_data)
        else:
            # 旧格式：场景直接在顶层（向后兼容）
            for scene_key, scene_data in d.items():
                if scene_key == "canvas":
                    continue
                if isinstance(scene_data, dict):
                    _parse_scene_entry(scene_key, scene_data)
        return Layout(name=name, canvas=canvas, scenes=scenes, points=points, arrows=arrows)


# ─── 管理器 ──────────────────────────────────────────────

class LayoutConfigManager:
    """管理布局配置的持久化"""

    def __init__(self):
        LAYOUTS_DIR.mkdir(parents=True, exist_ok=True)
        self._config = self._load_config()
        # 确保 layouts 数组存在
        if "layouts" not in self._config:
            self._config["layouts"] = []
            self._save_config()

    def _load_config(self) -> dict:
        if SESSION_PATH.exists():
            try:
                return json.loads(SESSION_PATH.read_text(encoding="utf-8"))
            except Exception as e:
                logger.error(f"加载 session.json 失败: {e}")
        return {"active_layout": "", "layouts": []}

    def _save_config(self):
        LOCAL_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        SESSION_PATH.write_text(
            json.dumps(self._config, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _reload_config(self):
        """从文件重新加载配置（多实例同步）"""
        self._config = self._load_config()
        if "layouts" not in self._config:
            self._config["layouts"] = []

    def _layout_path(self, name: str) -> Path:
        safe = "".join(c if c.isalnum() or c in "-_ " else "_" for c in name)
        return LAYOUTS_DIR / f"{safe}.json"

    # ─── 布局 CRUD ──────────────────────────────────────

    def _ensure_registered(self, name: str):
        """确保布局名称在 config.json 的 layouts 数组中"""
        layouts = self._config.setdefault("layouts", [])
        if name not in layouts:
            layouts.append(name)
            self._save_config()

    def list_layouts(self) -> list[str]:
        """返回布局列表（按 config.json 中的顺序，每次从文件读取）"""
        self._reload_config()
        return list(self._config.get("layouts", []))

    def new_layout(self, name: str) -> Layout:
        """创建空布局（所有场景初始为空 regions）"""
        layout = Layout(name=name)
        for scene_key in SCENE_REGIONS:
            layout.scenes[scene_key] = []
        self.save_layout(layout)
        self.set_active_layout(name)
        logger.info(f"布局已新建: {name}")
        return layout

    def load_layout(self, name: str) -> "Layout | None":
        path = self._layout_path(name)
        if not path.exists():
            logger.warning(f"布局文件不存在: {path}")
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            layout = Layout.from_dict(name, data)
            logger.info(f"布局已加载: {name}")
            return layout
        except Exception as e:
            logger.error(f"加载布局失败: {e}")
            return None

    def save_layout(self, layout: Layout):
        path = self._layout_path(layout.name)
        path.write_text(
            json.dumps(layout.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self._ensure_registered(layout.name)
        logger.info(f"布局已保存: {layout.name}")

    def delete_layout(self, name: str) -> bool:
        path = self._layout_path(name)
        if not path.exists():
            return False
        path.unlink()
        # 从 layouts 数组移除
        if name in self._config["layouts"]:
            self._config["layouts"].remove(name)
            self._save_config()
        if self._config.get("active_layout") == name:
            self._config["active_layout"] = ""
            self._save_config()
        logger.info(f"布局已删除: {name}")
        return True

    def check_scenes_valid(self, name: str, scene_keys: list[str]) -> list[str]:
        """检查指定场景是否已绑定区域，返回缺失场景的名称列表"""
        layout = self.load_layout(name)
        if not layout:
            return [get_scene_name(k) for k in scene_keys]
        missing = []
        for scene_key in scene_keys:
            regions = layout.scenes.get(scene_key, [])
            if not regions:
                missing.append(get_scene_name(scene_key))
        return missing

    # ─── 激活布局 ────────────────────────────────────────

    def get_active_layout_name(self) -> str:
        return self._config.get("active_layout", "")

    def set_active_layout(self, name: str):
        self._config["active_layout"] = name
        self._save_config()

    def get_active_layout(self) -> "Layout | None":
        name = self.get_active_layout_name()
        if not name:
            return None
        return self.load_layout(name)

    # ─── 窗口标题（从 preferences.yaml 读取） ────────────

    def get_window_title(self) -> str:
        """获取投屏窗口标题匹配关键字（空串表示不自动定位）"""
        try:
            import yaml
            if PREFERENCES_PATH.exists():
                data = yaml.safe_load(PREFERENCES_PATH.read_text(encoding="utf-8"))
                return data.get("window_title", "") if isinstance(data, dict) else ""
        except Exception as e:
            logger.warning(f"读取 window_title 失败: {e}")
        return ""

    def set_window_title(self, title: str):
        """保存窗口标题到 preferences.yaml"""
        try:
            import yaml
            data = {}
            if PREFERENCES_PATH.exists():
                data = yaml.safe_load(PREFERENCES_PATH.read_text(encoding="utf-8")) or {}
            data["window_title"] = title
            PREFERENCES_PATH.parent.mkdir(parents=True, exist_ok=True)
            PREFERENCES_PATH.write_text(
                yaml.dump(data, allow_unicode=True, default_flow_style=False, sort_keys=False),
                encoding="utf-8",
            )
        except Exception as e:
            logger.error(f"保存 window_title 失败: {e}")

