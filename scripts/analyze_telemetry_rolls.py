#!/usr/bin/env python3
"""调律遥测事件分析：本地数据 → Markdown 报告。

输入是 ``yysls.tuning_session`` 事件（字段定义见
``src/lvjiang/apps/yysls/telemetry/schemas.py``）——**一条 = 一件装备从进
调律页面到离开**，带初始词条、逐轮产出序列与结束原因。三种来源自动识别：

1. 本地缓冲 NDJSON —— ``config/local/telemetry/spool/ready/*.ndjson``，
   每行一条事件，是开发者自己那台机器尚未上报的数据；
2. D1 导出 —— ``wrangler d1 execute --json`` 的输出，``roll_batch`` 行里
   ``payload`` 是 JSON 字符串数组，本脚本会自动展开；
3. 裸 JSON 数组 —— 上面两种的中间产物，或手工拼的样本。

方法论、每一节怎么读、以及这份数据已知的系统性偏差，见
``docs/10-game/20-affix-analysis/README.md``。报告本身也会把关键口径
重复一遍，因为报告会被单独转发，不该依赖读者手边有文档。

只用标准库：本仓库运行期不依赖 numpy/pandas/scipy，分析脚本也不该引入
只有它自己用的重型依赖。置信区间用 Wilson 区间（小样本、低概率下正态
近似会给出越界或过窄的区间），不给 p 值——见文档「为什么不给 p 值」。

分析逻辑本体在同目录 ``telemetry_analysis/`` 包里（本文件只是薄壳），
好处是 ``ops/stats-client`` 可以直接 import 同一份逻辑，不必 subprocess
调用这个 CLI；本文件的参数、行为、输出格式因此必须保持不变。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from telemetry_analysis import SCHEMA_NAME, build_report, load_events, parse_slot_range


def main() -> int:
    ap = argparse.ArgumentParser(
        description="调律遥测事件 → Markdown 分析报告",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="示例：\n"
               "  python scripts/analyze_telemetry_rolls.py config/local/telemetry/spool/ready\n"
               "  python scripts/analyze_telemetry_rolls.py rolls.json -o report.md "
               "--target-affix 会心 --max-per-install 2000\n"
               "  # 腿甲、首词条为「劲」时第 2-5 格的词条分布：\n"
               "  python scripts/analyze_telemetry_rolls.py rolls.json --slot-part leg "
               "--slot-first-affix 劲 --target-slots 2-5\n"
               "  # 第 2 格出现「会心」以后，第 3-5 格的词条分布：\n"
               "  python scripts/analyze_telemetry_rolls.py rolls.json --given-slot 2 "
               "--given-affix 会心 --target-slots 3-5\n")
    ap.add_argument("paths", nargs="+", type=Path,
                    help="NDJSON / JSON 文件或目录（目录会递归找 *.ndjson 和 *.json）")
    ap.add_argument("-o", "--output", type=Path, help="输出文件，缺省写 stdout")
    ap.add_argument("--target-affix", help="第 3 节保底检测的目标词条名（如「会心」）")
    ap.add_argument("--min-version", help="只统计 app_version >= 此值的事件（字符串比较）")
    ap.add_argument("--since", help="只统计 date >= 此值的事件（YYYY-MM-DD）")
    ap.add_argument("--max-per-install", type=int, metavar="N",
                    help="每个 install 最多取 N 条（固定 seed 随机抽样），抑制重度用户主导")
    ap.add_argument("--seed", type=int, default=20260826, help="抽样种子，缺省 20260826")
    ap.add_argument("--top", type=int, default=15, help="每格列出的词条数，缺省 15")

    g = ap.add_argument_group(
        "第 8 节：槽位条件查询（终态重建口径，见 slots.py）",
        "给了 --target-slots 才会输出这一节；其余都是可选的叠加筛选/条件")
    g.add_argument("--target-slots", metavar="N 或 N-M",
                   help="要查询的格位或格位区间，如 2 或 2-5")
    g.add_argument("--slot-part", help="限定部位（如 leg/weapon/ring/pendant/"
                  "head/chest/wrist），缺省不限定")
    g.add_argument("--slot-first-affix", help="限定首词条（第 1 格），如「劲」")
    g.add_argument("--given-slot", type=int, help="条件查询：给定这一格……")
    g.add_argument("--given-affix", help="……等于这个词条，配合 --given-slot 使用；"
                  "两者都给才会做条件查询，否则是无条件查询")
    args = ap.parse_args()

    if bool(args.given_slot) != bool(args.given_affix):
        ap.error("--given-slot 和 --given-affix 必须一起给")
    slot_query_target = None
    if args.target_slots:
        try:
            slot_query_target = parse_slot_range(args.target_slots)
        except ValueError as e:
            ap.error(str(e))

    events = load_events(args.paths)
    if not events:
        sys.exit(f"没有解析到任何 {SCHEMA_NAME} 事件")

    try:
        report = build_report(
            events,
            source_label=", ".join(str(p) for p in args.paths),
            target_affix=args.target_affix,
            min_version=args.min_version,
            since=args.since,
            max_per_install=args.max_per_install,
            seed=args.seed,
            top=args.top,
            slot_query_target=slot_query_target,
            slot_query_part=args.slot_part,
            slot_query_first_affix=args.slot_first_affix,
            slot_query_given_slot=args.given_slot,
            slot_query_given_affix=args.given_affix,
        )
    except ValueError as e:
        sys.exit(str(e))

    if args.output:
        args.output.write_text(report.text, encoding="utf-8")
        print(f"报告已写入 {args.output}（{report.n_events} 个会话 / {report.n_rolls} 轮）",
              file=sys.stderr)
    else:
        sys.stdout.write(report.text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
