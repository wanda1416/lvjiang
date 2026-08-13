"""布局配置管理器 - 布局 CRUD、截图管理、窗口标题配置

存储结构（目录化）：
- layouts.yaml          名册 + canvas 内联（聚合键值，local diff 合并）
- layouts/{name}/{scene_key}.json  每场景独立文件（实体影子 + 墓碑）
"""

import json
import shutil
from pathlib import Path

import numpy as np
from loguru import logger

from ..constants import SESSION_CONFIG_DIR
from .config_resolver import get_resolver
from .scene_registry import Layout, get_scene_name

# ─── 路径常量 ────────────────────────────────────────────

# 布局截图属于运行态产出，落 session 层
SCREENSHOTS_DIR = SESSION_CONFIG_DIR / "screenshots"


def _safe_name(name: str) -> str:
    """将名称转为文件系统安全的字符串"""
    return "".join(c if c.isalnum() or c in "-_ " else "_" for c in name)


def layout_screenshots_dir(layout_name: str) -> Path:
    """获取布局的截图目录"""
    return SCREENSHOTS_DIR / _safe_name(layout_name)


def scene_screenshot_name(scene_key: str, view: str = "") -> str:
    """截图文件名：基底视图沿用场景名，其余视图加 __视图 key 后缀

    同一场景的多个视图（同一页面的不同滚动态）各自一张底图，
    否则无法在正确的背景上标定坐标。
    """
    from .scene_loader import BASE_VIEW_KEY
    if not view or view == BASE_VIEW_KEY:
        return f"{scene_key}.png"
    return f"{scene_key}__{view}.png"


# ─── 截图管理 ────────────────────────────────────────────

def load_scene_screenshot(
    layout_name: str, scene_key: str, view: str = ""
) -> np.ndarray | None:
    """读取布局下某场景（可选视图）的截图，不存在返回 None（支持中文路径）"""
    path = layout_screenshots_dir(layout_name) / scene_screenshot_name(scene_key, view)
    if not path.exists():
        return None
    try:
        import cv2
        # cv2.imread 不支持中文路径，用 np.fromfile + imdecode
        data = path.read_bytes()
        buf = np.frombuffer(data, dtype=np.uint8)
        img = cv2.imdecode(buf, cv2.IMREAD_COLOR)
        if img is None:
            return None
        # cv2.imdecode 返回 BGR，项目内部统一使用 BGR，无需翻转
        return img
    except Exception as e:
        logger.error(f"读取截图失败 {path}: {e}")
        return None


def save_scene_screenshot(
    layout_name: str, scene_key: str, image: np.ndarray, view: str = ""
):
    """保存场景（可选视图）截图（支持中文路径）

    image: BGR numpy 数组（项目内部统一使用 BGR）
    """
    import cv2
    d = layout_screenshots_dir(layout_name)
    d.mkdir(parents=True, exist_ok=True)
    path = d / scene_screenshot_name(scene_key, view)
    # image 已是 BGR，cv2.imencode 期望 BGR，无需翻转
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


# ─── 跨场景迁移 ──────────────────────────────────────────

