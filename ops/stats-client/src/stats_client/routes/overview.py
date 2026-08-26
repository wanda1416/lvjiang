"""总览页：用户量/活跃度顶部卡片 + DAU 趋势图。"""
from __future__ import annotations

import json
import sqlite3

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse

from .. import config as config_mod
from .. import metrics_user
from ..deps import get_cfg, get_conn
from ..templates_env import templates

router = APIRouter()


@router.get("/")
def overview(request: Request,
             cfg: config_mod.Config = Depends(get_cfg),
             conn: sqlite3.Connection = Depends(get_conn)):
    if not cfg.is_ready() or not config_mod.get_api_token(cfg):
        return RedirectResponse("/setup", status_code=303)

    ov = metrics_user.overview(conn)
    series = metrics_user.dau_series(conn, days=90)
    retention = metrics_user.retention_cohort(conn)
    return templates.TemplateResponse(request, "overview.html", {
        "cfg": cfg, "ov": ov,
        "series_json": json.dumps(series, ensure_ascii=False),
        "version_dist": metrics_user.version_dist(conn),
        "platform_dist": metrics_user.platform_dist(conn),
        "retention": retention,
    })
