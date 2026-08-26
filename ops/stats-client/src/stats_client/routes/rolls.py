"""调律分析页：筛选表单 + 生成/下载报告 + 槽位条件查询。

MVP 阶段等价覆盖 ``scripts/analyze_telemetry_rolls.py`` 的全部小节（复用同
一份 ``telemetry_analysis`` 逻辑，见 ``analysis_bridge.py``），不做结构化
结论卡——那是 README/方案里明确写的第二阶段。Web 阅读版目前是等宽 <pre>
原样展示 Markdown，不接 markdown 渲染库（保持依赖精简，等真需要交互式
筛选/图表联动时再评估）。

「槽位条件查询」面板是例外——终态重建口径的槽位分析天然是参数化查询
（部位 × 首词条 × 目标格位 × 条件格位组合太多，写不进固定报告，见
``sections.section_slot``），所以这里做成交互表单，不是静态报告小节。
"""
from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, Request
from fastapi.responses import PlainTextResponse, RedirectResponse

from .. import config as config_mod
from .. import vocab as vocab_mod
from ..analysis_bridge import (
    available_target_slots,
    build_report_from_cache,
    parse_slot_range,
    run_slot_query,
    slot_items_from_cache,
)
from ..deps import get_cfg, get_conn
from ..templates_env import templates

router = APIRouter()


def _parse_params(request: Request) -> dict:
    q = request.query_params
    return {
        "target_affix": q.get("target_affix") or None,
        "min_version": q.get("min_version") or None,
        "since": q.get("since") or None,
        "max_per_install": (
            int(q["max_per_install"]) if q.get("max_per_install") else None),
        "top": int(q["top"]) if q.get("top") else 15,
    }


def _parse_slot_params(request: Request) -> dict:
    q = request.query_params
    return {
        "part": q.get("slot_part") or None,
        "first_affix": q.get("slot_first_affix") or None,
        "given_slot": int(q["slot_given_slot"]) if q.get("slot_given_slot") else None,
        "given_affix": q.get("slot_given_affix") or None,
        "target": q.get("slot_target") or "",
    }


@router.get("/rolls")
def rolls_page(request: Request,
               cfg: config_mod.Config = Depends(get_cfg),
               conn: sqlite3.Connection = Depends(get_conn)):
    if not cfg.is_ready() or not config_mod.get_api_token(cfg):
        return RedirectResponse("/setup", status_code=303)

    params = _parse_params(request)
    report = None
    error = None
    if request.query_params.get("run") == "1":
        try:
            report = build_report_from_cache(conn, **params)
        except ValueError as e:
            error = str(e)

    # 槽位条件查询：轻量单独跑，不依赖上面那份完整报告是否生成过
    slot_params = _parse_slot_params(request)
    slot_stat = None
    slot_error = None
    if slot_params["target"]:
        try:
            target_lo, target_hi = parse_slot_range(slot_params["target"])
            if bool(slot_params["given_slot"]) != bool(slot_params["given_affix"]):
                raise ValueError("「给定格位」和「给定词条」必须一起填")
            slot_stat = run_slot_query(
                conn, part=slot_params["part"],
                first_affix=slot_params["first_affix"],
                given_slot=slot_params["given_slot"],
                given_affix=slot_params["given_affix"],
                target_lo=target_lo, target_hi=target_hi)
        except ValueError as e:
            slot_error = str(e)

    # 部位/首词条的候选值来自 game_config.yaml 的规范枚举快照（见
    # vocab.py），不是本地缓存里"碰巧出现过"的值——缓存样本少时后者会漏掉
    # 大半可能取值，用户根本没法知道该填什么去查一个还没见过的组合。
    # 「目标格位」没有规范枚举来源（游戏没有公开"某部位有几格"的配置），
    # 只能退回已同步数据里实际见过的格位号。
    vocab = vocab_mod.load_vocab()
    items = slot_items_from_cache(conn)
    return templates.TemplateResponse(request, "rolls.html", {
        "cfg": cfg, "params": params, "report": report, "error": error,
        "slot_params": slot_params, "slot_stat": slot_stat, "slot_error": slot_error,
        "part_labels": vocab.get("part_labels", {}),
        "available_parts": vocab.get("parts", []),
        "available_first_affixes": vocab_mod.affixes_for_part(
            vocab, slot_params["part"]),
        "available_target_slots": available_target_slots(items, slot_params["part"]),
    })


@router.get("/rolls/report.md")
def rolls_download(request: Request,
                   conn: sqlite3.Connection = Depends(get_conn)):
    params = _parse_params(request)
    try:
        report = build_report_from_cache(conn, **params)
    except ValueError as e:
        return PlainTextResponse(str(e), status_code=400)
    return PlainTextResponse(
        report.text, media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="report.md"'})
