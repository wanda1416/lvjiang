"""设备应用生命周期公共能力门面。

工作流层只依赖本模块，不直接依赖 ``core.android`` 平台实现。当前桌面宿主
提供 ADB Android 实现；未来其他设备后端可在不改 DSL 的前提下实现同一接口。
"""

from .android.app_controller import AndroidAppController, AndroidAppError

__all__ = ["AndroidAppController", "AndroidAppError"]
