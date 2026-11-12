"""本地自然调律与首词条观测频率。2026-10-31 新增。"""
from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass

from lvjiang.apps.yysls.config.equipment_slots import slot_part


@dataclass(frozen=True)
class FrequencyRow:
    part: str
    source: str
    affix: str
    hits: int
    total: int

    @property
    def frequency(self) -> float:
        return self.hits / self.total if self.total else 0.0


def _affixes(raw) -> list[dict]:
    value = json.loads(raw or "[]") if isinstance(raw, str) else raw
    if not isinstance(value, (list, tuple)):
        raise ValueError("词条列表无效")
    return list(value)


def calculate_frequency(
    records: list[dict], *, allowed: list[str], username: str = "",
    level: int | None = 115, part: str = "", weapon: str = "",
    first_affix: str = "", slot: int | None = None,
) -> tuple[list[FrequencyRow], int]:
    """只计入能确定来源的观测，不把已有四词条快照当作四次 roll。"""
    known = set(allowed)
    counts: Counter = Counter()
    totals: Counter = Counter()
    invalid = 0
    for record in records:
        if record.get("level") != 115 or record.get("quality") != "gold":
            continue  # 固定样本口径：紫装会按词条提前停止，不能混入概率估计。
        if record.get("tuning_mode") == "transition_review":
            continue  # 清理复核保留审计记录，不制造新的掉落/调律观测。
        group = slot_part(record.get("slot_key", ""))
        if (not group or (part and group != part)
                or (username and record.get("username") != username)
                or (level is not None and record.get("level") != level)
                or (weapon and record.get("equip_type") != weapon)):
            continue
        try:
            previous = _affixes(record.get("initial_affixes_json"))
            rounds = _affixes(record.get("round_details_json"))
        except (ValueError, TypeError):
            invalid += 1
            continue
        first = previous[0].get("name") if previous and isinstance(previous[0], dict) else ""
        if first_affix and first != first_affix:
            continue

        def add(source: str, affix: dict, index: int, group: str = group) -> None:
            nonlocal invalid
            name = affix.get("name")
            if (name not in known or (affix.get("is_transferred")
                                     and not affix.get("is_original"))):
                invalid += 1
                return
            if slot is None or index == slot:
                counts[group, source, name] += 1
                totals[group, source] += 1

        if first in known:
            add("initial", previous[0], 1)
        for detail in rounds:
            if not isinstance(detail, dict) or detail.get("completed") is False:
                continue
            current = detail.get("current_affixes")
            if isinstance(current, list):
                if (not all(isinstance(a, dict) for a in current)
                        or not all(isinstance(a, dict) for a in previous)
                        or len(current) > 5):
                    invalid += 1
                    continue
                old_names = [a.get("name") for a in previous]
                if [a.get("name") for a in current[:len(previous)]] != old_names:
                    # 转律/不可还原的跳变：不把差异当成自然调律。
                    invalid += 1
                    previous = current
                    continue
                for index in range(len(previous), len(current)):
                    add("tune", current[index], index + 1)
                previous = current
            else:
                # 旧记录只在明确单条新增时可用；批量记录缺明细不猜测。
                affix = detail.get("new_affix_data")
                if (isinstance(affix, dict) and detail.get("filled_count", 1) == 1
                        and 1 <= len(previous) < 5):
                    add("tune", affix, len(previous) + 1)
                    previous = previous + [affix]
                else:
                    invalid += 1
    rows = [FrequencyRow(group, source, name, counts[group, source, name], total)
            for (group, source), total in sorted(totals.items())
            for name in allowed]
    return rows, invalid
