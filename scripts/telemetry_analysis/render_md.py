"""组装完整 Markdown 报告——相当于拆分前 CLI ``main()`` 里"拿到 events 之后"
的那部分。拆成库函数是为了让 ``ops/stats-client`` 也能直接拿到同一份报告文本
（以及背后的样本量），不必 subprocess 调用 CLI。

CLI 行为不变：``scripts/analyze_telemetry_rolls.py`` 现在只是解析参数、
调用 :func:`build_report`、写文件/写 stdout 的薄壳，见该文件。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from .loader import flatten_rolls, subsample_per_install
from .sections import (
    section_affix_dist,
    section_cap_pct,
    section_caveats,
    section_conditional,
    section_health,
    section_pity,
    section_slot,
    section_slot_query,
    section_stop_reason,
    section_transfer,
)


@dataclass
class Report:
    text: str
    n_raw: int          # 过滤/抽样前的事件（会话）数
    n_events: int        # 实际参与统计的事件（会话）数
    n_rolls: int          # 实际参与统计的轮次数


def build_report(
    events: list[dict],
    *,
    source_label: str,
    target_affix: str | None = None,
    min_version: str | None = None,
    since: str | None = None,
    max_per_install: int | None = None,
    seed: int = 20260826,
    top: int = 15,
    slot_query_target: tuple[int, int] | None = None,
    slot_query_part: str | None = None,
    slot_query_first_affix: str | None = None,
    slot_query_given_slot: int | None = None,
    slot_query_given_affix: str | None = None,
) -> Report:
    """``events`` 是 :func:`telemetry_analysis.load_events` 的输出（未过滤）。

    过滤条件全部为空时结果不变；``events`` 为空或过滤后为空会抛
    ``ValueError``（CLI 壳负责把它转成 ``sys.exit``，库调用方按自己的方式处理）。

    ``slot_query_*`` 全是可选的槽位条件查询参数：给了 ``slot_query_target``
    （``(lo, hi)``，单格传 ``(k, k)``）才会多出「## 8. 槽位条件查询」一节，
    其余参数是叠加筛选/条件——见 :func:`sections.section_slot_query`。
    """
    if not events:
        raise ValueError("没有可统计的事件")
    n_raw = len(events)

    if min_version:
        events = [e for e in events if str(e.get("app_version") or "") >= min_version]
    if since:
        events = [e for e in events if str(e.get("date") or "") >= since]
    if not events:
        raise ValueError("过滤后没有剩余事件")
    if max_per_install:
        events = subsample_per_install(events, max_per_install, seed)

    now = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S%z")
    filters = []
    if min_version:
        filters.append(f"app_version >= {min_version}")
    if since:
        filters.append(f"date >= {since}")
    if max_per_install:
        filters.append(f"每 install ≤ {max_per_install} 条（seed={seed}）")

    lines = [
        "# 调律词条分布分析报告", "",
        f"- 生成时间：{now}",
        f"- 数据源：{source_label}",
        f"- 过滤：{'；'.join(filters) if filters else '无'}",
        "", "---", "",
    ]
    rolls = flatten_rolls(events)
    lines += section_health(events)
    lines += ["", "---", ""] + section_affix_dist(rolls, top)
    lines += ["", "---", ""] + section_cap_pct(rolls)
    lines += ["", "---", ""] + section_pity(rolls, target_affix)
    lines += ["", "---", ""] + section_transfer(rolls, top)
    lines += ["", "---", ""] + section_stop_reason(events)
    lines += ["", "---", ""] + section_conditional(events, top)
    lines += ["", "---", ""] + section_slot(events, top)
    if slot_query_target is not None:
        target_lo, target_hi = slot_query_target
        lines += ["", "---", ""] + section_slot_query(
            events, part=slot_query_part, first_affix=slot_query_first_affix,
            given_slot=slot_query_given_slot, given_affix=slot_query_given_affix,
            target_lo=target_lo, target_hi=target_hi, top=top)
    lines += ["", "---", ""] + section_caveats(n_raw, len(events), max_per_install)

    text = "\n".join(lines) + "\n"
    return Report(text=text, n_raw=n_raw, n_events=len(events), n_rolls=len(rolls))
