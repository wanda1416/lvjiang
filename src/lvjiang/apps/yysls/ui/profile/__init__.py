"""Profile UI 模块

对外导出三类组件：
- ProfileOverviewTab: 档案总览 Tab（宽表 + 分组管理）
- ProfileTab: 其他信息 Tab（当前用户详情）
- ProfileDefinitionDialog: 数据模型定义对话框

采用延迟导入：仅在首次访问某个符号时才加载对应子模块，
避免触发 PyQt6 的连带加载，保持"插件 import 不触发 PyQt6"约定。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .dialogs import ProfileDefinitionDialog
    from .overview import ProfileOverviewTab
    from .tab import ProfileTab

__all__ = [
    "ProfileDefinitionDialog",
    "ProfileOverviewTab",
    "ProfileTab",
]


def __getattr__(name: str):
    if name == "ProfileOverviewTab":
        from .overview import ProfileOverviewTab as _cls  # type: ignore[assignment]
        return _cls
    if name == "ProfileTab":
        from .tab import ProfileTab as _cls  # type: ignore[assignment]
        return _cls
    if name == "ProfileDefinitionDialog":
        from .dialogs import ProfileDefinitionDialog as _cls  # type: ignore[assignment]
        return _cls
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return __all__
