"""场景注册表 - 全局函数与缓存

提供场景/区域/坐标/面板/分组的全局注册表函数。
运行时布局数据类（Region / Point / Arrow / Panel / Layout 等）
已拆分至 layout_models.py。
"""

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import replace

from loguru import logger

from ..config.resolver import get_root_resolver
from .config import load_scene_manifest
from .definition import SceneRegistry
from .definition_models import (
    BASE_VIEW_KEY,
    PanelDef,
    PointDef,
    RegionDef,
    SubsceneRefDef,
    ViewDef,
    valid_references,
)


def _load_manifest():
    """读取目录化场景配置的合并视图。"""
    # 兜底迁移：run_app 之外的读取路径（vscode LSP、独立脚本、测试）也可能
    # 首个触达场景注册表；未迁移的旧聚合文件必须在这里拆分，否则旧平铺
    # 场景文件会被当成顶层分组定义误读。已迁移时是两次 stat 的空操作。
    from ..config.content_split import prepare_content_split
    resolver = get_root_resolver()
    prepare_content_split(resolver)
    try:
        return load_scene_manifest(resolver)
    except Exception as e:
        logger.warning(f"读取场景分组配置失败: {e}")
        return None


# ─── 场景注册表（从 YAML 加载） ─────────────────────────

# 模块导入不构造注册表：构造会立刻 _load_all 并打印每条场景日志，任何一条
# 模块级 ``from .scene.registry import ...``（core.ocr.engine、core.layout.manager
# 等）都会因此抢在插件注册完成前触发场景加载。改为显式初始化：启动时由
# run_app 调用 initialize_scene_registry()，首次 get_registry() /
# reload_scene_registry() 也会兜底初始化。
_registry: SceneRegistry | None = None

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
    assert _registry is not None  # 仅在 initialize_scene_registry / reload 内调用
    scenes = _registry.all_scenes()
    # 先把新内容算完再替换：clear() 与 update() 之间是空窗，后台线程此刻读到的
    # 是空 dict，会当成「这个场景没有区域」继续跑下去。
    regions: dict[str, tuple[str, list[tuple[str, str]]]] = {
        key: (scene.name, [(r.key, r.name) for r in scene.regions])
        for key, scene in scenes.items()
    }
    points: dict[str, list[tuple[str, str]]] = {
        key: [(p.key, p.name) for p in scene.points]
        for key, scene in scenes.items()
    }
    panels: dict[str, list[tuple[str, str]]] = {
        key: [(p.key, p.name) for p in scene.panels]
        for key, scene in scenes.items()
    }
    groups_meta: dict[str, str] = {}
    group_scenes: dict[str, list[str]] = {}
    group_order: list[str] = []
    for gk, gname in _registry.get_groups():
        groups_meta[gk] = gname
        group_scenes[gk] = _registry.get_group_scenes(gk)
        group_order.append(gk)

    SCENE_REGIONS.clear()
    SCENE_REGIONS.update(regions)
    SCENE_POINTS.clear()
    SCENE_POINTS.update(points)
    SCENE_PANELS.clear()
    SCENE_PANELS.update(panels)
    # 重建分组缓存
    SCENE_GROUPS_META.clear()
    SCENE_GROUPS_META.update(groups_meta)
    GROUP_SCENES.clear()
    GROUP_SCENES.update(group_scenes)
    GROUP_ORDER.clear()
    GROUP_ORDER.extend(group_order)


def initialize_scene_registry() -> None:
    """显式初始化场景注册表（首次加载）；幂等。

    启动时在插件全部注册完成之后调用；后续场景变化走
    :func:`reload_scene_registry` 原位更新，不再经过这里。
    """
    global _registry
    if _registry is not None:
        return
    manifest = _load_manifest()
    _registry = SceneRegistry(
        resolver=get_root_resolver(),
        scene_order=manifest.order if manifest else None,
        group_config=manifest.groups if manifest else None,
        group_names=manifest.group_names if manifest else None,
        disabled_scenes=manifest.disabled if manifest else None,
        group_layers=manifest.group_layers if manifest else None,
        scene_layers=manifest.scene_layers if manifest else None,
    )
    _rebuild_scene_globals()
    # 配置可暂时没有场景分组，不能在首次加载时阻断应用启动。
    if not _registry.get_groups():
        logger.warning("未加载场景分组，场景相关功能暂不可用；请检查 scenes/ 目录，不影响程序启动")


_scoped_registry: ContextVar[SceneRegistry | None] = ContextVar(
    "scoped_scene_registry", default=None,
)


