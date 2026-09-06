"""布局配置管理器 - 布局 CRUD、截图管理、窗口标题配置

存储结构（目录化）：
- layouts.yaml          名册 + canvas 内联（聚合键值，local diff 合并）
- layouts/{name}/{scene_key}.json  每场景独立文件（实体影子 + 墓碑）

布局别名（extends）：条目带 `extends: 根布局名` 时，scene 全部复用根布局
目录，仅 canvas 独立；别名自身不产生任何 scene 文件。
严格约束：extends 只能指向根布局（禁止多级继承）。
"""

import json
import shutil
from pathlib import Path
from typing import Iterable

import numpy as np
from loguru import logger

from ..constants import SESSION_CONFIG_DIR
from ..i18n import tr
from .config.resolver import get_resolver
from .config.session import get_session_store
from .key_validation import validate_layout_activation_keys
from .layout_models import CanvasConfig, Layout
from .scene_registry import (
    get_panel_defs,
    get_point_defs,
    get_region_defs,
)

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
    from .scene_definition_models import BASE_VIEW_KEY
    if not view or view == BASE_VIEW_KEY:
        return f"{scene_key}.png"
    return f"{scene_key}__{view}.png"


# ─── 截图管理 ────────────────────────────────────────────

def load_scene_screenshot(
    layout_name: str, scene_key: str, view: str = ""
) -> np.ndarray | None:
    """读取布局下某场景（可选视图）的截图，不存在返回 None（支持中文路径）

    别名布局使用自己的截图目录（按布局名），不重定向到父布局，
    避免截图操作污染父布局。
    """
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


