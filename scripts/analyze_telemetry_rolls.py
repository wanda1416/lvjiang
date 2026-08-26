#!/usr/bin/env python3
"""调律遥测事件分析：本地数据 → Markdown 报告。

输入是 ``yysls.tuning_roll`` 事件（字段定义见
``src/lvjiang/apps/yysls/telemetry/schemas.py``），三种来源自动识别：

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
"""
from __future__ import annotations

import argparse
import json
import math
import random
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_NAME = "yysls.tuning_roll"

# 每格样本少于这个数就不出结论，只报计数。7 部位 × 4 材料 × 数十词条，
# 格子极多，小格必然出现极端比例，读成"发现"就是在读噪声。
MIN_CELL_N = 30

PART_LABELS = {
    "weapon": "武器", "ring": "环", "pendant": "佩", "head": "冠胄",
    "chest": "胸甲", "leg": "胫甲", "wrist": "腕甲",
}
FOOD_LABELS = {
    "none": "不加狗粮", "gold": "金色狗粮", "purple": "紫色狗粮",
    "rainbow": "彩色狗粮",
}
MODE_LABELS = {
    "normal": "普通调律", "force_tune": "强制调律",
    "tune_full_recycle": "满词条回收",
}
ROLL_BUCKETS = ((1, 1, "1"), (2, 5, "2-5"), (6, 20, "6-20"),
                (21, 50, "21-50"), (51, 10 ** 9, "51+"))


# ─── 统计工具 ────────────────────────────────────────────

