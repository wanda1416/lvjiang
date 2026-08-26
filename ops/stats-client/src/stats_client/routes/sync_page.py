"""数据与同步页：连接状态、同步历史、立即同步、清空本地缓存。"""
from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, RedirectResponse

from .. import config as config_mod
from ..cloudflare import D1Client, D1ConnectionError, D1Error
from ..deps import get_cfg, get_client, get_conn
from ..sync import Syncer
from ..templates_env import templates

router = APIRouter()


def _local_ranges(conn: sqlite3.Connection) -> dict:
    def _range(table: str, col: str = "day") -> tuple[str | None, str | None]:
        row = conn.execute(
            f"SELECT MIN({col}) AS lo, MAX({col}) AS hi FROM {table}").fetchone()
        return (row["lo"], row["hi"]) if row else (None, None)

    daily_lo, daily_hi = _range("remote_daily")
    roll_lo, roll_hi = _range("remote_roll_batch")
    return {"daily": (daily_lo, daily_hi), "roll_batch": (roll_lo, roll_hi)}


@router.get("/sync")
def sync_page(request: Request,
             cfg: config_mod.Config = Depends(get_cfg),
             conn: sqlite3.Connection = Depends(get_conn)):
    if not cfg.is_ready() or not config_mod.get_api_token(cfg):
        return RedirectResponse("/setup", status_code=303)

    runs = conn.execute(
        "SELECT * FROM sync_runs ORDER BY id DESC LIMIT 20").fetchall()
    cursors = conn.execute("SELECT * FROM sync_cursor").fetchall()
    return templates.TemplateResponse(request, "sync.html", {
        "cfg": cfg, "runs": runs, "cursors": cursors,
        "ranges": _local_ranges(conn),
    })


@router.post("/sync/run")
def sync_run(cfg: config_mod.Config = Depends(get_cfg),
            conn: sqlite3.Connection = Depends(get_conn),
            client: D1Client | None = Depends(get_client)):
    if client is None:
        return JSONResponse({"ok": False, "error": "尚未配置有效凭据"}, status_code=400)
    try:
        result = Syncer(conn, client).run()
    except (D1Error, D1ConnectionError) as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=502)
    return JSONResponse({
        "ok": result.ok,
        "duration_ms": result.duration_ms,
        "tables": [t.as_dict() for t in result.tables],
    })


@router.post("/sync/clear-cache")
def clear_cache(cfg: config_mod.Config = Depends(get_cfg)):
    """删除本地缓存文件（含 install_id 的原始数据）。远端不受影响，下次
    同步会按"首次同步"逻辑从 90 天保留窗口重新拉一遍。"""
    for suffix in ("", "-wal", "-shm"):
        p = cfg.db_path.with_name(cfg.db_path.name + suffix)
        p.unlink(missing_ok=True)
    return RedirectResponse("/sync", status_code=303)
