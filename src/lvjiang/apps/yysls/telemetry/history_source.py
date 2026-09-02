"""从本地调律历史投影最近七天的匿名统计事件。

一条事件 = 一件装备从进调律页面到离开。事件不再由调律主流程边跑边攒
（原 ``probe.py`` 的做法，已删除），而是事后从结构化历史 ``_project`` 投影，
因此采集与调律执行彻底解耦：投影失败只影响统计，不可能波及调律。

**整条丢弃原则**：一次会话里只要有任何一轮的词条没能精确命中普通词条池，
``_project`` 返回 ``None`` 整条作废，而不是只丢那一轮。理由是分析正确性而非
隐私——序列里挖个洞之后，第 4 轮会被下游当成"紧跟第 2 轮"，条件概率直接算错。
宁可少一条，不要一条错的。

**词条名的 PII 收敛有两层，缺一不可**：

1. 上游 ``parser._parse_single_affix`` 匹配成功时赋的是白名单条目本身而非
   OCR 原文，匹配不上返回 None。唯一的例外是 ``WUXUE_PATTERN``
   （``^(.+?)武学增[伤效]``）会把 OCR 匹配段整体当作词条名；
2. 本模块的 ``vocab.normalize_affix_name`` 要求精确命中普通词条池，其中武学
   词条是穷举列好的具体武器，所以拼不出来的名字到不了这里。
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone

from loguru import logger

from ....core.telemetry import identity as identity_mod
from ....core.telemetry.sources import SourceBatch, register_source
from ..tuning_history.repository import (
    TuningHistoryRepository,
    default_db_path,
)
from . import vocab
from .schemas import TUNING_SESSION_SCHEMA

LOOKBACK_DAYS = 7
BATCH_SIZE = 50
# 投影抛异常时不判死刑，但也不能让同一批坏行每次都占满查询窗口造成队头
# 阻塞——退避这么久之后才再试一次。
RETRY_BACKOFF_HOURS = 12


def _json_value(raw, default):
    if not raw:
        return default
    try:
        return json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return default


def _affix_entry(raw: dict | None) -> dict | None:
    if not isinstance(raw, dict):
        return None
    name = vocab.normalize_affix_name(raw.get("name"))
    if name is None:
        return None
    entry = {
        "affix": name,
        "is_transferred": bool(raw.get("is_transferred")),
    }
    cap_pct = raw.get("cap_pct")
    if isinstance(cap_pct, (int, float)) and not isinstance(cap_pct, bool):
        entry["cap_pct"] = float(cap_pct)
    return entry


def _project(row: dict, install_id: str) -> dict | None:
    part = vocab.normalize_part(row.get("equip_type"))
    level = row.get("level")
    if part is None or not isinstance(level, (int, float)) or level <= 0:
        return None

    initial: list[dict] = []
    for affix in _json_value(row.get("initial_affixes_json"), []):
        entry = _affix_entry(affix)
        if entry is None:
            return None
        initial.append(entry)

    rolls: list[dict] = []
    for detail in _json_value(row.get("round_details_json"), []):
        # 本轮可能因狗粮不足策略而在准备阶段停止。该决策保留在本地历史，
        # 但没有实际调律产出，不能伪装成匿名统计中的一次 roll。
        if detail.get("completed") is False:
            continue
        entry = _affix_entry(detail.get("new_affix_data"))
        food = vocab.normalize_food(detail.get("food_used"))
        slot = detail.get("affix_count")
        resets = detail.get("resets", 0)
        if (entry is None or food is None
                or not isinstance(slot, int) or not 1 <= slot <= 5
                or not isinstance(resets, int)):
            return None
        entry.update({"slot": slot, "food": food, "resets": resets})
        rolls.append(entry)

    rule_snapshot = _json_value(row.get("rule_snapshot_json"), [])
    rule_keys = [item.get("key") for item in rule_snapshot
                 if isinstance(item, dict) and item.get("key")]
    config = _json_value(row.get("config_snapshot_json"), {})
    started = str(row.get("tuning_started_at") or "")
    fields = {
        "event_id": row["event_id"],
        "install_id": install_id,
        "date": started[:10],
        "part": part,
        "level": int(level),
        "mode": row.get("tuning_mode") or "normal",
        "active_rule": vocab.normalize_active_rule(rule_keys),
        "game_config_customized": bool(
            config.get("game_config_customized", False)),
        "initial_affixes": initial,
        "rolls": rolls,
        "stop_reason": vocab.normalize_stop_reason(
            row.get("telemetry_stop_reason")),
        "total_rounds": int(row.get("rounds") or 0),
        "resets": int(row.get("resets") or 0),
    }
    weapon_type = vocab.normalize_weapon_type(row.get("equip_type"), part)
    if weapon_type is not None:
        fields["weapon_type"] = weapon_type
    quality = vocab.normalize_quality(row.get("quality"))
    if quality is not None:
        fields["quality"] = quality
    season = config.get("season")
    if isinstance(season, int):
        fields["season"] = season
    rating = vocab.normalize_rating(row.get("telemetry_final_rating"))
    if rating is not None:
        fields["final_rating"] = rating
    validated = TUNING_SESSION_SCHEMA.validate(fields)
    return {
        "schema": validated.schema_name,
        "version": validated.schema_version,
        **dict(validated.values),
    }


class TuningHistoryTelemetrySource:
    name = "yysls.tuning_history"

    def collect(self, limit: int) -> tuple[SourceBatch, ...]:
        # 本方法目前跑在 Qt 主线程上（reporter.build_job）。三段各自计时，便于
        # 判断真要拆线程时瓶颈在哪：建库/迁移拿的是写锁，查询要反序列化每行的
        # JSON blob，投影每行都要查游戏配置。
        path = default_db_path()
        if not path.exists():
            return ()
        started = time.perf_counter()
        repo = TuningHistoryRepository(path)
        opened = time.perf_counter()
        rows = repo.pending_telemetry(
            days=LOOKBACK_DAYS, limit=limit,
            retry_after_hours=RETRY_BACKOFF_HOURS)
        queried = time.perf_counter()
        if not rows:
            logger.info(
                f"[telemetry] 历史采集: 无待上报行，"
                f"建库 {opened - started:.3f}s / 查询 {queried - opened:.3f}s")
            return ()
        install_id = identity_mod.get_identity().install_id
        accepted: list[tuple[dict, int]] = []
        rejected: list[int] = []
        deferred: list[int] = []
        for row in rows:
            equipment_id = int(row["equipment_id"])
            try:
                event = _project(row, install_id)
            except Exception as exc:  # noqa: BLE001
                # 异常不等于数据不合规：vocab.* 要读游戏配置，一次加载失败
                # 就把整批还在窗口内的会话判死刑太狠。留 unreported 重试。
                logger.warning(
                    f"[telemetry] 历史调律投影异常，稍后重试"
                    f"（equipment_id={equipment_id}）: {exc}")
                deferred.append(equipment_id)
                continue
            if event is None:
                # _project 主动返回 None：确定不合白名单，重试多少次都一样。
                rejected.append(equipment_id)
            else:
                accepted.append((event, equipment_id))
        repo.mark_rejected(rejected)
        now = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
        repo.mark_attempted(
            (receipt for _event, receipt in accepted),
            at=now, schema_version=TUNING_SESSION_SCHEMA.version,
        )
        # 退避计时同样落在 last_attempt_at 上，复用既有列，无需迁移。
        repo.mark_attempted(
            deferred, at=now, schema_version=TUNING_SESSION_SCHEMA.version)
        done = time.perf_counter()
        logger.info(
            f"[telemetry] 历史采集: {len(rows)} 行 → accepted {len(accepted)} / "
            f"rejected {len(rejected)} / deferred {len(deferred)}；"
            f"建库 {opened - started:.3f}s / 查询 {queried - opened:.3f}s / "
            f"投影回写 {done - queried:.3f}s")
        batches: list[SourceBatch] = []
        for offset in range(0, len(accepted), BATCH_SIZE):
            chunk = accepted[offset:offset + BATCH_SIZE]
            first_id = chunk[0][0]["event_id"][:8]
            batches.append(SourceBatch(
                source_name=self.name,
                batch_id=f"tuning-history-{first_id}",
                events=tuple(event for event, _receipt in chunk),
                receipts=tuple(receipt for _event, receipt in chunk),
            ))
        return tuple(batches)

    def mark_reported(self, receipts: tuple[int | str, ...]) -> None:
        path = default_db_path()
        if not path.exists():
            return
        TuningHistoryRepository(path).mark_reported(
            (int(value) for value in receipts),
            at=datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        )


SOURCE = TuningHistoryTelemetrySource()
register_source(SOURCE)
