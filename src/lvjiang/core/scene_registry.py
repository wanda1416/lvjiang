"""场景注册表 - 全局函数与缓存

提供场景/区域/坐标/面板/分组的全局注册表函数。
运行时布局数据类（Region / Point / Arrow / Panel / Layout 等）
已拆分至 layout_models.py。
"""

from loguru import logger

from .config.resolver import get_resolver
from .scene_definition import SceneRegistry
from .scene_definition_models import (
    BASE_VIEW_KEY,
    PanelDef,
    PointDef,
    RegionDef,
    ViewDef,
)


def _load_scene_order() -> list[str] | None:
    """从 scenes.yaml（合并视图）读取场景加载顺序"""
    try:
        data = get_resolver().load_merged("scenes.yaml")
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
    """从 scenes.yaml（合并视图）读取分组配置，返回 (group_config, group_names)"""
    try:
        data = get_resolver().load_merged("scenes.yaml")
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
        scene_order=scene_order,
        group_config=group_config,
        group_names=group_names,
    )
    _rebuild_scene_globals()


def _on_config_change(rel_path: str):
    """配置写入后的失效通知：场景相关文件变更时重载注册表"""
    if rel_path == "scenes.yaml" or rel_path.startswith("scenes/"):
        reload_scene_registry()


get_resolver().add_change_listener(_on_config_change)




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


# ─── 视图 ───────────────────────────────────────────────

def get_scene_views(scene_key: str) -> list[ViewDef]:
    """获取场景视图列表（空 = 未开启多视图）"""
    return _registry.get_scene_views(scene_key)


def is_view_visible(item_view: str, current_view: str) -> bool:
    """当前视图下某定义是否可见

    current_view 为空 = 看全部；选定视图时只看该视图自身的定义
    （基底是普通视图，不叠加展示；定义的 view 字段为空等价于归属基底）。
    """
    if not current_view:
        return True
    if current_view == BASE_VIEW_KEY:
        return item_view in ("", BASE_VIEW_KEY)
    return item_view == current_view


def get_view_visible_keys(scene_key: str, current_view: str) -> set[str] | None:
    """当前视图下可见的全部定义 key（看全部时返回 None 表示不过滤）"""
    if not current_view:
        return None
    scene = _registry.get_scene(scene_key)
    if not scene:
        return set()
    return {
        i.key
        for i in (*scene.regions, *scene.points, *scene.panels)
        if is_view_visible(i.view, current_view)
    }


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




def get_panel_defs(scene_key: str) -> list[PanelDef]:
    """获取场景的完整面板定义列表"""
    scene = _registry.get_scene(scene_key)
    if not scene:
        return []
    return list(scene.panels)
