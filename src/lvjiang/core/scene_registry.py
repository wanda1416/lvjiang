"""场景注册表 - 全局函数与缓存

提供场景/区域/坐标/面板/分组的全局注册表函数。
运行时布局数据类（Region / Point / Arrow / Panel / Layout 等）
已拆分至 layout_models.py。
"""

from loguru import logger

from ..i18n import tr
from .config.resolver import get_resolver
from .scene_config import load_scene_manifest
from .scene_definition import SceneRegistry
from .scene_definition_models import (
    BASE_VIEW_KEY,
    PanelDef,
    PointDef,
    RegionDef,
    SubsceneRefDef,
    ViewDef,
)


def _load_manifest():
    """读取并兼容转换 scenes.yaml。"""
    try:
        return load_scene_manifest(get_resolver())
    except Exception as e:
        logger.warning(f"读取 scenes.yaml 失败: {e}")
        return None


# ─── 场景注册表（从 YAML 加载） ─────────────────────────

_manifest = _load_manifest()
_registry = SceneRegistry(
    scene_order=_manifest.order if _manifest else None,
    group_config=_manifest.groups if _manifest else None,
    group_names=_manifest.group_names if _manifest else None,
    disabled_scenes=_manifest.disabled if _manifest else None,
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
    raise RuntimeError(tr("scenes.yaml 必须至少包含一个场景分组，请检查配置"))


def get_registry() -> SceneRegistry:
    """获取场景注册表实例（供 UI 层调用 CRUD 方法）"""
    return _registry


def reload_scene_registry():
    """原位重新加载注册表，避免已打开编辑器持有失效对象。"""
    manifest = _load_manifest()
    refreshed = SceneRegistry(
        scene_order=manifest.order if manifest else None,
        group_config=manifest.groups if manifest else None,
        group_names=manifest.group_names if manifest else None,
        disabled_scenes=manifest.disabled if manifest else None,
    )
    # 配置写入会同步触发本函数。若直接替换模块单例，已打开的区域编辑器、
    # 视图管理器仍会继续修改旧 SceneRegistry，下一次写入就可能把刚保存的
    # to/views 等字段覆盖回去。保留对象身份，只替换其最新磁盘状态。
    _registry.__dict__.clear()
    _registry.__dict__.update(refreshed.__dict__)
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




def _referenced_entity_name(scene, entity_key: str) -> str | None:
    """跨场景引用实体的显示名：引用只存 key，名字在源场景里。

    引用项在本场景的 regions/points 里查不到，名称查询若就此退回 key，画布
    标签和列表里的引用行就会和本场景原生实体长得不一样。统一回源取名。
    """
    for ref in getattr(scene, "references", ()):
        if ref.entity != entity_key:
            continue
        source = _registry.get_scene(ref.scene)
        if source is None:
            continue
        entity = next(
            (e for e in (*source.regions, *source.points)
             if e.key == entity_key), None)
        if entity is not None:
            return entity.name
    return None


def get_region_name(scene_key: str, region_key: str) -> str:
    """通过 scene_key + region_key 查找区域中文名（含跨场景引用）"""
    scene = _registry.get_scene(scene_key)
    if scene:
        for r in scene.regions:
            if r.key == region_key:
                return r.name
        referenced = _referenced_entity_name(scene, region_key)
        if referenced is not None:
            return referenced
    return region_key


def get_point_name(scene_key: str, point_key: str) -> str:
    """通过 scene_key + point_key 查找坐标点中文名（含跨场景引用）"""
    scene = _registry.get_scene(scene_key)
    if scene:
        for p in scene.points:
            if p.key == point_key:
                return p.name
        referenced = _referenced_entity_name(scene, point_key)
        if referenced is not None:
            return referenced
    return point_key


def get_region_defs(scene_key: str) -> list[RegionDef]:
    """获取场景的完整区域定义列表"""
    scene = _registry.get_scene(scene_key)
    if not scene:
        return []
    return list(scene.regions)


def is_subscene(scene_key: str) -> bool:
    scene = _registry.get_scene(scene_key)
    return bool(scene and scene.is_subscene)


def get_region_def(scene_key: str, region_key: str) -> RegionDef | None:
    """获取场景内指定 region 的类型定义（与 get_point_def 对称）"""
    scene = _registry.get_scene(scene_key)
    if not scene:
        return None
    return next((r for r in scene.regions if r.key == region_key), None)


def get_subscene_ref_defs(scene_key: str) -> list[SubsceneRefDef]:
    scene = _registry.get_scene(scene_key)
    return list(scene.subscene_refs) if scene else []


def get_subscene_ref_def(scene_key: str, ref_key: str) -> SubsceneRefDef | None:
    return next((r for r in get_subscene_ref_defs(scene_key) if r.key == ref_key), None)


def get_subscene_scenes() -> list[tuple[str, str]]:
    return [(key, scene.name) for key, scene in _registry.all_scenes().items()
            if scene.is_subscene]


# ─── 视图 ───────────────────────────────────────────────

def get_scene_views(scene_key: str) -> list[ViewDef]:
    """获取场景视图列表（空 = 未开启多视图）"""
    return _registry.get_scene_views(scene_key)


def is_view_visible(item_view: str | list[str], current_view: str) -> bool:
    """当前视图下某定义是否可见

    current_view 为空 = 看全部；选定视图时只看该视图自身的定义
    （基底是普通视图，不叠加展示；定义的 view 字段为空等价于归属基底）。

    ``item_view`` 接受单值或**归属视图列表**：同一个按钮可以同时属于多个视图
    （``close_btn`` 在结果视图和返还视图都在），只要命中其一就可见。
    """
    if not current_view:
        return True
    views = ([item_view] if isinstance(item_view, str)
             else list(item_view or []))
    if not views:
        views = [""]
    if current_view == BASE_VIEW_KEY:
        return any(v in ("", BASE_VIEW_KEY) for v in views)
    return current_view in views


def get_view_visible_keys(scene_key: str, current_view: str) -> set[str] | None:
    """当前视图下可见的全部定义 key（看全部时返回 None 表示不过滤）"""
    if not current_view:
        return None
    scene = _registry.get_scene(scene_key)
    if not scene:
        return set()
    return {
        # references 必须算进来：引用项的坐标已在布局加载时展开进本场景，
        # 但视图过滤是按场景定义算的——漏掉它们，选中视图后画布就不画引用项，
        # 而它们明明在右侧列表里。
        i.key
        for i in (*scene.regions, *scene.points, *scene.panels,
                  *scene.subscene_refs, *scene.references)
        if is_view_visible(i.views, current_view)
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
