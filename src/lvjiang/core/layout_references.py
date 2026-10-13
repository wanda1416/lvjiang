"""跨布局的实体键与引用维护；截图变更委托资源模块。"""

import json

from loguru import logger

from . import layout_screenshots as screenshots
from .config.resolver import get_resolver
from .layout_config import (
    load_layout_entries,
)
from .layout_models import Layout
from .layout_storage import (
    _scene_rel,
)


def rename_layout_scene_key(layout_name: str, old_key: str, new_key: str):
    """重命名单个布局下某场景的 JSON 文件（内容不变，仅改文件名）"""
    if old_key == new_key:
        return
    resolver = get_resolver()
    old_rel = _scene_rel(layout_name, old_key)
    new_rel = _scene_rel(layout_name, new_key)
    old_path = resolver.resolve_read(old_rel)
    if old_path and old_path.exists():
        content = old_path.read_text(encoding="utf-8")
        resolver.write_entity(new_rel, content)
        resolver.delete_entity(old_rel)
        logger.info(f"布局场景文件已重命名: {old_key}.json -> {new_key}.json ({layout_name})")


def rename_scene_across_all_layouts(old_key: str, new_key: str):
    """遍历所有布局，重命名场景 JSON 文件 + 截图文件"""
    if old_key == new_key:
        return
    for layout_name in load_layout_entries():
        rename_layout_scene_key(layout_name, old_key, new_key)
    screenshots.rename_scene_screenshots(old_key, new_key)


def delete_scene_across_all_layouts(scene_key: str):
    """彻底删除所有布局中的场景 JSON 及该场景的全部视图截图。"""
    resolver = get_resolver()
    for layout_name in load_layout_entries():
        resolver.delete_entity(_scene_rel(layout_name, scene_key))
    if screenshots.SCREENSHOTS_DIR.exists():
        for layout_dir in screenshots.SCREENSHOTS_DIR.iterdir():
            if not layout_dir.is_dir():
                continue
            for extension in screenshots._SCREENSHOT_EXTENSIONS:
                candidates = [layout_dir / f"{scene_key}.{extension}"]
                candidates.extend(layout_dir.glob(f"{scene_key}__*.{extension}"))
                for path in candidates:
                    if path.is_file():
                        path.unlink()
                        logger.info(f"场景截图已删除: {path}")
            meta = screenshots._load_screenshot_meta(layout_dir.name)
            changed = False
            for field in ("active", "format"):
                values = meta.get(field, {})
                if scene_key in values:
                    del values[scene_key]
                    changed = True
            if changed:
                screenshots._save_screenshot_meta(layout_dir.name, meta)


def rename_item_key_across_all_layouts(scene_key: str, kind: str, old_key: str, new_key: str):
    """遍历所有布局，重命名指定场景下某实例的 key

    Args:
        scene_key: 场景 key
        kind: 实例类型 ("region" | "point" | "panel")
        old_key: 旧 key
        new_key: 新 key

    对于 point 重命名，同步更新 Arrow 的 from_key/to_key 引用。
    """
    if old_key == new_key:
        return
    for layout_name in load_layout_entries():
        _rename_layout_item_key(layout_name, scene_key, kind, old_key, new_key)


