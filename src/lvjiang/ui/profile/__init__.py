"""主引擎内置的 Profile 用户页面。"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .dialogs import ProfileDefinitionDialog
    from .tab import ProfileTab
    from .user_info import UserInfoTab

__all__ = [
    "ProfileDefinitionDialog",
    "ProfileTab",
    "UserInfoTab",
]


def __getattr__(name: str):
    if name == "ProfileTab":
        from .tab import ProfileTab
        return ProfileTab
    if name == "UserInfoTab":
        from .user_info import UserInfoTab
        return UserInfoTab
    if name == "ProfileDefinitionDialog":
        from .dialogs import ProfileDefinitionDialog as _cls  # type: ignore[assignment]
        return _cls
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return __all__
