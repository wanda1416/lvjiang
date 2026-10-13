"""宿主与插件之间的通用事件信封；不依赖 GUI。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class AppEvent:
    app_id: str
    topic: str
    payload: Any = None
