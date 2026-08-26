"""薄封装：把本地 ``remote_roll_batch`` 缓存喂给 ``scripts/telemetry_analysis``。

不 subprocess 调用 CLI，运行时把仓库根的 ``scripts/`` 目录插进 ``sys.path``
后直接 import——这是刻意的轻耦合：``scripts/`` 不是一个可安装依赖（项目的
可安装包边界只到 ``src/lvjiang/``，见主 ``pyproject.toml``
``[tool.setuptools.packages.find]`` 的注释），这里用运行时路径注入维持
这个边界，而不是新开一个安装单元。见 ``ops/stats-client/README.md``。
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

# parents[4]：analysis_bridge.py -> stats_client -> src -> stats-client -> ops -> 仓库根
_REPO_ROOT = Path(__file__).resolve().parents[4]
_SCRIPTS_DIR = _REPO_ROOT / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from telemetry_analysis import MIN_CELL_N, SCHEMA_NAME, build_report  # noqa: E402
from telemetry_analysis.loader import _events_from_rows  # noqa: E402
from telemetry_analysis.metrics import PART_LABELS  # noqa: E402
from telemetry_analysis.render_md import Report  # noqa: E402
from telemetry_analysis.slots import (  # noqa: E402
    SlotStat,
    conditional_slot_distribution,
    observed_slots,
    parse_slot_range,
    reconstruct_all,
    slot_distribution,
    slot_range_distribution,
)

__all__ = ["MIN_CELL_N", "SCHEMA_NAME", "Report", "SlotStat", "PART_LABELS",
          "load_cached_events", "build_report_from_cache",
          "slot_items_from_cache", "run_slot_query", "parse_slot_range",
          "available_parts", "available_first_affixes", "available_target_slots"]


def load_cached_events(
    conn: sqlite3.Connection, since: str | None = None,
) -> list[dict]:
    """从本地 ``remote_roll_batch`` 缓存展开事件列表。

    行形状与 ``wrangler d1 execute --json`` 导出的一样（含 ``payload`` 列），
    ``telemetry_analysis.loader._events_from_rows`` 本就认识这种形状——
    这正是当初把它做成"识别多种输入形态"的原因之一。
    """
    sql = ("SELECT batch_id, install_id, day, app_version, plugin, n_events, "
           "payload, received_at FROM remote_roll_batch")
    params: list = []
    if since:
        # 按 batch 的 day 预筛，避免解码用不上的旧 JSON；事件自己的 date
        # 字段仍会在 build_report 里再筛一遍，这里只是性能优化不是精确口径
        # （批次 day 与事件 date 允许 ±2 天误差，见 stats-worker schema.sql）。
        sql += " WHERE day >= ?"
        params.append(since)
    rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    return _events_from_rows(rows)


def build_report_from_cache(
    conn: sqlite3.Connection,
    *,
    target_affix: str | None = None,
    min_version: str | None = None,
    since: str | None = None,
    max_per_install: int | None = None,
    seed: int = 20260826,
    top: int = 15,
) -> Report:
    events = load_cached_events(conn, since=since)
    if not events:
        raise ValueError("本地缓存里还没有调律事件——先去「数据与同步」页拉一次数据")
    return build_report(
        events,
        source_label="本地缓存 remote_roll_batch",
        target_affix=target_affix,
        min_version=min_version,
        since=since,
        max_per_install=max_per_install,
        seed=seed,
        top=top,
    )


def slot_items_from_cache(conn: sqlite3.Connection, since: str | None = None):
    """本地缓存 → 终态重建后的槽位快照列表，已排除转律件。给「槽位条件
    查询」面板用——比整份 :func:`build_report_from_cache` 轻，不用每次
    查询都跑一遍全部 8 节。"""
    events = load_cached_events(conn, since=since)
    return [it for it in reconstruct_all(events) if not it.is_transferred_any]


def run_slot_query(
    conn: sqlite3.Connection, *, part: str | None = None,
    first_affix: str | None = None, given_slot: int | None = None,
    given_affix: str | None = None, target_lo: int, target_hi: int,
    since: str | None = None,
) -> SlotStat:
    items = slot_items_from_cache(conn, since=since)
    if given_slot is not None and given_affix:
        return conditional_slot_distribution(
            items, given_slot=given_slot, given_affix=given_affix,
            target_lo=target_lo, target_hi=target_hi,
            part=part, first_affix=first_affix)
    if target_lo == target_hi:
        return slot_distribution(items, target_lo, part=part, first_affix=first_affix)
    return slot_range_distribution(items, target_lo, target_hi,
                                   part=part, first_affix=first_affix)


def available_parts(items) -> list[str]:
    return sorted({it.part for it in items if it.part})


def available_first_affixes(items, part: str | None = None) -> list[str]:
    sub = [it for it in items if not part or it.part == part]
    return sorted({it.slots[1] for it in sub if 1 in it.slots})


def available_target_slots(items, part: str | None = None) -> list[int]:
    sub = [it for it in items if not part or it.part == part]
    return [s for s in observed_slots(sub) if s != 1]