def rename_scene_screenshots(old_key: str, new_key: str):
    """重命名所有布局下的场景截图文件（含多视图后缀）

    匹配规则：{old_key}.png 和 {old_key}__*.png
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
        candidates = [layout_dir / f"{old_key}.png"]
        candidates.extend(layout_dir.glob(f"{old_key}__*.png"))
        for png in candidates:
            if not png.is_file():
                continue
            suffix = png.stem[len(old_key):]  # 空或 "__view_key"
            new_name = f"{new_key}{suffix}.png"
            png.rename(layout_dir / new_name)
            logger.info(f"截图已重命名: {png.name} -> {new_name}")


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
    manager = LayoutConfigManager()
    for layout_name in manager.list_layouts():
        rename_layout_scene_key(layout_name, old_key, new_key)
    rename_scene_screenshots(old_key, new_key)


def delete_scene_across_all_layouts(scene_key: str):
    """彻底删除所有布局中的场景 JSON 及该场景的全部视图截图。"""
    resolver = get_resolver()
    manager = LayoutConfigManager()
    for layout_name in manager.list_layouts():
        resolver.delete_entity(_scene_rel(layout_name, scene_key))
    if SCREENSHOTS_DIR.exists():
        for layout_dir in SCREENSHOTS_DIR.iterdir():
            if not layout_dir.is_dir():
                continue
            candidates = [layout_dir / f"{scene_key}.png"]
            candidates.extend(layout_dir.glob(f"{scene_key}__*.png"))
            for path in candidates:
                if path.is_file():
                    path.unlink()
                    logger.info(f"场景截图已删除: {path}")


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
    manager = LayoutConfigManager()
    for layout_name in manager.list_layouts():
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
    manager = LayoutConfigManager()
    changed = []
    for layout_name in manager.list_layouts():
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


def rename_view_screenshots(scene_key: str, old_view_key: str, new_view_key: str):
    """重命名所有布局下某视图的截图文件

    截图命名规则：
    - 基底视图 (view="" 或 view="base"): {scene_key}.png
    - 其他视图: {scene_key}__{view_key}.png
    """
    if old_view_key == new_view_key:
        return
    from .scene_definition_models import BASE_VIEW_KEY
    screenshots_base = SCREENSHOTS_DIR
    if not screenshots_base.exists():
        return
    for layout_dir in screenshots_base.iterdir():
        if not layout_dir.is_dir():
            continue
        # 确定旧文件名和新文件名
        if old_view_key == BASE_VIEW_KEY or old_view_key == "":
            old_name = f"{scene_key}.png"
        else:
            old_name = f"{scene_key}__{old_view_key}.png"
        if new_view_key == BASE_VIEW_KEY or new_view_key == "":
            new_name = f"{scene_key}.png"
        else:
            new_name = f"{scene_key}__{new_view_key}.png"
        old_path = layout_dir / old_name
        if old_path.exists() and old_name != new_name:
            old_path.rename(layout_dir / new_name)
            logger.info(f"视图截图已重命名: {old_name} -> {new_name}")


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


# ─── 布局目录辅助 ────────────────────────────────────────

_LAYOUTS_YAML_REL = "layouts.yaml"


def _layout_dir_rel(name: str) -> str:
    """布局目录相对路径：layouts/{safe_name}"""
    return f"layouts/{_safe_name(name)}"


def _scene_rel(name: str, scene_key: str) -> str:
    """场景文件相对路径：layouts/{safe_name}/{scene_key}.json"""
    return f"layouts/{_safe_name(name)}/{scene_key}.json"


def scene_layout_rel(name: str, scene_key: str) -> str:
    """某布局下某场景坐标文件的相对路径（公开入口）。

    UI 要按布局名解析该文件的来源层（system/remote/local），需要这个路径。
    命名规则（safe_name 转义）与**别名布局的解析**都只有本模块知道，不该
    让调用方自己拼：带 ``extends`` 的别名布局，scene 文件实际存放在**根布局**
    目录下（见 :func:`_resolve_layout_entry`），照别名拼出来的路径根本不存在，
    调用方会得到"这个文件没有"的空结果而不自知。
    """
    return scene_layout_rels(name, (scene_key,))[scene_key]


def scene_layout_rels(name: str,
                      scene_keys: Iterable[str]) -> dict[str, str]:
    """批量返回布局场景路径，只解析一次 ``layouts.yaml``。

    场景编辑器会同时展示一个布局下的许多场景。逐场景调用
    :func:`scene_layout_rel` 会为每个场景重复解析同一份 YAML；这个批量入口
    保留完全相同的别名布局语义，同时把解析成本固定为一次。
    """
    doc = get_resolver().load_merged(_LAYOUTS_YAML_REL).get("layouts", {})
    resolved = _resolve_layout_entry(doc, name)
    scene_dir_name = resolved[1] if resolved else name
    return {
        scene_key: _scene_rel(scene_dir_name, scene_key)
        for scene_key in scene_keys
    }


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


def _resolve_layout_entry(layouts_doc: dict, name: str) -> tuple[dict, str, str] | None:
    """解析布局条目，返回 (canvas_dict, scene 目录所属布局名, desc)

    支持别名布局：条目带 extends 时，scene 文件目录指向根布局。
    严格约束：extends 只能指向根布局（目标自身不得再带 extends）。

    Returns:
        None 表示条目无效（extends 目标不存在或多级继承）
    """
    entry = layouts_doc.get(name) or {}
    extends = entry.get("extends")
    desc = entry.get("desc", "")
    if not extends:
        return entry.get("canvas", {}), name, desc
    if extends not in layouts_doc:
        logger.error(f"布局 [{name}] 的 extends 目标不存在: {extends}")
        return None
    if (layouts_doc.get(extends) or {}).get("extends"):
        logger.error(f"布局 [{name}] 的 extends 只能指向根布局，禁止多级继承: {extends}")
        return None
    return entry.get("canvas", {}), extends, desc


def load_layout_by_name(name: str) -> Layout | None:
    """模块级布局加载（无 session 依赖，供 workflow_runner 使用）

    从 layouts.yaml 读 canvas，从 layouts/{name}/ 目录逐场景加载。
    别名布局（带 extends）的 scene 从根布局目录加载，canvas 取自身条目。
    """
    from .layout_models import (
        Arrow,
        Panel,
        Point,
        Region,
        SubsceneRef,
        _apply_legacy_disabled,
    )

    resolver = get_resolver()
    merged = resolver.load_merged(_LAYOUTS_YAML_REL)
    layouts_doc = merged.get("layouts", {})

    resolved = _resolve_layout_entry(layouts_doc, name)
    if resolved is None:
        return None
    canvas_dict, scene_dir_name, desc = resolved

    if name not in layouts_doc:
        # 回退：目录存在但 yaml 未登记（兼容迁移中间态）
        scene_keys = _enumerate_scene_files(name)
        if not scene_keys:
            return None
    else:
        scene_keys = _enumerate_scene_files(scene_dir_name)

    # canvas（始终取自身条目）
    from .layout_models import CanvasConfig
    canvas = CanvasConfig.from_dict(canvas_dict) if canvas_dict else CanvasConfig()

    # 逐场景加载（别名布局从根布局目录读）
    regions: dict[str, list[Region]] = {}
    points: dict[str, list[Point]] = {}
    arrows: dict[str, list[Arrow]] = {}
    panels: dict[str, list[Panel]] = {}
    crop_canvases: dict[str, CanvasConfig] = {}
    subscene_refs: dict[str, list[SubsceneRef]] = {}

    for scene_key in scene_keys:
        path = resolver.resolve_read(_scene_rel(scene_dir_name, scene_key))
        if path is None:
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            logger.error(f"场景文件解析失败 {path}: {e}")
            continue
        if "regions" in data:
            regions[scene_key] = [Region.from_dict(r) for r in data["regions"]]
        if "points" in data:
            points[scene_key] = [Point.from_dict(p) for p in data["points"]]
        if "arrows" in data:
            arrows[scene_key] = [Arrow.from_dict(a) for a in data["arrows"]]
        if "panels" in data:
            panels[scene_key] = [Panel.from_dict(p) for p in data["panels"]]
        if isinstance(data.get("crop_canvas"), dict):
            crop_canvases[scene_key] = CanvasConfig.from_dict(data["crop_canvas"])
        if "subscene_refs" in data:
            subscene_refs[scene_key] = [SubsceneRef.from_dict(r) for r in data["subscene_refs"]]
        # 向后兼容：旧格式 disabled 段迁移到实例属性
        if "disabled" in data and isinstance(data["disabled"], dict):
            _apply_legacy_disabled(
                data["disabled"],
                regions, points, arrows, panels, scene_key,
            )

    _drop_orphan_coords(regions, points)
    _expand_scene_references(regions, points)

    return Layout(name=name, desc=desc, canvas=canvas, regions=regions,
                  points=points, arrows=arrows, panels=panels,
                  crop_canvases=crop_canvases, subscene_refs=subscene_refs)


def _drop_orphan_coords(regions: dict, points: dict) -> None:
    """丢弃布局里没有场景定义支撑的坐标记录（孤儿）。

    删除区域/坐标定义只改场景 YAML（``remove_region_from_scene``），布局 JSON
    里的坐标不跟着走；保存路径又只过滤带 ``source_scene`` 的引用项，于是这条
    残留记录会永久留在文件里。它在编辑器列表中根本不显示（列表按场景定义遍
    历），运行期却照常参与查表，还会把同名跨场景引用的展开顶掉——引用从此
    指向一份再也不会同步的陈旧坐标，而且完全静默。

    加载期统一清一次，存量自愈，不必手工改 JSON。**必须排在
    ``_expand_scene_references`` 之前**，否则孤儿仍然挡着展开。
    """
    from .scene_registry import get_registry

    try:
        registry = get_registry()
    except Exception as exc:  # noqa: BLE001 — 注册表不可用时绝不能清坐标
        logger.warning(f"孤儿坐标清理跳过（场景注册表不可用）: {exc}")
        return

    for table, attr in ((regions, "regions"), (points, "points")):
        for scene_key, items in list(table.items()):
            scene = registry.get_scene(scene_key)
            if scene is None:
                # 场景 YAML 缺失时定义集为空，照清等于抹掉整份坐标。这种布局
                # 文件整体已是死数据，属于文件级清理的活，这里不碰。
                continue
            defined = {d.key for d in getattr(scene, attr)}
            dropped = [i.key for i in items if i.key not in defined]
            if not dropped:
                continue
            table[scene_key] = [i for i in items if i.key in defined]
            logger.warning(
                f"布局中 {scene_key} 有 {len(dropped)} 条坐标已无场景定义，"
                f"已丢弃: {', '.join(dropped)}")


def _expand_scene_references(regions: dict, points: dict) -> None:
    """把场景声明的跨场景 area 引用展开进本场景的坐标表。

    只允许引用一级场景，其实体坐标本就是画布归一化，**原样搬过来即可，
    零变换**——这正是"只引用一级场景"这条约束换来的简化。

    展开在加载期做而不是查表期做，好处是运行期零改动：``get_scene_regions``
    仍是纯字典查表，``click [equip_tune_detail].[confirm]`` 自然就通了，
    click_region / click_any / _validate_refs_bound 一行都不用改。

    展开项带 ``source_scene`` 标记，编辑器据此锁死、保存路径据此过滤。
    """
    from .scene_registry import get_registry

    try:
        scenes = get_registry().all_scenes()
    except Exception as exc:  # noqa: BLE001 — 注册表异常不能让布局加载失败
        logger.warning(f"跨场景引用展开跳过（场景注册表不可用）: {exc}")
        return

    for scene_key, scene in scenes.items():
        for ref in getattr(scene, "references", ()):
            expand_one_reference(
                regions, points, scene_key, ref.scene, ref.entity)


def refresh_scene_references(
    layout: Layout,
    source_scenes: set[str] | None = None,
) -> set[str]:
    """用源场景当前坐标重建跨场景引用，返回受影响的目标场景。

    布局加载时引用只展开一次；编辑器随后修改源场景坐标时，目标场景持有的
    仍是旧克隆。保存前调用本函数，先移除指定源场景的旧投影，再从 Layout
    当前快照重新展开，使已打开的引用场景无需重进布局管理器即可同步。
    """
    from .scene_registry import get_registry

    try:
        scenes = get_registry().all_scenes()
    except Exception as exc:  # noqa: BLE001 — 刷新失败不能破坏源布局数据
        logger.warning(f"跨场景引用刷新跳过（场景注册表不可用）: {exc}")
        return set()

    affected: set[str] = set()
    for scene_key, scene in scenes.items():
        refs = [
            ref for ref in getattr(scene, "references", ())
            if source_scenes is None or ref.scene in source_scenes
        ]
        if not refs:
            continue
        affected.add(scene_key)
        refreshed_sources = {ref.scene for ref in refs}
        layout.regions[scene_key] = [
            item for item in layout.regions.get(scene_key, [])
            if item.source_scene not in refreshed_sources
        ]
        layout.points[scene_key] = [
            item for item in layout.points.get(scene_key, [])
            if item.source_scene not in refreshed_sources
        ]
        for ref in refs:
            expand_one_reference(
                layout.regions,
                layout.points,
                scene_key,
                ref.scene,
                ref.entity,
            )
    return affected


def expand_one_reference(regions: dict, points: dict, scene_key: str,
                         source_scene: str, entity: str) -> bool:
    """把一条跨场景引用展开进坐标表，成功返回 True。

    单独拆出来是为了编辑器：新加一条引用之后不必整份布局重载（那会丢掉
    画布上还没保存的改动），把这一条的坐标补进去即可。
    """
    from dataclasses import replace as _replace

    for table in (regions, points):
        source = next(
            (item for item in table.get(source_scene, [])
             if item.key == entity), None)
        if source is None:
            continue
        target = table.setdefault(scene_key, [])
        if any(item.key == entity for item in target):
            # 场景加载期的 key 去重本应拦下同名，走到这里说明配置被
            # 绕过了。宁可不展开也不覆盖——覆盖等于让运行期点到另一
            # 个位置，而且完全静默。
            logger.error(
                f"跨场景引用 {scene_key}.{entity} 与本场景已有定义"
                f"同名，已跳过展开")
            return False
        target.append(_replace(source, source_scene=source_scene))
        return True
    # 源场景在当前布局里没给这个实体标坐标，跑到它就会失败。
    logger.warning(
        f"跨场景引用 {scene_key}.{entity} 在本布局中无坐标"
        f"（源场景 {source_scene} 未绑定）")
    return False


# ─── 布局配置管理器 ──────────────────────────────────────

class LayoutConfigManager:
    """管理布局配置的持久化

    布局存储为目录结构：layouts.yaml（名册+canvas）+ layouts/{name}/{scene}.json；
    读写经 ConfigResolver（开发→system，用户→local 影子）；
    session.json 只在 actives.layout 记录激活布局。
    """

    def __init__(self):
        self._config = self._load_config()

    def _load_config(self) -> dict:
        """从 session.json 读 actives.layout（兼容旧 active_layout）

        layout_manager 只管理 active_layout，不触碰 session.json 其他节点。
        """
        return {"active_layout": get_session_store().get_active("layout", "")}

    def _save_config(self):
        """保存 actives.layout，并清理旧 active_* 顶层键。"""
        get_session_store().set_active(
            "layout", self._config.get("active_layout", ""))

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
            return list(layouts.keys())  # 保持 YAML 定义顺序，不排序
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
        """创建空布局（所有场景初始为空 regions）

        Raises:
            ValueError: 布局名已存在（含别名）时。新建撞名别名会把空场景
                全量写入根布局目录，造成根布局数据被清空，必须拒绝。
        """
        if name in self.list_layouts():
            raise ValueError(f"布局已存在，无法新建: {name}")
        from .scene_registry import SCENE_REGIONS
        layout = Layout(name=name)
        for scene_key in SCENE_REGIONS:
            layout.regions[scene_key] = []
        self.save_layout(layout)
        self.set_active_layout(name)
        logger.info(f"布局已新建: {name}")
        return layout

    def is_alias_layout(self, name: str) -> bool:
        """判断布局是否为别名（yaml 条目带 extends）"""
        resolver = get_resolver()
        merged = resolver.load_merged(_LAYOUTS_YAML_REL)
        entry = merged.get("layouts", {}).get(name) or {}
        return bool(entry.get("extends"))

    def create_alias_layout(self, name: str, extends_name: str, canvas: CanvasConfig) -> Layout | None:
        """创建别名布局：仅 yaml 条目（extends + canvas），无 scene 文件

        Args:
            name: 新布局名称
            extends_name: 继承的根布局名称
            canvas: 画布配置

        Returns:
            创建的 Layout 对象，失败时返回 None
        """
        if name in self.list_layouts():
            logger.error(f"布局已存在，无法创建别名: {name}")
            return None
        if extends_name not in self.list_layouts():
            logger.error(f"继承目标不存在: {extends_name}")
            return None
        if self.is_alias_layout(extends_name):
            logger.error(f"继承目标必须是根布局，不能是别名: {extends_name}")
            return None

        # 写入 yaml 条目
        resolver = get_resolver()
        merged = resolver.load_merged(_LAYOUTS_YAML_REL)
        layouts_doc = merged.setdefault("layouts", {})
        layouts_doc[name] = {"extends": extends_name, "canvas": canvas.to_dict()}
        resolver.save_merged(_LAYOUTS_YAML_REL, merged)

        # 加载并返回（scene 从根布局读取）
        layout = self.load_layout(name)
        if layout:
            logger.info(f"别名布局已创建: {name} (extends {extends_name})")
        return layout

    def load_layout(self, name: str) -> "Layout | None":
        layout = load_layout_by_name(name)
        if layout is None:
            logger.warning(f"布局不存在: {name}")
        else:
            logger.info(f"布局已加载: {name}")
        return layout

    def save_layout(self, layout: Layout, changed_scenes: set[str] | None = None,
                    content_versions: dict[str, int] | None = None) -> bool:
        """保存布局

        Args:
            layout: 布局对象
            changed_scenes: 需要写盘的场景 key 集合。
                None = 全量写盘（新建布局、另存为等场景）；
                set = 增量写盘，只写指定场景的 JSON 文件。
            content_versions: 场景 key → 显式目标版本。版本提升本身也会使对应
                场景文件进入本次写盘集合；未提供的文件普通保存时保留旧版本。

        别名布局（yaml 条目带 extends）：保留 extends 字段，
        scene 文件写入根布局目录（别名自身不维护 scene）。
        extends 条目非法（目标缺失/多级继承）时拒绝写盘。
        """
        # 持久化入口做最终门禁：UI 表单会实时校验，但导入、
        # 测试替身或其他调用方仍可能构造出非法的内存布局。
        try:
            validate_layout_activation_keys(layout)
        except ValueError as exc:
            logger.error(f"布局 [{layout.name}] 按键校验失败，拒绝保存：{exc}")
            return False

        resolver = get_resolver()

        # 1. 校验 extends 合法性（与加载侧对称：目标缺失/多级继承拒绝写盘）
        merged = resolver.load_merged(_LAYOUTS_YAML_REL)
        layouts_doc = merged.setdefault("layouts", {})
        resolved = _resolve_layout_entry(layouts_doc, layout.name)
        if resolved is None:
            logger.error(f"布局 [{layout.name}] 的 extends 条目非法，拒绝保存")
            return False
        _, scene_dir_name, _ = resolved

        # 2. 更新 layouts.yaml 中的条目（别名布局保留 extends）
        existing = layouts_doc.get(layout.name) or {}
        entry_out: dict = {}
        if existing.get("extends"):
            entry_out["extends"] = existing["extends"]
        # 保留 desc 字段（如果有）
        if layout.desc:
            entry_out["desc"] = layout.desc
        elif existing.get("desc"):
            entry_out["desc"] = existing["desc"]
        entry_out["canvas"] = layout.canvas.to_dict()
        layouts_doc[layout.name] = entry_out
        resolver.save_merged(_LAYOUTS_YAML_REL, merged)

        # 3. 写场景 JSON 文件（增量或全量）；别名布局落到根布局目录
        all_scene_keys = (set(layout.regions) | set(layout.points) | set(layout.arrows)
                          | set(layout.panels) | set(layout.crop_canvases)
                          | set(layout.subscene_refs))
        versions = content_versions or {}
        scene_keys = all_scene_keys if changed_scenes is None else (
            (changed_scenes & all_scene_keys) | set(versions))
        for sk in scene_keys:
            entry: dict = {}
            # 按场景定义顺序排序 regions/points/panels，避免编辑顺序影响输出
            region_order = {r.key: i for i, r in enumerate(get_region_defs(sk))}
            # 引用项属于源场景，写回就烘死成拷贝，源场景改坐标不再同步。
            # Region.to_dict 也剔了 source_scene，这里是第二道闸。
            regions = [r for r in (layout.regions.get(sk) or [])
                       if not getattr(r, "source_scene", "")]
            entry["regions"] = [r.to_dict() for r in sorted(regions, key=lambda r: region_order.get(r.key, 999))]
            pts = [p for p in (layout.points.get(sk) or [])
                   if not getattr(p, "source_scene", "")]
            if pts:
                point_order = {p.key: i for i, p in enumerate(get_point_defs(sk))}
                entry["points"] = [p.to_dict() for p in sorted(pts, key=lambda p: point_order.get(p.key, 999))]
            arrs = layout.arrows.get(sk) or []
            if arrs:
                entry["arrows"] = [a.to_dict() for a in arrs]  # arrows 无定义顺序，保持原样
            pnls = layout.panels.get(sk) or []
            if pnls:
                panel_order = {p.key: i for i, p in enumerate(get_panel_defs(sk))}
                entry["panels"] = [p.to_dict() for p in sorted(pnls, key=lambda p: panel_order.get(p.key, 999))]
            if sk in layout.crop_canvases:
                entry["crop_canvas"] = layout.crop_canvases[sk].to_dict()
            refs = layout.subscene_refs.get(sk) or []
            if refs:
                entry["subscene_refs"] = [r.to_dict() for r in refs]
            resolver.write_entity(
                _scene_rel(scene_dir_name, sk),
                json.dumps(entry, ensure_ascii=False, indent=2),
                content_version=versions.get(sk),
            )
        mode = f"增量 {len(scene_keys)}/{len(all_scene_keys)} 场景" if changed_scenes is not None else tr("全量")
        logger.info(f"布局已保存: {layout.name} ({mode})")
        return True

    @staticmethod
    def is_system_layout(name: str) -> bool:
        """布局是否由 system 层声明。

        别名布局没有自己的场景目录，因此不能通过场景文件是否存在来判断来源。
        ``layouts.yaml`` 的 system 名册才是布局身份的单一事实源。
        """
        resolver = get_resolver()
        system_doc = resolver.load_system(_LAYOUTS_YAML_REL)
        layouts = system_doc.get("layouts", {})
        return isinstance(layouts, dict) and name in layouts

    def delete_layout(self, name: str) -> bool:
        resolver = get_resolver()
        # 确认布局存在
        merged = resolver.load_merged(_LAYOUTS_YAML_REL)
        layouts_doc = merged.get("layouts", {})
        scene_keys = _enumerate_scene_files(name)
        if name not in layouts_doc and not scene_keys:
            return False

        # 拒绝删除被别名引用的根布局（避免悬空 extends）
        referencing = [n for n, e in layouts_doc.items()
                       if isinstance(e, dict) and e.get("extends") == name]
        if referencing:
            logger.error(f"布局 [{name}] 被别名布局引用: {referencing}，拒绝删除")
            return False
        # 系统布局属于 system 内容，用户模式下不可删除——不想用就别选它
        if not resolver.is_dev_mode() and self.is_system_layout(name):
            logger.error(f"布局 [{name}] 由系统配置提供，用户模式下不可删除")
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