def set_scoped_registry(registry: SceneRegistry | None):
    """设置当前上下文的场景注册表并返回恢复 token。"""
    return _scoped_registry.set(registry)


def reset_scoped_registry(token) -> None:
    """按 token 恢复上下文场景注册表。"""
    _scoped_registry.reset(token)


@contextmanager
def use_scene_registry(registry: SceneRegistry | None):
    """在上下文内临时切换 get_registry() 返回值，退出时必定还原。"""
    token = _scoped_registry.set(registry)
    try:
        yield
    finally:
        _scoped_registry.reset(token)


def get_registry() -> SceneRegistry:
    """获取场景注册表实例（未进入作用域时返回进程级单例）。"""
    scoped = _scoped_registry.get()
    if scoped is not None:
        return scoped
    initialize_scene_registry()
    assert _registry is not None  # initialize_scene_registry 保证已构造
    return _registry


def _reload_global_scene_registry() -> None:
    """原位重载进程级全局场景注册表，不受当前线程任务包作用域影响。"""
    if _registry is None:
        initialize_scene_registry()
        return
    # 显式重载也服务于 Git 拉取和外部编辑，不依赖写入通知或文件时间戳。
    get_root_resolver().clear_read_cache()
    manifest = _load_manifest()
    refreshed = SceneRegistry(
        resolver=get_root_resolver(),
        scene_order=manifest.order if manifest else None,
        group_config=manifest.groups if manifest else None,
        group_names=manifest.group_names if manifest else None,
        disabled_scenes=manifest.disabled if manifest else None,
        group_layers=manifest.group_layers if manifest else None,
        scene_layers=manifest.scene_layers if manifest else None,
    )
    # 配置写入会同步触发本函数。若直接替换模块单例，已打开的区域编辑器、
    # 视图管理器仍会继续修改旧 SceneRegistry，下一次写入就可能把刚保存的
    # to/views 等字段覆盖回去。保留对象身份，只替换其最新磁盘状态。
    #
    # 替换方式是**逐属性赋值**：旧实现先 __dict__.clear() 再 update()，两步
    # 之间是一张空表，工作流线程此刻读注册表会拿到 AttributeError 或空集合。
    # CPython 里属性赋值是原子的，读者只会看到旧态或新态，不会看到「什么都没有」。
    for name, value in refreshed.__dict__.items():
        setattr(_registry, name, value)
    _rebuild_scene_globals()


def reload_scene_registry():
    """场景配置变化后原位重载，保留对象身份，避免已打开编辑器持有失效对象。

    未初始化时先初始化（兜底）：正常启动由 run_app 显式调用
    initialize_scene_registry，本函数只在场景文件变化后使用。
    若当前处于任务场景作用域，仅原位重载该作用域注册表，不触碰全局单例。
    """
    scoped = _scoped_registry.get()
    if scoped is not None:
        from ..config.resolver import get_resolver
        resolver = get_resolver()
        resolver.clear_read_cache()
        manifest = load_scene_manifest(resolver)
        refreshed = SceneRegistry(
            resolver=resolver,
            scene_order=manifest.order if manifest else None,
            group_config=manifest.groups if manifest else None,
            group_names=manifest.group_names if manifest else None,
            disabled_scenes=manifest.disabled if manifest else None,
            group_layers=manifest.group_layers if manifest else None,
            scene_layers=manifest.scene_layers if manifest else None,
        )
        for name, value in refreshed.__dict__.items():
            setattr(scoped, name, value)
        return
    _reload_global_scene_registry()


def _on_config_change(paths: tuple[str, ...]):
    """配置写入后的失效通知：根解析器场景变更时只重载全局注册表。"""
    if any(path.startswith("scenes/") for path in paths):
        _reload_global_scene_registry()


get_root_resolver().add_batch_change_listener(_on_config_change)




def get_group_name(group_key: str) -> str:
    """获取分组名称"""
    return dict(get_registry().get_groups()).get(group_key, group_key)


def get_scene_group(scene_key: str) -> str | None:
    """获取场景所在分组 key"""
    for gk, _name in get_registry().get_groups():
        if scene_key in get_registry().get_group_scenes(gk):
            return gk
    return None


def sync_scene_cache(scene_key: str):
    """仅刷新单个场景的全局缓存（区域/坐标/面板编辑后调用，避免全量重载）"""
    if _scoped_registry.get() is not None:
        return  # 任务场景作用域内不污染全局字典
    global SCENE_REGIONS, SCENE_POINTS, SCENE_PANELS
    scene = get_registry().get_scene(scene_key)
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
    scene = get_registry().get_scene(scene_key)
    return scene.name if scene else scene_key


