"""布局条目与场景文件读取、别名解析及引用展开。"""

import json
from typing import Iterable

from loguru import logger

from .config.resolver import get_resolver
from .layout_config import (
    DEFAULT_ASPECT_TOLERANCE,
    load_layout_doc,
    validate_layout_key,
)
from .layout_models import CanvasConfig, Layout

# ─── 布局目录辅助 ────────────────────────────────────────

_LAYOUTS_YAML_REL = "layouts.yaml"


def _layout_dir_rel(name: str) -> str:
    """布局目录相对路径：layouts/{safe_name}"""
    return f"layouts/{validate_layout_key(name)}"


def _scene_rel(name: str, scene_key: str) -> str:
    """场景文件相对路径：layouts/{safe_name}/{scene_key}.json"""
    return f"layouts/{validate_layout_key(name)}/{scene_key}.json"


def scene_layout_rel(name: str, scene_key: str) -> str:
    """某布局下某场景坐标文件的相对路径（公开入口）。

    UI 要按布局名解析该文件的来源层（system/local），需要这个路径。
    命名规则（safe_name 转义）与**别名布局的解析**都只有本模块知道，不该
    让调用方自己拼：带 ``extends`` 的别名布局，scene 文件实际存放在**根布局**
    目录下（见 :func:`_resolve_layout_entry`），照别名拼出来的路径根本不存在，
    调用方会得到"这个文件没有"的空结果而不自知。
    """
    return scene_layout_rels(name, (scene_key,))[scene_key]


def shared_layout_bindings(name: str) -> list[str]:
    """返回与指定布局共用场景绑定的布局名（包括根布局和别名）。"""
    doc = load_layout_doc().get("layouts", {})
    current = _resolve_layout_entry(doc, name)
    if current is None:
        return []
    names = []
    for candidate in doc:
        resolved = _resolve_layout_entry(doc, candidate)
        if resolved is not None and resolved[1] == current[1]:
            names.append(candidate)
    return names


def scene_layout_rels(name: str,
                      scene_keys: Iterable[str]) -> dict[str, str]:
    """批量返回布局场景路径，只解析一次 ``layouts.yaml``。

    场景编辑器会同时展示一个布局下的许多场景。逐场景调用
    :func:`scene_layout_rel` 会为每个场景重复解析同一份 YAML；这个批量入口
    保留完全相同的别名布局语义，同时把解析成本固定为一次。
    """
    doc = load_layout_doc().get("layouts", {})
    resolved = _resolve_layout_entry(doc, name)
    scene_dir_name = resolved[1] if resolved else name
    return {
        scene_key: _scene_rel(scene_dir_name, scene_key)
        for scene_key in scene_keys
    }


