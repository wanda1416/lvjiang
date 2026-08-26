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

SCHEMA_NAME = "yysls.tuning_session"

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


# ─── 各节分析 ────────────────────────────────────────────

def section_health(events: list[dict]) -> list[str]:
    L = ["## 0. 样本体检", "",
         "**这一节不合格，下面几节就不要读。** 分布类结论对样本构成极其敏感，",
         "而这份数据天然是自愿上报、少数重度用户贡献大头。", ""]

    installs = Counter(e.get("install_id") or "?" for e in events)
    dates = sorted({e.get("date") for e in events if e.get("date")})
    versions = Counter(e.get("app_version") or "(未知)" for e in events)

    L += [f"- 会话数（件装备）：**{len(events)}**",
          f"- 轮次总数：**{sum(len(e.get('rolls') or []) for e in events)}**",
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

    # cap_pct 是选填：算不出上限时客户端**省略该字段**，所以缺席=未知，
    # 用 isinstance 排除即可。**不能再用 `> 0` 过滤**——0 是合法取值
    # （洗到该词条下限），把它当无效值丢掉会砍掉分布的整个低位，
    # 直接扭曲「有没有下限保护」这个结论，而那正是本节要看的东西。
    base = [e for e in events
            if not e.get("game_config_customized")
            and not e.get("is_transferred")
            and isinstance(e.get("cap_pct"), (int, float))]
    if not base:
        return L + ["（无可用样本：cap_pct 均未采集到，或样本已被排除）"]

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


def section_stop_reason(sessions: list[dict]) -> list[str]:
    L = ["## 5. 结束原因分布", "",
         "规则判定得对不对，这一节是直接证据：`decided_recycle` 占比过高说明"
         "规则过严（好装备被回收），`cannot_continue` 占比过高说明材料配置跟不上。",
         "旧的逐轮粒度答不了这个问题——它根本不记录会话怎么结束的。", ""]
    counts = Counter(s.get("stop_reason") for s in sessions)
    ratings = Counter(s.get("final_rating") for s in sessions if s.get("final_rating"))
    n = sum(counts.values())
    if not n:
        return L + ["（无样本）"]
    L += ["| 结束原因 | 会话数 | 占比 |", "|---|---:|---:|"]
    for k, v in counts.most_common():
        L.append(f"| `{k}` | {v} | {v / n * 100:.1f}% |")
    if ratings:
        m = sum(ratings.values())
        L += ["", "**最终评级分布**（仅统计有适用规则的会话）：", "",
              "| 评级 | 会话数 | 占比 |", "|---|---:|---:|"]
        for k in ("top", "excellent", "normal", "junk"):
            if k in ratings:
                L.append(f"| `{k}` | {ratings[k]} | {ratings[k] / m * 100:.1f}% |")
    return L


def section_conditional(sessions: list[dict], top: int) -> list[str]:
    L = ["## 6. 条件概率 P(下一条 | 已有词条)", "",
         "**这一节是按件上报解锁的能力。** 逐轮独立上报的事件之间没有关联字段，"
         "拼不出「这件装备已经有什么」，这个问题当时根本问不了。", "",
         "看的是：某个词条已经在装备上时，下一轮出它的概率是否变化。"
         "明显低于无条件概率 → 游戏在排重；基本持平 → 每轮独立。", ""]

    base = Counter()          # 无条件：每一轮的产出
    cond_present = Counter()  # 该词条已在场时的产出
    cond_trials = Counter()   # 该词条已在场的轮次总数
    for s in sessions:
        initial = s.get("initial_affixes") or []
        rolls = s.get("rolls") or []
        # 转律的出条机制与普通调律不同。这里必须连 rolls 一起看：只查
        # initial_affixes 会漏掉「普通装备中途转律」的会话，那条转律产出
        # 会被当成普通产出计进分母和分子。整条会话排除而不是只跳过那一轮
        # ——序列里挖个洞会让后续轮次的「已在场」集合失真。
        if any(a.get("is_transferred") for a in initial) or \
                any(r.get("is_transferred") for r in rolls):
            continue
        # 重置会把装备清回**只剩首词条**（auto_tuning 里
        # ``equip_data.affixes = base_affixes[:1]``），slot 也打回 1。
        # 跨重置继续累加 have，等于拿重置前早已不存在的词条去算「已在场」
        # 条件——条件概率的分母和分子会同时算错。所以按 resets 分段重建。
        first_affix = {initial[0].get("affix")} if initial else set()
        have = {a.get("affix") for a in initial}
        prev_resets = 0
        for r in rolls:
            resets = r.get("resets")
            resets = resets if isinstance(resets, int) else prev_resets
            if resets > prev_resets:
                have = set(first_affix)   # 重置：清回首词条
                prev_resets = resets
            name = r.get("affix")
            base[name] += 1
            for seen in have:
                cond_trials[seen] += 1
                if name == seen:
                    cond_present[seen] += 1
            have.add(name)
    n_base = sum(base.values())
    if not n_base:
        return L + ["（无样本）"]

    rows = [(a, cond_trials[a], cond_present[a]) for a in cond_trials
            if cond_trials[a] >= MIN_CELL_N]
    if not rows:
        return L + [f"没有任何词条的「已在场轮次」达到 n≥{MIN_CELL_N}，样本不足。"]
    L += ["| 词条 | 无条件 P | 已在场时 P | 已在场轮次 n | 已在场时 95% CI |",
          "|---|---:|---:|---:|---|"]
    for affix, trials, hits in sorted(rows, key=lambda r: -r[1])[:top]:
        lo, hi = wilson(hits, trials)
        L.append(f"| {affix} | {base[affix] / n_base * 100:.2f}% | "
                 f"{hits / trials * 100:.2f}% | {trials} | "
                 f"{lo * 100:.2f}% ~ {hi * 100:.2f}% |")
    L += ["", "**怎么读**：只有当「已在场时」的 CI 完全落在无条件概率之下，"
          "才是排重机制的证据。区间罩住无条件值 = 没有证据。"]
    return L


def section_slot(sessions: list[dict], top: int) -> list[str]:
    """第 N 格的词条分布——对齐 analyze_tuning_affixes.py 的 position_affix。"""
    L = ["## 7. 第 N 格的词条分布", "",
         "`initial_affixes` 的下标是**槽位序**（宫商角徵羽），`rolls[].slot` 是"
         "该轮落在第几格。两者合起来就是「这一格上出现过什么」。", "",
         "重置不影响本节：第 2 格重置后仍是第 2 格，同格观测可以合并统计。"
         "需要按 `resets` 分段的是**重建终态词条组合**（重置会把 slot 打回 1，"
         "跨段拼接会把两批词条叠在同一格上），本节不做那件事。", ""]

    by_slot: dict[int, Counter] = defaultdict(Counter)
    for s in sessions:
        for i, a in enumerate(s.get("initial_affixes") or [], start=1):
            if not a.get("is_transferred"):
                by_slot[i][a.get("affix")] += 1
        for r in (s.get("rolls") or []):
            if not r.get("is_transferred") and isinstance(r.get("slot"), int):
                by_slot[r["slot"]][r.get("affix")] += 1
    if not by_slot:
        return L + ["（无样本）"]

    for slot in sorted(by_slot):
        c = by_slot[slot]
        n = sum(c.values())
        L += ["", f"### 第 {slot} 格 （n={n}）", ""]
        if n < MIN_CELL_N:
            L += [f"样本不足（n={n} < {MIN_CELL_N}），只列计数：", "",
                  "　" + "、".join(f"{a}×{k}" for a, k in c.most_common(top))]
            continue
        L += ["| 词条 | 次数 | 占比 | 95% CI |", "|---|---:|---:|---|"]
        for affix, k in c.most_common(top):
            lo, hi = wilson(k, n)
            L.append(f"| {affix} | {k} | {k / n * 100:.2f}% | "
                     f"{lo * 100:.2f}% ~ {hi * 100:.2f}% |")
    L += ["", "**怎么读**：各格分布若在 CI 内一致，说明槽位不影响词条池；"
          "某格系统性偏离才是「这一格有特殊规则」的证据。"]
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
        sys.exit(f"没有解析到任何 {SCHEMA_NAME} 事件")
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
    rolls = flatten_rolls(events)
    lines += section_health(events)
    lines += ["", "---", ""] + section_affix_dist(rolls, args.top)
    lines += ["", "---", ""] + section_cap_pct(rolls)
    lines += ["", "---", ""] + section_pity(rolls, args.target_affix)
    lines += ["", "---", ""] + section_transfer(rolls, args.top)
    lines += ["", "---", ""] + section_stop_reason(events)
    lines += ["", "---", ""] + section_conditional(events, args.top)
    lines += ["", "---", ""] + section_slot(events, args.top)
    lines += ["", "---", ""] + section_caveats(args, n_raw, len(events))

    text = "\n".join(lines) + "\n"
    if args.output:
        args.output.write_text(text, encoding="utf-8")
        print(f"报告已写入 {args.output}（{len(events)} 个会话 / {len(rolls)} 轮）",
              file=sys.stderr)
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