def get_scene_regions(scene_key: str) -> list[tuple[str, str]]:
    """获取场景的 (key, name) 区域列表"""
    scene = get_registry().get_scene(scene_key)
    return [(region.key, region.name) for region in scene.regions] if scene else []




def _referenced_entity_name(scene, entity_key: str) -> str | None:
    """跨场景引用实体的显示名：引用只存 key，名字在源场景里。

    引用项在本场景的 regions/points 里查不到，名称查询若就此退回 key，画布
    标签和列表里的引用行就会和本场景原生实体长得不一样。统一回源取名。
    """
    for ref in valid_references(scene):
        if ref.entity != entity_key:
            continue
        source = get_registry().get_scene(ref.scene)
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
    scene = get_registry().get_scene(scene_key)
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
    scene = get_registry().get_scene(scene_key)
    if scene:
        for p in scene.points:
            if p.key == point_key:
                return p.name
        referenced = _referenced_entity_name(scene, point_key)
        if referenced is not None:
            return referenced
    return point_key


def get_panel_name(scene_key: str, panel_key: str) -> str:
    """通过 scene_key + panel_key 查找网格面板中文名。"""
    scene = get_registry().get_scene(scene_key)
    if scene:
        for panel in scene.panels:
            if panel.key == panel_key:
                return panel.name
    return panel_key


def get_region_defs(scene_key: str) -> list[RegionDef]:
    """获取场景的完整区域定义列表"""
    scene = get_registry().get_scene(scene_key)
    if not scene:
        return []
    return list(scene.regions)


def get_effective_region_defs(
    scene_key: str,
    current_view: str = "",
) -> list[RegionDef]:
    """返回当前场景可用的区域定义，包括跨场景引用。

    引用实体沿用源场景的类型、文字识别等属性，但视图归属属于目标场景，
    因而以 ``SceneRefDef.views`` 覆盖源定义。``current_view`` 为空表示“全部”。
    """
    scene = get_registry().get_scene(scene_key)
    if scene is None:
        return []
    definitions = [
        region for region in scene.regions
        if is_view_visible(region.views, current_view)
    ]
    for ref in valid_references(scene):
        if not is_view_visible(ref.views, current_view):
            continue
        source = get_registry().get_scene(ref.scene)
        if source is None:
            continue
        source_def = next(
            (region for region in source.regions
             if region.key == ref.entity),
            None,
        )
        if source_def is not None:
            definitions.append(replace(source_def, views=list(ref.views)))
    return definitions


def is_subscene(scene_key: str) -> bool:
    scene = get_registry().get_scene(scene_key)
    return bool(scene and scene.is_subscene)


def get_region_def(scene_key: str, region_key: str) -> RegionDef | None:
    """获取场景内指定 region 的类型定义（与 get_point_def 对称）"""
    scene = get_registry().get_scene(scene_key)
    if not scene:
        return None
    return next((r for r in scene.regions if r.key == region_key), None)


def get_subscene_ref_defs(scene_key: str) -> list[SubsceneRefDef]:
    scene = get_registry().get_scene(scene_key)
    return list(scene.subscene_refs) if scene else []


def get_subscene_ref_def(scene_key: str, ref_key: str) -> SubsceneRefDef | None:
    return next((r for r in get_subscene_ref_defs(scene_key) if r.key == ref_key), None)


def get_subscene_scenes() -> list[tuple[str, str]]:
    return [(key, scene.name) for key, scene in get_registry().all_scenes().items()
            if scene.is_subscene]


# ─── 视图 ───────────────────────────────────────────────

def get_scene_views(scene_key: str) -> list[ViewDef]:
    """获取场景视图列表（空 = 未开启多视图）"""
    return get_registry().get_scene_views(scene_key)


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
    scene = get_registry().get_scene(scene_key)
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
    scene = get_registry().get_scene(scene_key)
    return [(point.key, point.name) for point in scene.points] if scene else []


def get_point_defs(scene_key: str) -> list[PointDef]:
    """获取场景的完整坐标点定义列表"""
    scene = get_registry().get_scene(scene_key)
    if not scene:
        return []
    return list(scene.points)


def get_point_def(scene_key: str, point_key: str) -> PointDef | None:
    """获取场景内指定 point 的类型定义"""
    scene = get_registry().get_scene(scene_key)
    if not scene:
        return None
    return next((p for p in scene.points if p.key == point_key), None)




def get_panel_defs(scene_key: str) -> list[PanelDef]:
    """获取场景的完整面板定义列表"""
    scene = get_registry().get_scene(scene_key)
    if not scene:
        return []
    return list(scene.panels)
