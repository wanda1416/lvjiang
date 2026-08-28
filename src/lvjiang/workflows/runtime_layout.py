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
}


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
