"""共享的 Jinja2Templates 实例，避免每个路由模块各建一份。"""
from __future__ import annotations

from pathlib import Path

from fastapi.templating import Jinja2Templates

_BASE_DIR = Path(__file__).resolve().parents[2]  # ops/stats-client/
templates = Jinja2Templates(directory=str(_BASE_DIR / "templates"))
