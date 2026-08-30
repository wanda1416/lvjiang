"""布局对象的运行时可用性检查。

静态检查只验证脚本引用是否在布局中定义；``disabled`` 对象仍算已绑定，
以便同一份脚本适配能力不同的布局。真正执行到该对象时，必须在读取坐标
前中断，避免旧布局中的零坐标占位被当成有效坐标使用。
"""

from typing import TypeVar

from .errors import WorkflowUserError

T = TypeVar("T")

_KIND_NAMES = {
    "region": "区域",
    "point": "坐标点",
    "arrow": "方向",
    "panel": "面板",
    "subscene_ref": "子场景引用",
}


def resolve_subscene_target_scene(parent_scene: str, reference_key: str) -> str:
    """返回引用指向的真实子场景 key，并校验引用定义。"""
    from ..core.scene_registry import get_subscene_ref_def, is_subscene

    ref_def = get_subscene_ref_def(parent_scene, reference_key)
    if ref_def is None:
        raise WorkflowUserError(
            f"场景 {parent_scene} 未定义子场景引用: {reference_key}")
    if not is_subscene(ref_def.scene):
        raise WorkflowUserError(f"引用目标不是子场景: {ref_def.scene}")
    return ref_def.scene


def resolve_subscene_entity(layout, parent_scene: str, reference_key: str,
                            entity_key: str):
    """把子场景局部实体组合为父场景画布中的临时运行时实体。"""
    from ..core.layout_models import Panel, Point, Region

    target_scene = resolve_subscene_target_scene(parent_scene, reference_key)
    instances = layout.get_scene_subscene_refs(parent_scene)
    instance = next((r for r in instances if r.key == reference_key), None)
    if instance is None:
        raise WorkflowUserError(
            f"场景 {parent_scene} 的子场景引用未绑定坐标: {reference_key}")
    require_enabled(instance, parent_scene, "subscene_ref")
    child = next((r for r in layout.get_scene_regions(target_scene)
                  if r.key == entity_key), None)
    if child is not None:
        require_enabled(child, target_scene, "region")
        return Region(
            key=entity_key,
            x_ratio=instance.x_ratio + child.x_ratio * instance.w_ratio,
            y_ratio=instance.y_ratio + child.y_ratio * instance.h_ratio,
            w_ratio=child.w_ratio * instance.w_ratio,
            h_ratio=child.h_ratio * instance.h_ratio,
        )
    point = next((p for p in layout.get_scene_points(target_scene)
                  if p.key == entity_key), None)
    if point is not None:
        require_enabled(point, target_scene, "point")
        return Point(
            key=entity_key,
            cx_ratio=instance.x_ratio + point.cx_ratio * instance.w_ratio,
            cy_ratio=instance.y_ratio + point.cy_ratio * instance.h_ratio,
            r_ratio=point.r_ratio * min(instance.w_ratio, instance.h_ratio),
        )
    panel = next((p for p in layout.get_scene_panels(target_scene)
                  if p.key == entity_key), None)
    if panel is not None:
        require_enabled(panel, target_scene, "panel")
        return Panel(
            key=entity_key,
            x_ratio=instance.x_ratio + panel.x_ratio * instance.w_ratio,
            y_ratio=instance.y_ratio + panel.y_ratio * instance.h_ratio,
            w_ratio=panel.w_ratio * instance.w_ratio,
            h_ratio=panel.h_ratio * instance.h_ratio,
            cols=panel.cols, rows=panel.rows,
            min_visible=panel.min_visible, calibration=panel.calibration,
            scroll_direction=panel.scroll_direction,
        )
    raise WorkflowUserError(
        f"子场景 {target_scene} 的实体未绑定坐标: {entity_key}")


def resolve_subscene_region(layout, parent_scene: str, reference_key: str,
                            entity_key: str):
    """解析只接受矩形区域的 scan/recognize 目标。"""
    from ..core.layout_models import Region
    item = resolve_subscene_entity(
        layout, parent_scene, reference_key, entity_key)
    if not isinstance(item, Region):
        raise WorkflowUserError(
            f"子场景实体 {entity_key} 不是区域，不能用于 scan/recognize")
    return item


def require_enabled(item: T, scene_key: str, kind: str) -> T:
    """返回可用布局对象；对象被禁用时抛出用户可见的运行时错误。"""
    # 布局模型持有真正的 bool。使用 ``is True``，避免测试替身或旧式动态
    # 对象上不存在该属性时，MagicMock 一类的惰性属性被误判为已禁用。
    if getattr(item, "disabled", False) is True:
        kind_name = _KIND_NAMES.get(kind, kind)
        key = getattr(item, "key", "?")
        raise WorkflowUserError(
            f"当前布局中的{kind_name} [{scene_key}].[{key}] 已禁用，无法在运行时访问"
        )
    return item


def require_regions_enabled(regions: list[T], scene_key: str) -> list[T]:
    """检查本次显式选中的所有区域。"""
    for region in regions:
        require_enabled(region, scene_key, "region")
    return regions


def enabled_regions(regions: list[T]) -> list[T]:
    """整场景扫描时过滤当前布局中不可用的区域。"""
    return [r for r in regions if getattr(r, "disabled", False) is not True]