def _enumerate_scene_files(name: str) -> list[str]:
    """枚举布局目录下所有场景 JSON 文件名（system ∪ local 并集）

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
    return sorted(names)


def _resolve_layout_entry(
    layouts_doc: dict, name: str,
) -> tuple[dict, str, str, str] | None:
    """解析布局条目，返回 (canvas, scene 目录 key, 显示名, desc)。

    支持别名布局：条目带 extends 时，scene 文件目录指向根布局。
    严格约束：extends 只能指向根布局（目标自身不得再带 extends）。

    Returns:
        None 表示条目无效（extends 目标不存在或多级继承）
    """
    entry = layouts_doc.get(name)
    if not isinstance(entry, dict):
        return None
    extends = entry.get("extends")
    display_name = entry["name"]
    desc = entry.get("desc", "")
    if not extends:
        return entry.get("canvas", {}), name, display_name, desc
    if extends not in layouts_doc:
        logger.error(f"布局 [{name}] 的 extends 目标不存在: {extends}")
        return None
    if (layouts_doc.get(extends) or {}).get("extends"):
        logger.error(f"布局 [{name}] 的 extends 只能指向根布局，禁止多级继承: {extends}")
        return None
    return entry.get("canvas", {}), extends, display_name, desc


def load_layout_by_key(name: str) -> Layout | None:
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
    )

    resolver = get_resolver()
    merged = load_layout_doc(resolver)
    layouts_doc = merged.get("layouts", {})

    resolved = _resolve_layout_entry(layouts_doc, name)
    if resolved is None:
        return None
    canvas_dict, scene_dir_name, display_name, desc = resolved
    scene_keys = _enumerate_scene_files(scene_dir_name)

    # canvas（始终取自身条目）
    canvas = CanvasConfig.from_dict(canvas_dict) if canvas_dict else CanvasConfig()

    # 逐场景加载（别名布局从根布局目录读）
    regions: dict[str, list[Region]] = {}
    points: dict[str, list[Point]] = {}
    arrows: dict[str, list[Arrow]] = {}
    panels: dict[str, list[Panel]] = {}
    crop_canvases: dict[str, CanvasConfig] = {}
    subscene_refs: dict[str, list[SubsceneRef]] = {}
    reference_positions: dict[tuple[str, str, str], tuple[float, float]] = {}

    for scene_key in scene_keys:
        path = resolver.resolve_read(_scene_rel(scene_dir_name, scene_key))
        if path is None:
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            logger.error(f"场景文件解析失败 {path}: {e}")
            continue
        if "disabled" in data:
            logger.error(
                f"场景文件 {path} 的旧集中 disabled 段不再兼容，已跳过；"
                "请将停用状态移到各实例的 disabled: true，保留原坐标")
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
        for position in data.get("reference_positions", []):
            if not isinstance(position, dict):
                continue
            source = position.get("scene")
            entity = position.get("entity")
            if source and entity:
                reference_positions[(scene_key, str(source), str(entity))] = (
                    float(position["x_ratio"]), float(position["y_ratio"]))

    _drop_orphan_coords(regions, points)
    _expand_scene_references(regions, points, reference_positions)

    # aspect 与 canvas 同源：都描述「这个布局怎么看画面」，取自身条目而非根布局
    own_entry = layouts_doc.get(name) or {}
    aspect = str(own_entry.get("aspect", "") or "")
    tolerance = own_entry.get("aspect_tolerance", DEFAULT_ASPECT_TOLERANCE)

    return Layout(key=name, name=display_name, desc=desc, canvas=canvas,
                  aspect=aspect, aspect_tolerance=float(tolerance),
                  regions=regions,
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


def _expand_scene_references(
    regions: dict,
    points: dict,
    reference_positions: (
        dict[tuple[str, str, str], tuple[float, float]] | None
    ) = None,
) -> None:
    """把场景声明的跨场景 area 引用展开进本场景的坐标表。

    只允许引用一级场景，其实体坐标本就是画布归一化，**原样搬过来即可，
    零变换**——这正是"只引用一级场景"这条约束换来的简化。

    展开在加载期做而不是查表期做，好处是运行期零改动：``get_scene_regions``
    仍是纯字典查表，``click [equip_tune_detail].[confirm]`` 自然就通了，
    click_region / click_any / _validate_refs_bound 一行都不用改。

    展开项带 ``source_scene`` 标记。编辑器只允许移动，并把目标场景的位置
    单独保存为 ``reference_positions``；完整实体仍由保存路径过滤。
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
                regions, points, scene_key, ref.scene, ref.entity,
                position=(reference_positions or {}).get(
                    (scene_key, ref.scene, ref.entity)))


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
        positions = {
            (item.source_scene, item.key): (
                (item.x_ratio, item.y_ratio)
                if hasattr(item, "x_ratio")
                else (item.cx_ratio, item.cy_ratio))
            for item in (
                *layout.regions.get(scene_key, []),
                *layout.points.get(scene_key, []),
            )
            if item.source_scene and item.position_overridden
        }
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
                position=positions.get((ref.scene, ref.entity)),
            )
    return affected


def expand_one_reference(regions: dict, points: dict, scene_key: str,
                         source_scene: str, entity: str,
                         position: tuple[float, float] | None = None) -> bool:
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
        expanded = _replace(
            source,
            source_scene=source_scene,
            position_overridden=position is not None,
            source_x_ratio=(
                source.x_ratio if hasattr(source, "x_ratio")
                else source.cx_ratio),
            source_y_ratio=(
                source.y_ratio if hasattr(source, "y_ratio")
                else source.cy_ratio),
        )
        if position is not None:
            if hasattr(expanded, "x_ratio"):
                expanded.x_ratio, expanded.y_ratio = position
            else:
                expanded.cx_ratio, expanded.cy_ratio = position
        target.append(expanded)
        return True
    # 源场景在当前布局里没给这个实体标坐标，跑到它就会失败。
    logger.warning(
        f"跨场景引用 {scene_key}.{entity} 在本布局中无坐标"
        f"（源场景 {source_scene} 未绑定）")
    return False


# ─── 布局配置管理器 ──────────────────────────────────────
