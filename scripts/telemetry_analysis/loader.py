"""三种输入形态的加载与事件展开。

平移自 ``analyze_telemetry_rolls.py``（拆分前），逻辑逐字未改——见该文件
顶部 docstring 关于三种来源（本地 NDJSON 缓冲 / D1 导出 JSON / 裸 JSON 数组）
的说明。
"""
from __future__ import annotations

import json
import random
import sys
from collections import defaultdict
from pathlib import Path

SCHEMA_NAME = "yysls.tuning_session"


def _events_from_obj(obj) -> list[dict]:
    """从任意一层 JSON 结构里挖出事件列表，兼容三种输入形态。"""
    # wrangler --json：[{"results": [...], "success": true, "meta": {...}}]
    if isinstance(obj, list) and obj and isinstance(obj[0], dict) and "results" in obj[0]:
        rows = []
        for block in obj:
            rows.extend(block.get("results") or [])
        return _events_from_rows(rows)
    if isinstance(obj, dict) and "results" in obj:
        return _events_from_rows(obj.get("results") or [])
    if isinstance(obj, list):
        return _events_from_rows(obj)
    return _events_from_rows([obj])


def _events_from_rows(rows: list) -> list[dict]:
    """行可能是事件本身，也可能是带 payload 的 roll_batch 行。"""
    events: list[dict] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        if "payload" in row:
            payload = row["payload"]
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                except json.JSONDecodeError:
                    continue
            if isinstance(payload, list):
                for ev in payload:
                    if isinstance(ev, dict):
                        # roll_batch 行上的元数据事件里没有，补进去：
                        # app_version 是剔除坏解析器数据的唯一抓手
                        ev.setdefault("app_version", row.get("app_version"))
                        ev.setdefault("install_id", row.get("install_id"))
                        ev.setdefault("date", row.get("day"))
                        events.append(ev)
            continue
        events.append(row)
    return events


def load_events(paths: list[Path]) -> list[dict]:
    files: list[Path] = []
    for p in paths:
        if p.is_dir():
            files.extend(sorted(p.rglob("*.ndjson")))
            files.extend(sorted(p.rglob("*.json")))
        else:
            files.append(p)
    if not files:
        sys.exit("没有找到任何输入文件")

    events: list[dict] = []
    for f in files:
        try:
            raw = f.read_text(encoding="utf-8").strip()
        except OSError as e:
            print(f"警告：跳过无法读取的文件 {f}: {e}", file=sys.stderr)
            continue
        if not raw:
            continue
        if f.suffix == ".ndjson":
            for i, line in enumerate(raw.splitlines()):
                line = line.strip()
                if not line:
                    continue
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    # 末行半截是进程被杀的正常产物（见 spool.py），中段损坏才值得报
                    if i != len(raw.splitlines()) - 1:
                        print(f"警告：{f.name} 第 {i + 1} 行解析失败，已跳过", file=sys.stderr)
        else:
            try:
                events.extend(_events_from_obj(json.loads(raw)))
            except json.JSONDecodeError as e:
                print(f"警告：跳过无法解析的 JSON {f}: {e}", file=sys.stderr)

    return [e for e in events
            if e.get("schema") in (None, SCHEMA_NAME)
            and isinstance(e.get("rolls"), list) and "part" in e]


def flatten_rolls(sessions: list[dict]) -> list[dict]:
    """会话 → 逐轮记录，供分布/保底等按轮统计的小节使用。

    ``roll_index`` 由数组下标推出（+1）：它在事件里不再单独存，因为下标就是
    它——本件第几轮，跨重置连续累加，与 auto_tuning 里 ``rounds`` 的语义一致。
    """
    out: list[dict] = []
    for s in sessions:
        ctx = {k: v for k, v in s.items() if k not in ("rolls", "initial_affixes")}
        for i, r in enumerate(s.get("rolls") or [], start=1):
            out.append({**ctx, **r, "roll_index": i})
    return out


def subsample_per_install(events: list[dict], cap: int, seed: int) -> list[dict]:
    """把每个 install 的事件数截到 cap 条，抑制重度用户主导整体分布。

    用固定 seed 的随机抽样而非"取前 N 条"：后者会系统性偏向低 roll_index
    （每个会话都是从 1 开始记的），直接把保底分析的输入弄坏。
    """
    by_install: dict[str, list[dict]] = defaultdict(list)
    for e in events:
        by_install[e.get("install_id") or "?"].append(e)
    rng = random.Random(seed)
    out: list[dict] = []
    for evs in by_install.values():
        out.extend(evs if len(evs) <= cap else rng.sample(evs, cap))
    return out