def migrate_layout_item(layout: Layout, source: str, target: str, kind: str, key: str) -> bool:
    """将布局中指定 key 的 region/point/panel 从 source 场景迁移到 target 场景

    kind="point" 时联动处理箭头：
    - source 中 from_key == key 的箭头随点迁移到 target
    - source 中 to_key == key 的箭头烘焙为绝对坐标，避免悬空引用

    Returns:
        是否发生了改动
    """
    if kind == "region":
        src_list = layout.get_scene_regions(source)
        item = next((r for r in src_list if r.key == key), None)
        if item is None:
            return False
        layout.set_scene_regions(source, [r for r in src_list if r.key != key])
        # target 已有同 key 的陈旧项先移除
        dst_list = [r for r in layout.get_scene_regions(target) if r.key != key]
        dst_list.append(item)
        layout.set_scene_regions(target, dst_list)
        return True

    if kind == "point":
        src_list = layout.get_scene_points(source)
        item = next((p for p in src_list if p.key == key), None)
        if item is None:
            return False
        layout.set_scene_points(source, [p for p in src_list if p.key != key])
        dst_list = [p for p in layout.get_scene_points(target) if p.key != key]
        dst_list.append(item)
        layout.set_scene_points(target, dst_list)
        # 箭头联动：from_key 随迁，to_key 烘焙为绝对坐标
        src_arrows = layout.get_scene_arrows(source)
        moved = [a for a in src_arrows if a.from_key == key]
        remain = [a for a in src_arrows if a.from_key != key]
        for a in remain:
            if a.to_key == key:
                a.to_key = None
                a.to_cx_ratio = item.cx_ratio
                a.to_cy_ratio = item.cy_ratio
        layout.set_scene_arrows(source, remain)
        if moved:
            moved_keys = {a.key for a in moved}
            dst_arrows = [a for a in layout.get_scene_arrows(target) if a.key not in moved_keys]
            dst_arrows.extend(moved)
            layout.set_scene_arrows(target, dst_arrows)
        return True

    if kind == "panel":
        src_list = layout.get_scene_panels(source)
        item = next((p for p in src_list if p.key == key), None)
        if item is None:
            return False
        layout.set_scene_panels(source, [p for p in src_list if p.key != key])
        dst_list = [p for p in layout.get_scene_panels(target) if p.key != key]
        dst_list.append(item)
        layout.set_scene_panels(target, dst_list)
        return True

    raise ValueError(f"未知迁移类型: {kind}")


# ─── 布局目录辅助 ────────────────────────────────────────

_LAYOUTS_YAML_REL = "layouts.yaml"


def _layout_dir_rel(name: str) -> str:
    """布局目录相对路径：layouts/{safe_name}"""
    return f"layouts/{_safe_name(name)}"


def _scene_rel(name: str, scene_key: str) -> str:
    """场景文件相对路径：layouts/{safe_name}/{scene_key}.json"""
    return f"layouts/{_safe_name(name)}/{scene_key}.json"


def _enumerate_scene_files(name: str) -> list[str]:
    """枚举布局目录下所有场景 JSON 文件名（system ∪ local 并集，墓碑剔除）

    Returns:
        排序后的 scene_key 列表（不含 .json 后缀）
    """
    resolver = get_resolver()
    rel_dir = _layout_dir_rel(name)
    names: set[str] = set()
    for root in (resolver.system_dir, resolver.local_dir):
        base = root / rel_dir
        if not base.is_dir():
            continue
        for p in base.glob("*.json"):
            if p.is_file() and not p.name.startswith("_"):
                names.add(p.stem)
    alive = [n for n in sorted(names)
             if not (resolver.local_dir / f"{rel_dir}/{n}.json.deleted").exists()]
    return alive


def load_layout_by_name(name: str) -> Layout | None:
    """模块级布局加载（无 session 依赖，供 workflow_runner / smoke 使用）

    从 layouts.yaml 读 canvas，从 layouts/{name}/ 目录逐场景加载。
    """
    from .scene_registry import Arrow, Panel, Point, Region

    resolver = get_resolver()
    merged = resolver.load_merged(_LAYOUTS_YAML_REL)
    layouts_doc = merged.get("layouts", {})
    if name not in layouts_doc:
        # 回退：目录存在但 yaml 未登记（兼容迁移中间态）
        scene_keys = _enumerate_scene_files(name)
        if not scene_keys:
            return None
    else:
        scene_keys = _enumerate_scene_files(name)

    # canvas
    from .scene_registry import CanvasConfig
    canvas_dict = layouts_doc.get(name, {}).get("canvas", {})
    canvas = CanvasConfig.from_dict(canvas_dict) if canvas_dict else CanvasConfig()

    # 逐场景加载
    scenes: dict[str, list[Region]] = {}
    points: dict[str, list[Point]] = {}
    arrows: dict[str, list[Arrow]] = {}
    panels: dict[str, list[Panel]] = {}

    for scene_key in scene_keys:
        path = resolver.resolve_read(_scene_rel(name, scene_key))
        if path is None:
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            logger.error(f"场景文件解析失败 {path}: {e}")
            continue
        if "regions" in data:
            scenes[scene_key] = [Region.from_dict(r) for r in data["regions"]]
        if "points" in data:
            points[scene_key] = [Point.from_dict(p) for p in data["points"]]
        if "arrows" in data:
            arrows[scene_key] = [Arrow.from_dict(a) for a in data["arrows"]]
        if "panels" in data:
            panels[scene_key] = [Panel.from_dict(p) for p in data["panels"]]

    return Layout(name=name, canvas=canvas, scenes=scenes,
                  points=points, arrows=arrows, panels=panels)


