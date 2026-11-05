"""首条显示归类及四系属攻汇总；保留原始样本分母。"""
from __future__ import annotations

import json
from pathlib import Path

from lvjiang.apps.shuijiao_tuning.core.tuning_history.frequency import FrequencyRow


def groups_config() -> dict:
    return json.loads((Path(__file__).parents[2] / "config/frequency_groups.json").read_text(encoding="utf-8"))


def initial_categories(part: str) -> list[str]:
    return [*groups_config()["initial"].get(part, []), "其他"]


def initial_category(part: str, name: str) -> str:
    return name if name in initial_categories(part)[:-1] else "其他"


def attribute_members(label: str) -> list[str]:
    if label not in ("最小属性攻击", "最大属性攻击"):
        return []
    return [label[:2] + attr + "攻击" for attr in groups_config()["attributes"]]


def grouped_stat(rows: list[FrequencyRow], part: str, source: str, label: str) -> tuple[int, int]:
    selected = [r for r in rows if r.part == part and r.source == source]
    total = max((r.total for r in selected), default=0)
    if source == "initial":
        hits = sum(r.hits for r in selected if initial_category(part, r.affix) == label)
    else:
        members = attribute_members(label) if part != "武器" else []
        hits = sum(r.hits for r in selected if r.affix in (members or [label]))
    return hits, total


def format_stat(hits: int, total: int) -> str:
    return f"{hits / total:.2%}  ({hits}/{total})" if total else "暂无样本"
