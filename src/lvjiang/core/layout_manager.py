"""布局配置管理器 - 布局 CRUD、截图管理、窗口标题配置"""

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


# ─── 布局配置管理器 ──────────────────────────────────────

class LayoutConfigManager:
    """管理布局配置的持久化

    布局文件读写经 ConfigResolver（开发→system/layouts，用户→local/layouts）；
    名册由文件系统枚举派生，session.json 只记 active_layout。
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

    @staticmethod
    def _layout_rel(name: str) -> str:
        return f"layouts/{_safe_name(name)}.json"

    # ─── 布局 CRUD ──────────────────────────────────────

    def list_layouts(self) -> list[str]:
        """返回布局列表（文件系统枚举派生，system ∪ local，排序）"""
        names = get_resolver().enumerate_entities("layouts", "*.json")
        return [Path(n).stem for n in names]

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
        path = get_resolver().resolve_read(self._layout_rel(name))
        if path is None:
            logger.warning(f"布局文件不存在: {name}")
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
        get_resolver().write_entity(
            self._layout_rel(layout.name),
            json.dumps(layout.to_dict(), ensure_ascii=False, indent=2),
        )
        logger.info(f"布局已保存: {layout.name}")

    def delete_layout(self, name: str) -> bool:
        resolver = get_resolver()
        rel = self._layout_rel(name)
        if resolver.resolve_read(rel) is None:
            return False
        resolver.delete_entity(rel)
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
        return self._config.get("active_layout", "")

    def set_active_layout(self, name: str):
        self._config["active_layout"] = name
        self._save_config()

    def get_active_layout(self) -> "Layout | None":
        name = self.get_active_layout_name()
        if not name:
            return None
        return self.load_layout(name)

