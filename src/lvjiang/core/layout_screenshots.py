"""布局截图资源读写、格式偏好与重命名。"""

import json
import re
import shutil
import uuid
from pathlib import Path

import numpy as np
from loguru import logger

from ..constants import SESSION_CONFIG_DIR
from .layout_config import (
    validate_layout_key,
)

# ─── 路径常量 ────────────────────────────────────────────

# 布局截图属于运行态产出，落 session 层
SCREENSHOTS_DIR = SESSION_CONFIG_DIR / "screenshots"
_SCREENSHOT_EXTENSIONS = ("webp", "png")
_WEBP_QUALITY = 90


def rename_view_screenshots(scene_key: str, old_view_key: str, new_view_key: str):
    """重命名所有布局下某视图的截图文件（含多截图 __N 后缀）

    截图命名规则：
    - 基底视图 (view="" 或 view="base"): {scene_key}.png/.webp
    - 其他视图: {scene_key}__{view_key}.png/.webp（含多截图后缀）
    """
    if old_view_key == new_view_key:
        return
    screenshots_base = SCREENSHOTS_DIR
    if not screenshots_base.exists():
        return
    old_stem = _screenshot_base_stem(scene_key, old_view_key)
    new_stem = _screenshot_base_stem(scene_key, new_view_key)
    pattern = re.compile(re.escape(old_stem) + r"(__\d+)?\.(png|webp)$")
    for layout_dir in screenshots_base.iterdir():
        if not layout_dir.is_dir():
            continue
        for image_path in list(layout_dir.iterdir()):
            m = pattern.match(image_path.name)
            if not m:
                continue
            suffix = m.group(1) or ""  # "" or "__N"
            new_name = f"{new_stem}{suffix}.{m.group(2)}"
            if new_name != image_path.name:
                image_path.rename(layout_dir / new_name)
                logger.info(f"视图截图已重命名: {image_path.name} -> {new_name}")
        meta = _load_screenshot_meta(layout_dir.name)
        changed = False
        for field in ("active", "format"):
            views = meta.get(field, {}).get(scene_key, {})
            if old_view_key in views:
                views[new_view_key] = views.pop(old_view_key)
                changed = True
        if changed:
            _save_screenshot_meta(layout_dir.name, meta)

def _safe_name(name: str) -> str:
    """校验并返回可直接作为目录名的稳定布局 key。"""
    return validate_layout_key(name)


def layout_screenshots_dir(layout_name: str) -> Path:
    """获取布局的截图目录"""
    return SCREENSHOTS_DIR / _safe_name(layout_name)


def scene_screenshot_name(
    scene_key: str, view: str = "", index: int = 1, *, extension: str = "png",
) -> str:
    """截图文件名：基底视图沿用场景名，其余视图加 __视图 key 后缀

    同一场景的多个视图（同一页面的不同滚动态）各自一张底图，
    否则无法在正确的背景上标定坐标。

    index >= 2 时追加 __N 后缀，index=1 无额外后缀（向后兼容）。
    视图 key 必须以小写字母开头，纯数字后缀不会与视图名冲突。
    """
    from .scene_definition_models import BASE_VIEW_KEY
    if not view or view == BASE_VIEW_KEY:
        base = scene_key
    else:
        base = f"{scene_key}__{view}"
    if extension not in _SCREENSHOT_EXTENSIONS:
        raise ValueError(f"不支持的场景截图格式: {extension}")
    if index <= 1:
        return f"{base}.{extension}"
    return f"{base}__{index}.{extension}"


def _existing_screenshot_path(
    layout_name: str, scene_key: str, view: str, index: int,
) -> Path | None:
    d = layout_screenshots_dir(layout_name)
    for extension in _SCREENSHOT_EXTENSIONS:
        path = d / scene_screenshot_name(
            scene_key, view, index, extension=extension)
        if path.is_file():
            return path
    return None


# ─── 截图管理 ────────────────────────────────────────────