def _rename_layout_item_key(layout_name: str, scene_key: str, kind: str, old_key: str, new_key: str):
    """重命名单个布局中某场景下指定类型实例的 key"""
    resolver = get_resolver()
    scene_rel = _scene_rel(layout_name, scene_key)
    scene_path = resolver.resolve_read(scene_rel)
    if scene_path is None or not scene_path.exists():
        return  # 该布局下没有此场景的文件，跳过

    # 加载场景 JSON
    try:
        data = json.loads(scene_path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.error(f"加载布局场景文件失败 {scene_path}: {e}")
        return

    changed = False

    if kind == "region":
        for region in data.get("regions", []):
            if region.get("key") == old_key:
                region["key"] = new_key
                changed = True

    elif kind == "point":
        for point in data.get("points", []):
            if point.get("key") == old_key:
                point["key"] = new_key
                changed = True
        # 同步更新 Arrow 的 from_key/to_key 引用
        for arrow in data.get("arrows", []):
            if arrow.get("from_key") == old_key:
                arrow["from_key"] = new_key
                changed = True
            if arrow.get("to_key") == old_key:
                arrow["to_key"] = new_key
                changed = True

    elif kind == "panel":
        for panel in data.get("panels", []):
            if panel.get("key") == old_key:
                panel["key"] = new_key
                changed = True

    else:
        logger.warning(f"未知的实例类型: {kind}")
        return

    if changed:
        resolver.write_entity(scene_rel, json.dumps(data, ensure_ascii=False, indent=2))
        logger.info(f"布局 {layout_name} 场景 {scene_key} 中 {kind} key 重命名: {old_key} -> {new_key}")


def delete_item_key_across_all_layouts(
    scene_key: str, kind: str, key: str,
) -> list[str]:
    """遍历所有布局，删除指定场景下某实例的坐标记录，返回改动过的布局名。

    删定义只改场景 YAML，布局 JSON 里的坐标不跟着走。加载期虽有
    :func:`_drop_orphan_coords` 兜底，但那只清内存：没打开过的布局文件里
    那条记录会一直躺着，直到有人恰好加载并保存了那套布局。更糟的是**同名
    重建**——回头新建一个同 key 的实体，旧坐标会直接被当成它的坐标，位置
    莫名其妙还完全静默。

    删 point 连带删掉以它为端点的 arrow：端点没了的 arrow 既画不出来也跑
    不了，留着就是下一条残留。
    """
    changed = []
    for layout_name in load_layout_entries():
        if _delete_layout_item_key(layout_name, scene_key, kind, key):
            changed.append(layout_name)
    return changed


def _delete_layout_item_key(layout_name: str, scene_key: str, kind: str,
                            key: str) -> bool:
    """删除单个布局中某场景下指定类型实例的坐标记录"""
    table = {"region": "regions", "point": "points", "panel": "panels"}.get(kind)
    if table is None:
        logger.warning(f"未知的实例类型: {kind}")
        return False
    resolver = get_resolver()
    scene_rel = _scene_rel(layout_name, scene_key)
    scene_path = resolver.resolve_read(scene_rel)
    if scene_path is None or not scene_path.exists():
        return False  # 该布局下没有此场景的文件，跳过
    try:
        data = json.loads(scene_path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.error(f"加载布局场景文件失败 {scene_path}: {e}")
        return False

    items = data.get(table, [])
    kept = [item for item in items if item.get("key") != key]
    changed = len(kept) != len(items)
    if changed:
        data[table] = kept
    if kind == "point":
        arrows = data.get("arrows", [])
        kept_arrows = [
            a for a in arrows
            if a.get("from_key") != key and a.get("to_key") != key
        ]
        if len(kept_arrows) != len(arrows):
            data["arrows"] = kept_arrows
            changed = True

    if changed:
        resolver.write_entity(
            scene_rel, json.dumps(data, ensure_ascii=False, indent=2))
        logger.info(
            f"布局 {layout_name} 场景 {scene_key} 中已删除 {kind} 坐标: {key}")
    return changed




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
        source_regions = layout.get_scene_regions(source)
        region = next((r for r in source_regions if r.key == key), None)
        if region is None:
            return False
        layout.set_scene_regions(
            source, [r for r in source_regions if r.key != key])
        # target 已有同 key 的陈旧项先移除
        target_regions = [
            r for r in layout.get_scene_regions(target) if r.key != key]
        target_regions.append(region)
        layout.set_scene_regions(target, target_regions)
        return True

    if kind == "point":
        source_points = layout.get_scene_points(source)
        point = next((p for p in source_points if p.key == key), None)
        if point is None:
            return False
        layout.set_scene_points(
            source, [p for p in source_points if p.key != key])
        target_points = [
            p for p in layout.get_scene_points(target) if p.key != key]
        target_points.append(point)
        layout.set_scene_points(target, target_points)
        # 箭头联动：from_key 随迁，to_key 烘焙为绝对坐标
        src_arrows = layout.get_scene_arrows(source)
        moved = [a for a in src_arrows if a.from_key == key]
        remain = [a for a in src_arrows if a.from_key != key]
        for a in remain:
            if a.to_key == key:
                a.to_key = None
                a.to_cx_ratio = point.cx_ratio
                a.to_cy_ratio = point.cy_ratio
        layout.set_scene_arrows(source, remain)
        if moved:
            moved_keys = {a.key for a in moved}
            dst_arrows = [a for a in layout.get_scene_arrows(target) if a.key not in moved_keys]
            dst_arrows.extend(moved)
            layout.set_scene_arrows(target, dst_arrows)
        return True

    if kind == "panel":
        source_panels = layout.get_scene_panels(source)
        panel = next((p for p in source_panels if p.key == key), None)
        if panel is None:
            return False
        layout.set_scene_panels(
            source, [p for p in source_panels if p.key != key])
        target_panels = [
            p for p in layout.get_scene_panels(target) if p.key != key]
        target_panels.append(panel)
        layout.set_scene_panels(target, target_panels)
        return True

    raise ValueError(f"未知迁移类型: {kind}")
