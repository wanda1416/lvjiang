"""布局配置管理器 - 布局 CRUD、截图管理、窗口标题配置"""

import json
import shutil
from pathlib import Path

import numpy as np
from loguru import logger

from ..constants import LOCAL_CONFIG_DIR, PREFERENCES_PATH
from .scene_registry import Layout, get_scene_name


# ─── 路径常量 ────────────────────────────────────────────

LAYOUTS_DIR = LOCAL_CONFIG_DIR / "layouts"
SCREENSHOTS_DIR = LOCAL_CONFIG_DIR / "screenshots"


def _safe_name(name: str) -> str:
    """将名称转为文件系统安全的字符串"""
    return "".join(c if c.isalnum() or c in "-_ " else "_" for c in name)


def layout_screenshots_dir(layout_name: str) -> Path:
    """获取布局的截图目录"""
    return SCREENSHOTS_DIR / _safe_name(layout_name)


# ─── 截图管理 ────────────────────────────────────────────

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


# ─── 布局配置管理器 ──────────────────────────────────────

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
        from ..constants import SESSION_PATH
        if SESSION_PATH.exists():
            try:
                return json.loads(SESSION_PATH.read_text(encoding="utf-8"))
            except Exception as e:
                logger.error(f"加载 session.json 失败: {e}")
        return {"active_layout": "", "layouts": []}

    def _save_config(self):
        from ..constants import SESSION_PATH
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
        from .scene_registry import SCENE_REGIONS
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

