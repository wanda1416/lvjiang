"""FastAPI 依赖：配置、DB 连接、D1 客户端。每个请求各开一条 sqlite 连接，
用完即关——个人本机工具的请求量级用不上连接池，这样也彻底避开
sqlite3 连接跨线程共享的麻烦（Starlette 同步视图函数跑在线程池里）。
"""
from __future__ import annotations

import sqlite3
from collections.abc import Iterator

from fastapi import Depends

from . import config as config_mod
from .cloudflare import D1Client
from .database import connect


def get_cfg() -> config_mod.Config:
    return config_mod.load_config()


def get_conn(cfg: config_mod.Config = Depends(get_cfg)) -> Iterator[sqlite3.Connection]:
    conn = connect(cfg.db_path)
    try:
        yield conn
    finally:
        conn.close()


def get_client(cfg: config_mod.Config = Depends(get_cfg)) -> D1Client | None:
    token = config_mod.get_api_token(cfg)
    if not (cfg.is_ready() and token):
        return None
    return D1Client(account_id=cfg.account_id, api_token=token,
                    database_id=cfg.database_id)