def load_scene_screenshot(
    layout_name: str, scene_key: str, view: str = "", index: int = 1,
) -> np.ndarray | None:
    """读取布局下某场景（可选视图、截图序号）的截图，不存在返回 None（支持中文路径）

    别名布局使用自己的截图目录（按布局名），不重定向到父布局，
    避免截图操作污染父布局。
    """
    path = _existing_screenshot_path(layout_name, scene_key, view, index)
    if path is None:
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
    layout_name: str, scene_key: str, image: np.ndarray,
    view: str = "", index: int = 1,
) -> np.ndarray:
    """保存场景（可选视图、截图序号）截图（支持中文路径）

    image: BGR numpy 数组（项目内部统一使用 BGR）
    """
    import cv2
    d = layout_screenshots_dir(layout_name)
    d.mkdir(parents=True, exist_ok=True)
    extension = get_scene_screenshot_format(layout_name, scene_key, view)
    path = d / scene_screenshot_name(scene_key, view, index, extension=extension)
    if extension == "webp" and not cv2.haveImageWriter(".webp"):
        raise OSError("当前 OpenCV 不支持 WebP，请在截图管理中改用 PNG")
    params = ([cv2.IMWRITE_WEBP_QUALITY, _WEBP_QUALITY]
              if extension == "webp" else [])
    # image 已是 BGR，cv2.imencode 期望 BGR，无需翻转。
    try:
        success, buf = cv2.imencode(f".{extension}", image, params)
    except cv2.error as exc:
        raise OSError(f"截图编码失败: {path}") from exc
    if not success:
        raise OSError(f"截图编码失败: {path}")
    stored_image = cv2.imdecode(buf, cv2.IMREAD_COLOR)
    if stored_image is None:
        raise OSError(f"截图编码后无法解码: {path}")
    temporary = d / f".{path.stem}.{uuid.uuid4().hex}.{extension}"
    try:
        temporary.write_bytes(buf.tobytes())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    for other in _SCREENSHOT_EXTENSIONS:
        if other != extension:
            (d / scene_screenshot_name(
                scene_key, view, index, extension=other)).unlink(missing_ok=True)
    logger.info(f"截图已保存: {path}")
    return stored_image


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


def rename_scene_screenshots(old_key: str, new_key: str):
    """重命名所有布局下的场景截图文件（含多视图后缀）

    匹配规则：{old_key}.png/.webp 和 {old_key}__*.png/.webp
    """
    if old_key == new_key:
        return
    screenshots_base = SCREENSHOTS_DIR
    if not screenshots_base.exists():
        return
    for layout_dir in screenshots_base.iterdir():
        if not layout_dir.is_dir():
            continue
        # 只匹配 {old_key}.png 和 {old_key}__*.png，不能误伤同前缀场景。
        for extension in _SCREENSHOT_EXTENSIONS:
            candidates = [layout_dir / f"{old_key}.{extension}"]
            candidates.extend(layout_dir.glob(f"{old_key}__*.{extension}"))
            for image_path in candidates:
                if not image_path.is_file():
                    continue
                suffix = image_path.stem[len(old_key):]
                new_name = f"{new_key}{suffix}.{extension}"
                image_path.rename(layout_dir / new_name)
                logger.info(f"截图已重命名: {image_path.name} -> {new_name}")
        meta = _load_screenshot_meta(layout_dir.name)
        changed = False
        for field in ("active", "format"):
            values = meta.get(field, {})
            if old_key in values:
                values[new_key] = values.pop(old_key)
                changed = True
        if changed:
            _save_screenshot_meta(layout_dir.name, meta)


# ─── 多截图管理 ──────────────────────────────────────────

_SCREENSHOT_META_FILENAME = "_screenshot_meta.json"


def _screenshot_meta_path(layout_name: str) -> Path:
    return layout_screenshots_dir(layout_name) / _SCREENSHOT_META_FILENAME


def _load_screenshot_meta(layout_name: str) -> dict:
    p = _screenshot_meta_path(layout_name)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_screenshot_meta(layout_name: str, meta: dict) -> None:
    p = _screenshot_meta_path(layout_name)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


def get_scene_screenshot_format(
    layout_name: str, scene_key: str, view: str = "",
) -> str:
    """当前视图新截图的保存格式；旧数据默认采用 WebP。"""
    selected = (
        _load_screenshot_meta(layout_name)
        .get("format", {})
        .get(scene_key, {})
        .get(view or "", "webp")
    )
    return selected if selected in _SCREENSHOT_EXTENSIONS else "webp"


def set_scene_screenshot_format(
    layout_name: str, scene_key: str, view: str, extension: str,
) -> None:
    if extension not in _SCREENSHOT_EXTENSIONS:
        raise ValueError(f"不支持的场景截图格式: {extension}")
    meta = _load_screenshot_meta(layout_name)
    meta.setdefault("format", {}).setdefault(scene_key, {})[view or ""] = extension
    _save_screenshot_meta(layout_name, meta)


