"""兼容入口：``AppHooks`` 契约已迁到宿主 ``lvjiang.core.plugins.hooks``。"""
from __future__ import annotations

from ..core.plugins.hooks import AppHooks, TelemetryDisclosure

__all__ = ["AppHooks", "TelemetryDisclosure"]
