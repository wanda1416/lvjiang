"""POI 区域配置 - 布局→场景 层级结构 + 相对比例坐标 + JSON 持久化"""

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path

from loguru import logger

from ..constants import CONFIG_DIR, USER_CONFIG_DIR

# ─── 场景 & 字段组定义 ───────────────────────────────────

FIELD_GROUPS: dict[str, tuple[str, list[tuple[str, str]]]] = {
    "equip_detail": (
        "装备词条详情",
        [
            ("equip_type",  "装备类型"),
            ("equip_level", "装备等级"),
            ("base_attr",   "基础属性"),
            ("affix_gong",  "词条宫"),
            ("affix_shang", "词条商"),
            ("affix_jue",   "词条角"),
            ("affix_zhi",   "词条徵"),
            ("affix_yu",    "词条羽"),
        ],
    ),
    "equip_tune": (
        "装备调律详情",
        [
            ("affix_gong",  "词条宫"),
            ("affix_shang", "词条商"),
            ("affix_jue",   "词条角"),
            ("affix_zhi",   "词条徵"),
            ("affix_yu",    "词条羽"),
        ],
    ),
}

EQUIP_FIELDS = FIELD_GROUPS["equip_detail"][1]


def get_scene_name(scene_key: str) -> str:
    if scene_key in FIELD_GROUPS:
        return FIELD_GROUPS[scene_key][0]
    return scene_key


def get_scene_fields(scene_key: str) -> list[tuple[str, str]]:
    if scene_key in FIELD_GROUPS:
        return FIELD_GROUPS[scene_key][1]
    return []


# ─── 路径常量 ────────────────────────────────────────────

LAYOUTS_DIR = USER_CONFIG_DIR / "layouts"
CONFIG_FILE = USER_CONFIG_DIR / "config.json"


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
class Layout:
    """一个布局：包含画布配置 + 所有场景的区域定义"""
    name: str = ""
    canvas: CanvasConfig = field(default_factory=CanvasConfig)
    scenes: dict[str, list[Region]] = field(default_factory=dict)
    # scenes = {"equip_detail": [Region, ...], "equip_tune": [Region, ...]}

    def get_scene_regions(self, scene_key: str) -> list[Region]:
        return self.scenes.get(scene_key, [])

    def set_scene_regions(self, scene_key: str, regions: list[Region]):
        self.scenes[scene_key] = regions

    def get_canvas(self) -> CanvasConfig:
        return self.canvas

    def set_canvas(self, canvas: CanvasConfig):
        self.canvas = canvas

    def to_dict(self) -> dict:
        result = {"canvas": self.canvas.to_dict()}
        for scene, regions in self.scenes.items():
            result[scene] = {"regions": [r.to_dict() for r in regions]}
        return result

    @staticmethod
    def from_dict(name: str, d: dict) -> "Layout":
        # 解析 canvas（可选，向后兼容）
        canvas = CanvasConfig()
        if "canvas" in d and isinstance(d["canvas"], dict):
            canvas = CanvasConfig.from_dict(d["canvas"])
        # 解析各场景 regions
        scenes = {}
        for scene_key, scene_data in d.items():
            if scene_key == "canvas":
                continue
            if isinstance(scene_data, dict) and "regions" in scene_data:
                scenes[scene_key] = [
                    Region.from_dict(r) for r in scene_data["regions"]
                ]
        return Layout(name=name, canvas=canvas, scenes=scenes)


# ─── 管理器 ──────────────────────────────────────────────

class LayoutConfigManager:
    """管理布局配置的持久化"""

    def __init__(self):
        LAYOUTS_DIR.mkdir(parents=True, exist_ok=True)
        self._config = self._load_config()

    def _load_config(self) -> dict:
        if CONFIG_FILE.exists():
            try:
                return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            except Exception as e:
                logger.error(f"加载 config.json 失败: {e}")
        return {"active_layout": ""}

    def _save_config(self):
        USER_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(
            json.dumps(self._config, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _layout_path(self, name: str) -> Path:
        safe = "".join(c if c.isalnum() or c in "-_ " else "_" for c in name)
        return LAYOUTS_DIR / f"{safe}.json"

    # ─── 布局 CRUD ──────────────────────────────────────

    def list_layouts(self) -> list[str]:
        names = []
        for p in sorted(LAYOUTS_DIR.glob("*.json")):
            names.append(p.stem)
        return names

    def new_layout(self, name: str) -> Layout:
        """创建空布局（所有场景初始为空 regions）"""
        layout = Layout(name=name)
        for scene_key in FIELD_GROUPS:
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
        logger.info(f"布局已保存: {layout.name}")

    def delete_layout(self, name: str) -> bool:
        path = self._layout_path(name)
        if not path.exists():
            return False
        path.unlink()
        if self._config.get("active_layout") == name:
            self._config["active_layout"] = ""
            self._save_config()
        logger.info(f"布局已删除: {name}")
        return True

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