def _screenshot_base_stem(scene_key: str, view: str) -> str:
    """Return the filename stem shared by all screenshots of (scene, view)."""
    from .scene_definition_models import BASE_VIEW_KEY
    if not view or view == BASE_VIEW_KEY:
        return scene_key
    return f"{scene_key}__{view}"


def list_scene_screenshots(
    layout_name: str, scene_key: str, view: str = "",
) -> list[int]:
    """Return sorted screenshot indices for (scene, view) under *layout_name*.

    Scans the screenshot directory with a regex that matches:
      {base_stem}.png/.webp          -> index 1
      {base_stem}__<N>.png/.webp     -> index N  (N >= 2)
    """
    d = layout_screenshots_dir(layout_name)
    if not d.exists():
        return []
    stem = _screenshot_base_stem(scene_key, view)
    pattern = re.compile(re.escape(stem) + r"(?:__(\d+))?\.(?:png|webp)$")
    indices: set[int] = set()
    for f in d.iterdir():
        m = pattern.match(f.name)
        if m:
            indices.add(int(m.group(1)) if m.group(1) else 1)
    return sorted(indices)


def get_active_screenshot_index(
    layout_name: str, scene_key: str, view: str = "",
) -> int:
    """Read the active screenshot index from meta, with fallback.

    If the recorded index has no backing file, falls back to the first
    available screenshot (or 1 when none exist).
    """
    meta = _load_screenshot_meta(layout_name)
    active = (
        meta.get("active", {})
        .get(scene_key, {})
        .get(view or "", 1)
    )
    indices = list_scene_screenshots(layout_name, scene_key, view)
    if not indices:
        return 1
    if active in indices:
        return active
    return indices[0]


def set_active_screenshot_index(
    layout_name: str, scene_key: str, view: str, index: int,
) -> None:
    meta = _load_screenshot_meta(layout_name)
    meta.setdefault("active", {}).setdefault(scene_key, {})[view or ""] = index
    _save_screenshot_meta(layout_name, meta)


def delete_scene_screenshot(
    layout_name: str, scene_key: str, view: str, index: int,
) -> None:
    """Delete one screenshot and re-index the rest so no gap remains."""
    d = layout_screenshots_dir(layout_name)
    for extension in _SCREENSHOT_EXTENSIONS:
        victim = d / scene_screenshot_name(
            scene_key, view, index, extension=extension)
        if victim.exists():
            victim.unlink()
            logger.info(f"截图已删除: {victim.name}")

    # Re-index: shift every file with index > deleted down by 1
    indices = list_scene_screenshots(layout_name, scene_key, view)
    for old_idx in sorted(i for i in indices if i > index):
        for extension in _SCREENSHOT_EXTENSIONS:
            old_path = d / scene_screenshot_name(
                scene_key, view, old_idx, extension=extension)
            new_path = d / scene_screenshot_name(
                scene_key, view, old_idx - 1, extension=extension)
            if old_path.exists():
                old_path.rename(new_path)

    # Fix active pointer
    meta = _load_screenshot_meta(layout_name)
    cur = meta.get("active", {}).get(scene_key, {}).get(view or "", 1)
    if cur >= index:
        new_active = max(1, cur - 1)
        set_active_screenshot_index(layout_name, scene_key, view, new_active)


def reindex_scene_screenshots(
    layout_name: str, scene_key: str, view: str,
    old_index: int, new_index: int,
) -> None:
    """Swap two screenshot indices (used by move-up / move-down)."""
    if old_index == new_index:
        return
    d = layout_screenshots_dir(layout_name)
    path_a = _existing_screenshot_path(layout_name, scene_key, view, old_index)
    path_b = _existing_screenshot_path(layout_name, scene_key, view, new_index)
    if path_a is None or path_b is None:
        return
    # Three-way swap via temp file
    tmp = d / f"_swap_tmp_{uuid.uuid4().hex}{path_a.suffix}"
    new_a = d / scene_screenshot_name(
        scene_key, view, new_index, extension=path_a.suffix[1:])
    new_b = d / scene_screenshot_name(
        scene_key, view, old_index, extension=path_b.suffix[1:])
    path_a.rename(tmp)
    path_b.rename(new_b)
    tmp.rename(new_a)

    # Swap active pointer if it referenced either index
    meta = _load_screenshot_meta(layout_name)
    cur = meta.get("active", {}).get(scene_key, {}).get(view or "", 1)
    if cur == old_index:
        set_active_screenshot_index(layout_name, scene_key, view, new_index)
    elif cur == new_index:
        set_active_screenshot_index(layout_name, scene_key, view, old_index)
