"""宿主与 app UI 之间的通用事件信封。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class AppEvent:
    app_id: str
    topic: str
    payload: Any = None