# ─── 布局配置管理器 ──────────────────────────────────────

class LayoutConfigManager:
    """管理布局配置的持久化

    布局存储为目录结构：layouts.yaml（名册+canvas）+ layouts/{name}/{scene}.json；
    读写经 ConfigResolver（开发→system，用户→local 影子）；
    session.json 只记 active_layout。
    """

    def __init__(self):
        self._config = self._load_config()

    def _load_config(self) -> dict:
        from ..constants import SESSION_PATH
        if SESSION_PATH.exists():
            try:
                return json.loads(SESSION_PATH.read_text(encoding="utf-8"))
            except Exception as e:
                logger.error(f"加载 session.json 失败: {e}")
        return {"active_layout": ""}

    def _save_config(self):
        from ..constants import SESSION_PATH
        SESSION_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        SESSION_PATH.write_text(
            json.dumps(self._config, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _reload_config(self):
        """从文件重新加载配置（多实例同步）"""
        self._config = self._load_config()

    # ─── 布局 CRUD ──────────────────────────────────────

    def list_layouts(self) -> list[str]:
        """返回布局列表（layouts.yaml 名册派生，排序）"""
        resolver = get_resolver()
        merged = resolver.load_merged(_LAYOUTS_YAML_REL)
        layouts = merged.get("layouts", {})
        # yaml 存在时以名册为准（即使为空）；仅当 yaml 完全不存在时回退目录枚举
        yaml_exists = (
            (resolver.system_dir / _LAYOUTS_YAML_REL).exists()
            or (resolver.local_dir / _LAYOUTS_YAML_REL).exists()
        )
        if yaml_exists:
            return sorted(layouts.keys())
        # 回退：枚举目录（兼容迁移前）
        names: set[str] = set()
        for root in (resolver.system_dir, resolver.local_dir):
            base = root / "layouts"
            if not base.is_dir():
                continue
            for p in base.iterdir():
                if p.is_dir() and not p.name.startswith("_"):
                    names.add(p.name)
        return sorted(names)

    def new_layout(self, name: str) -> Layout:
        """创建空布局（所有场景初始为空 regions）"""
        from .scene_registry import SCENE_REGIONS
        layout = Layout(name=name)
        for scene_key in SCENE_REGIONS:
            layout.scenes[scene_key] = []
        self.save_layout(layout)
        self.set_active_layout(name)
        logger.info(f"布局已新建: {name}")
        return layout

    def load_layout(self, name: str) -> "Layout | None":
        layout = load_layout_by_name(name)
        if layout is None:
            logger.warning(f"布局不存在: {name}")
        else:
            logger.info(f"布局已加载: {name}")
        return layout

    def save_layout(self, layout: Layout, changed_scenes: set[str] | None = None):
        """保存布局

        Args:
            layout: 布局对象
            changed_scenes: 需要写盘的场景 key 集合。
                None = 全量写盘（新建布局、另存为等场景）；
                set = 增量写盘，只写指定场景的 JSON 文件。
        """
        resolver = get_resolver()

        # 1. 更新 layouts.yaml 中的 canvas 条目
        merged = resolver.load_merged(_LAYOUTS_YAML_REL)
        layouts_doc = merged.setdefault("layouts", {})
        layouts_doc[layout.name] = {"canvas": layout.canvas.to_dict()}
        resolver.save_merged(_LAYOUTS_YAML_REL, merged)

        # 2. 写场景 JSON 文件（增量或全量）
        all_scene_keys = set(layout.scenes) | set(layout.points) | set(layout.arrows) | set(layout.panels)
        scene_keys = all_scene_keys if changed_scenes is None else (changed_scenes & all_scene_keys)
        for sk in scene_keys:
            entry: dict = {}
            regions = layout.scenes.get(sk) or []
            entry["regions"] = [r.to_dict() for r in regions]
            pts = layout.points.get(sk) or []
            if pts:
                entry["points"] = [p.to_dict() for p in pts]
            arrs = layout.arrows.get(sk) or []
            if arrs:
                entry["arrows"] = [a.to_dict() for a in arrs]
            pnls = layout.panels.get(sk) or []
            if pnls:
                entry["panels"] = [p.to_dict() for p in pnls]
            resolver.write_entity(
                _scene_rel(layout.name, sk),
                json.dumps(entry, ensure_ascii=False, indent=2),
            )
        mode = f"增量 {len(scene_keys)}/{len(all_scene_keys)} 场景" if changed_scenes is not None else "全量"
        logger.info(f"布局已保存: {layout.name} ({mode})")

    def delete_layout(self, name: str) -> bool:
        resolver = get_resolver()
        # 确认布局存在
        merged = resolver.load_merged(_LAYOUTS_YAML_REL)
        layouts_doc = merged.get("layouts", {})
        scene_keys = _enumerate_scene_files(name)
        if name not in layouts_doc and not scene_keys:
            return False

        # 删除场景文件
        for sk in scene_keys:
            resolver.delete_entity(_scene_rel(name, sk))

        # 从 layouts.yaml 移除条目
        if name in layouts_doc:
            del layouts_doc[name]
            resolver.save_merged(_LAYOUTS_YAML_REL, merged)

        # 开发模式额外清理空目录
        if resolver.is_dev_mode():
            import shutil as _shutil
            dir_path = resolver.system_dir / _layout_dir_rel(name)
            if dir_path.is_dir() and not any(dir_path.iterdir()):
                _shutil.rmtree(dir_path)

        if self._config.get("active_layout") == name:
            self._config["active_layout"] = ""
            self._save_config()
        logger.info(f"布局已删除: {name}")
        return True

    def migrate_item_across_layouts(self, source: str, target: str, kind: str, key: str) -> list[str]:
        """在所有布局文件中把指定 key 的 region/point/panel 从 source 场景迁到 target 场景

        Returns:
            实际发生改动并已写盘的布局名称列表
        """
        changed = []
        for name in self.list_layouts():
            layout = self.load_layout(name)
            if layout is None:
                continue
            if migrate_layout_item(layout, source, target, kind, key):
                self.save_layout(layout)
                changed.append(name)
        if changed:
            logger.info(f"跨场景迁移 {kind}「{key}」: {source} -> {target}, 更新布局: {changed}")
        return changed

    def check_scenes_valid(self, name: str, scene_keys: list[str]) -> list[str]:
        """检查指定场景是否已绑定坐标，返回缺失场景的名称列表

        区域/坐标点/方向/面板任一非空即视为已绑定——有些场景（如通用控制）
        本身只定义坐标点与方向，不含任何区域。
        """
        layout = self.load_layout(name)
        if not layout:
            return [get_scene_name(k) for k in scene_keys]
        missing = []
        for scene_key in scene_keys:
            bound = (
                layout.get_scene_regions(scene_key)
                or layout.get_scene_points(scene_key)
                or layout.get_scene_arrows(scene_key)
                or layout.get_scene_panels(scene_key)
            )
            if not bound:
                missing.append(get_scene_name(scene_key))
        return missing

    # ─── 激活布局 ────────────────────────────────────────

    def get_active_layout_name(self) -> str:
        self._reload_config()
        name = self._config.get("active_layout", "")
        # 未指定时自动回退到第一个可用布局
        if not name:
            layouts = self.list_layouts()
            if layouts:
                name = layouts[0]
                logger.info(f"未指定 active_layout，自动使用: {name}")
        return name

    def set_active_layout(self, name: str):
        self._config["active_layout"] = name
        self._save_config()

    def get_active_layout(self) -> "Layout | None":
        name = self.get_active_layout_name()
        if not name:
            return None
        return self.load_layout(name)