def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson 得分区间。

    n 小、p 接近 0 时正态近似（p ± z·sqrt(p(1-p)/n)）会给出负下界或过窄
    的区间，而词条分布恰好是"几十个词条、每个几个百分点"这种场景，正是
    正态近似最不该用的地方。
    """
    if n <= 0:
        return (0.0, 1.0)
    p = k / n
    d = 1 + z * z / n
    centre = p + z * z / (2 * n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (max(0.0, (centre - half) / d), min(1.0, (centre + half) / d))


def quantiles(values: list[float], qs=(0.05, 0.25, 0.5, 0.75, 0.95)) -> dict[float, float]:
    """线性插值分位数（stdlib 的 statistics.quantiles 不接受任意分位点）。"""
    if not values:
        return {q: float("nan") for q in qs}
    s = sorted(values)
    out = {}
    for q in qs:
        pos = q * (len(s) - 1)
        lo = math.floor(pos)
        hi = math.ceil(pos)
        out[q] = s[lo] if lo == hi else s[lo] + (s[hi] - s[lo]) * (pos - lo)
    return out


def roll_bucket(idx: int) -> str:
    for lo, hi, label in ROLL_BUCKETS:
        if lo <= idx <= hi:
            return label
    return "51+"


# ─── 数据加载 ────────────────────────────────────────────

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

    rolls = [e for e in events
             if e.get("schema") in (None, SCHEMA_NAME) and "affix" in e and "part" in e]
    return rolls


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


# ─── 各节分析 ────────────────────────────────────────────

def section_health(events: list[dict]) -> list[str]:
    L = ["## 0. 样本体检", "",
         "**这一节不合格，下面几节就不要读。** 分布类结论对样本构成极其敏感，",
         "而这份数据天然是自愿上报、少数重度用户贡献大头。", ""]

    installs = Counter(e.get("install_id") or "?" for e in events)
    dates = sorted({e.get("date") for e in events if e.get("date")})
    versions = Counter(e.get("app_version") or "(未知)" for e in events)

    L += [f"- 事件总数：**{len(events)}**",
          f"- 安装数：**{len(installs)}**",
          f"- 日期跨度：{dates[0]} ~ {dates[-1]}（{len(dates)} 天）" if dates
          else "- 日期跨度：(无 date 字段)"]

    counts = sorted(installs.values(), reverse=True)
    if counts:
        total = sum(counts)
        top1 = counts[0] / total * 100
        top5 = sum(counts[:5]) / total * 100
        L += [f"- 每 install 事件数分位：{_fmt_q(quantiles([float(c) for c in counts]))}",
              f"- **头部集中度：最大单个 install 占 {top1:.1f}%，前 5 占 {top5:.1f}%**"]
        if top1 >= 30:
            L += ["", f"> ⚠️ 单个 install 贡献了 {top1:.1f}% 的样本。此时的"
                  "「词条分布」很大程度是这一个人的部位/材料使用习惯，不是总体规律。",
                  "> 用 `--max-per-install` 重跑，看结论是否稳定；不稳定就说明还没有可报的结论。"]

    L += ["", "**版本分布**（旧版本解析器可能有 OCR bug，跨版本混算会把 bug 读成机制）：", ""]
    L += ["| app_version | 事件数 | 占比 |", "|---|---:|---:|"]
    for v, n in versions.most_common():
        L.append(f"| `{v}` | {n} | {n / len(events) * 100:.1f}% |")

    n_transfer = sum(1 for e in events if e.get("is_transferred"))
    n_custom = sum(1 for e in events if e.get("game_config_customized"))
    L += ["", "**需要分流的样本**：", "",
          f"- 转律词条 `is_transferred=true`：{n_transfer} 条"
          f"（{n_transfer / len(events) * 100:.1f}%）→ 第 1 节默认排除，单独在第 4 节看",
          f"- 自定义过 game_config `game_config_customized=true`：{n_custom} 条"
          f"（{n_custom / len(events) * 100:.1f}%）→ 第 2 节 cap_pct 排除（cap 口径已失真），"
          "第 1 节保留（词条名不受影响）"]
    return L


def _fmt_q(q: dict[float, float]) -> str:
    return "  ".join(f"P{int(k * 100)}={v:.0f}" for k, v in q.items())


def section_affix_dist(events: list[dict], top: int) -> list[str]:
    L = ["## 1. 词条分布 P(词条 | 部位, 材料)", "",
         f"仅普通调律（已排除 `is_transferred=true`）。每格样本 < {MIN_CELL_N} 只报计数、不报比例。",
         "括号内是 Wilson 95% 置信区间——**两格区间重叠就不能说它们不同**。", ""]

    base = [e for e in events if not e.get("is_transferred")]
    if not base:
        return L + ["（无普通调律样本）"]

    cells: dict[tuple[str, str], Counter] = defaultdict(Counter)
    for e in base:
        cells[(e.get("part"), e.get("food"))][e.get("affix")] += 1

    for (part, food) in sorted(cells, key=lambda k: -sum(cells[k].values())):
        c = cells[(part, food)]
        n = sum(c.values())
        head = f"### {PART_LABELS.get(part, part)} × {FOOD_LABELS.get(food, food)} （n={n}）"
        L += ["", head, ""]
        if n < MIN_CELL_N:
            L += [f"样本不足（n={n} < {MIN_CELL_N}），只列计数：", "",
                  "　" + "、".join(f"{a}×{k}" for a, k in c.most_common(top))]
            continue
        L += ["| 词条 | 次数 | 占比 | 95% CI |", "|---|---:|---:|---|"]
        for affix, k in c.most_common(top):
            lo, hi = wilson(k, n)
            L.append(f"| {affix} | {k} | {k / n * 100:.2f}% | "
                     f"{lo * 100:.2f}% ~ {hi * 100:.2f}% |")
        if len(c) > top:
            rest = sum(c.values()) - sum(k for _, k in c.most_common(top))
            L.append(f"| *（其余 {len(c) - top} 种）* | {rest} | {rest / n * 100:.2f}% | |")
    return L


def section_cap_pct(events: list[dict]) -> list[str]:
    L = ["## 2. cap_pct 数值分布", "",
         "`cap_pct` 是词条数值占该词条该等级上限的百分比。**已排除 "
         "`game_config_customized=true`**——那些样本的 cap 来自用户改过的 "
         "`game_config.yaml`，口径与其余样本不一致。", "",
         "看的是形状：均匀分布意味着数值在 [0, cap] 上等概率，"
         "集中在高位意味着有下限保护，多峰意味着离散档位。", ""]

    base = [e for e in events
            if not e.get("game_config_customized")
            and not e.get("is_transferred")
            and isinstance(e.get("cap_pct"), (int, float))
            and e["cap_pct"] > 0]
    if not base:
        return L + ["（无可用样本：cap_pct 全为 0 或样本已被排除）"]

    by_part: dict[str, list[float]] = defaultdict(list)
    for e in base:
        by_part[e.get("part")].append(float(e["cap_pct"]))

    L += [f"总样本 n={len(base)}。", "",
          "| 部位 | n | P5 | P25 | 中位 | P75 | P95 | 均值 |", "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for part, vals in sorted(by_part.items(), key=lambda kv: -len(kv[1])):
        q = quantiles(vals)
        L.append(f"| {PART_LABELS.get(part, part)} | {len(vals)} | "
                 + " | ".join(f"{q[x]:.1f}" for x in (0.05, 0.25, 0.5, 0.75, 0.95))
                 + f" | {sum(vals) / len(vals):.1f} |")

    L += ["", "**整体直方图**（10% 一档）：", "", "```"]
    hist = Counter(min(int(float(e["cap_pct"]) // 10), 9) for e in base)
    peak = max(hist.values()) if hist else 1
    for b in range(10):
        k = hist.get(b, 0)
        bar = "█" * round(k / peak * 40)
        L.append(f"{b * 10:3d}-{b * 10 + 10:3d}%  {bar:<40} {k:6d}  {k / len(base) * 100:5.1f}%")
    L += ["```"]
    return L


def section_pity(events: list[dict], target: str | None) -> list[str]:
    L = ["## 3. 保底 / 软保底检测", ""]
    if not target:
        L += ["未指定 `--target-affix`，跳过。", "",
              "指定后本节会按 `roll_index` 分桶看命中率是否随次数上升。"]
        return L

    L += [f"目标词条：**{target}**", "",
          "**关键：分桶必须在「部位 × 材料」层内比较。** 高 `roll_index` 桶只包含"
          "「前面都没中」的会话，它们的部位/材料构成与低桶不同；不分层直接比，"
          "读到的差异可能全部来自样本构成，与保底机制无关。",
          "（`ops/stats-worker/queries/roll_pity_check.sql` 目前就是不分层的，"
          "它的输出只能当粗筛，不能当结论。）", ""]

    base = [e for e in events if not e.get("is_transferred")]
    strata: dict[tuple[str, str], dict[str, list[int]]] = defaultdict(
        lambda: defaultdict(list))
    for e in base:
        idx = e.get("roll_index")
        if not isinstance(idx, int):
            continue
        strata[(e.get("part"), e.get("food"))][roll_bucket(idx)].append(
            1 if e.get("affix") == target else 0)

    shown = 0
    for key in sorted(strata, key=lambda k: -sum(len(v) for v in strata[k].values())):
        buckets = strata[key]
        total = sum(len(v) for v in buckets.values())
        usable = {b: v for b, v in buckets.items() if len(v) >= MIN_CELL_N}
        if len(usable) < 2:
            continue
        part, food = key
        L += ["", f"### {PART_LABELS.get(part, part)} × {FOOD_LABELS.get(food, food)}"
              f" （层内 n={total}）", "",
              "| roll_index | n | 命中 | 命中率 | 95% CI |", "|---|---:|---:|---:|---|"]
        for _, _, label in ROLL_BUCKETS:
            v = buckets.get(label)
            if not v:
                continue
            n, k = len(v), sum(v)
            if n < MIN_CELL_N:
                L.append(f"| {label} | {n} | {k} | *(样本不足)* | |")
                continue
            lo, hi = wilson(k, n)
            L.append(f"| {label} | {n} | {k} | {k / n * 100:.2f}% | "
                     f"{lo * 100:.2f}% ~ {hi * 100:.2f}% |")
        shown += 1
        if shown >= 5:
            L += ["", "*（只列样本量最大的 5 层）*"]
            break
    if shown == 0:
        L += ["", f"没有任何「部位 × 材料」层里有 ≥2 个桶达到 n≥{MIN_CELL_N}，"
              "样本不足以做分层保底检测。"]
    else:
        L += ["", "**怎么读**：只有当同一层内、高桶的 CI 下界高于低桶的 CI 上界时，"
              "才是「命中率随次数上升」的证据。区间重叠 = 没有证据，不是「趋势不明显」。"]
    return L


def section_transfer(events: list[dict], top: int) -> list[str]:
    L = ["## 4. 转律 vs 普通调律", "",
         "转律（`is_transferred=true`）与普通调律出条机制不同，第 1 节已把它排除。"
         "这里单独对比两者的词条分布。", ""]
    normal = Counter(e.get("affix") for e in events if not e.get("is_transferred"))
    trans = Counter(e.get("affix") for e in events if e.get("is_transferred"))
    n_n, n_t = sum(normal.values()), sum(trans.values())
    if n_t < MIN_CELL_N:
        return L + [f"转律样本仅 {n_t} 条（< {MIN_CELL_N}），不足以对比。"]

    L += [f"普通 n={n_n}，转律 n={n_t}。按转律占比排序取前 {top}：", "",
          "| 词条 | 转律占比 | 普通占比 | 转律 95% CI |", "|---|---:|---:|---|"]
    for affix, k in trans.most_common(top):
        lo, hi = wilson(k, n_t)
        pn = normal.get(affix, 0) / n_n * 100 if n_n else float("nan")
        L.append(f"| {affix} | {k / n_t * 100:.2f}% | {pn:.2f}% | "
                 f"{lo * 100:.2f}% ~ {hi * 100:.2f}% |")
    L += ["", "同样：转律 CI 与普通占比重叠时，不能说两者不同。"]
    return L


def section_caveats(args, n_raw: int, n_used: int) -> list[str]:
    return [
        "## 附录：口径与已知偏差", "",
        f"- 原始事件 {n_raw} 条，实际参与统计 {n_used} 条"
        + (f"（`--max-per-install {args.max_per_install}` 抽样后）" if args.max_per_install else ""),
        "- **样本是自愿上报的**：只包含在首启弹窗里同意匿名统计的用户，"
        "不是全部装机量。对外引用任何数字都必须写明这一口径。",
        "- **停止规则会截断会话**：自动调律在规则满足时停止，所以每个会话的"
        "最后一次 roll 系统性地是「命中」的。按会话统计「平均多少次出目标」时"
        "必须意识到未完成的会话（材料耗尽、用户中断）也在样本里，它们没有终止命中。",
        "- **`active_rule` 决定了用户在调什么**：启用不同规则的用户，其部位/材料"
        "分布本就不同。跨 `active_rule` 比较词条分布，比的可能是使用习惯而非机制。",
        "- **不给 p 值**：几十个词条 × 7 部位 × 4 材料，逐格检验必然量产假阳性，"
        "而在这个场景里没有预注册的假设可供校正。置信区间 + 效应量能支撑决策，"
        "p 值只会制造「显著」的错觉。",
        "", "方法论详见 `docs/10-game/20-affix-analysis/README.md`。",
    ]


# ─── 主流程 ──────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(
        description="调律遥测事件 → Markdown 分析报告",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="示例：\n"
               "  python scripts/analyze_telemetry_rolls.py config/local/telemetry/spool/ready\n"
               "  python scripts/analyze_telemetry_rolls.py rolls.json -o report.md "
               "--target-affix 会心 --max-per-install 2000\n")
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
    args = ap.parse_args()

    events = load_events(args.paths)
    if not events:
        sys.exit("没有解析到任何 yysls.tuning_roll 事件")
    n_raw = len(events)

    if args.min_version:
        events = [e for e in events if str(e.get("app_version") or "") >= args.min_version]
    if args.since:
        events = [e for e in events if str(e.get("date") or "") >= args.since]
    if not events:
        sys.exit("过滤后没有剩余事件")
    if args.max_per_install:
        events = subsample_per_install(events, args.max_per_install, args.seed)

    now = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S%z")
    filters = []
    if args.min_version:
        filters.append(f"app_version >= {args.min_version}")
    if args.since:
        filters.append(f"date >= {args.since}")
    if args.max_per_install:
        filters.append(f"每 install ≤ {args.max_per_install} 条（seed={args.seed}）")

    lines = [
        "# 调律词条分布分析报告", "",
        f"- 生成时间：{now}",
        f"- 数据源：{', '.join(str(p) for p in args.paths)}",
        f"- 过滤：{'；'.join(filters) if filters else '无'}",
        "", "---", "",
    ]
    lines += section_health(events)
    lines += ["", "---", ""] + section_affix_dist(events, args.top)
    lines += ["", "---", ""] + section_cap_pct(events)
    lines += ["", "---", ""] + section_pity(events, args.target_affix)
    lines += ["", "---", ""] + section_transfer(events, args.top)
    lines += ["", "---", ""] + section_caveats(args, n_raw, len(events))

    text = "\n".join(lines) + "\n"
    if args.output:
        args.output.write_text(text, encoding="utf-8")
        print(f"报告已写入 {args.output}（{len(events)} 条事件）", file=sys.stderr)
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
