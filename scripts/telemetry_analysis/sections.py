"""报告各节：输入事件列表，输出 Markdown 行。多数节平移自
``analyze_telemetry_rolls.py``，逻辑与文案逐字未改（拆分那次改动的验收
标准是 CLI 输出前后字节级一致）。``section_slot`` 是例外：它改成了终态
重建口径，见该函数文档字符串——这是后续一次改动，不受"字节级一致"约束。
"""
from __future__ import annotations

from collections import Counter, defaultdict

from .metrics import (
    FOOD_LABELS,
    MIN_CELL_N,
    PART_LABELS,
    ROLL_BUCKETS,
    quantiles,
    roll_bucket,
    wilson,
)
from .slots import (
    conditional_slot_distribution,
    observed_slots,
    reconstruct_all,
    slot_distribution,
    slot_range_distribution,
)


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


def _slot_stat_lines(stat, heading: str, top: int) -> list[str]:
    L = ["", heading, ""]
    rows = stat.rows(top)
    if stat.n < MIN_CELL_N:
        L += [f"样本不足（n={stat.n} < {MIN_CELL_N}），只列计数：", "",
              "　" + "、".join(f"{a}×{k}" for a, k, _, _ in rows)]
        return L
    L += ["| 词条 | 次数 | 占比 | 95% CI |", "|---|---:|---:|---|"]
    for affix, k, p, (lo, hi) in rows:
        L.append(f"| {affix} | {k} | {p * 100:.2f}% | {lo * 100:.2f}% ~ {hi * 100:.2f}% |")
    return L


def section_slot(sessions: list[dict], top: int) -> list[str]:
    """槽位词条分布——终态重建口径，见 ``slots.py`` 模块文档。

    与旧版（按每次 roll 观测计数）的区别：这里统计的是**每件装备最终
    留下的**词条（按 ``resets`` 重建），中途被重置抹掉、或被同一格后续
    重新调出覆盖的旧值不计入。旧口径回答"调律过程中这一格出现过什么"，
    这里回答"这件装备最终这一格是什么"——是不同的问题，此前混着报是
    这一节"没用"的主因。
    """
    L = ["## 7. 槽位词条分布（终态重建）", "",
         "统计对象是**每件装备最终留下的**词条（按 `resets` 重建，见"
         "`docs/10-game/20-affix-analysis/README.md`「重置会把装备清回只剩"
         "首词条」一节），不是调律过程中每次 roll 的原始产出。转律件（会话内"
         "出现过一次转律即算）整条排除，理由与词条分布/cap_pct 一致。", "",
         "第 1 格（首词条）通常是装备自带、不是调律调出来的，单列一节"
         "仅供参考，不建议当「词条分布」解读。", ""]

    items = [it for it in reconstruct_all(sessions) if not it.is_transferred_any]
    if not items:
        return L + ["（无样本）"]
    slots = observed_slots(items)
    if not slots:
        return L + ["（无样本）"]
    tuned_slots = [s for s in slots if s != 1]

    L += ["", "### 全部部位汇总", ""]
    if 1 in slots:
        stat = slot_distribution(items, 1)
        L += _slot_stat_lines(stat, f"#### 第 1 格（首词条，仅供参考，n={stat.n}）", top)
    for slot in tuned_slots:
        stat = slot_distribution(items, slot)
        L += _slot_stat_lines(stat, f"#### 第 {slot} 格 （n={stat.n}）", top)

    if len(tuned_slots) >= 2:
        lo_slot, hi_slot = min(tuned_slots), max(tuned_slots)
        stat = slot_range_distribution(items, lo_slot, hi_slot)
        L += ["", f"### 第 {lo_slot}-{hi_slot} 格并集：词条出现在其中任意一格的概率", "",
              "分母是「这个区间至少有一格已知」的装备数；一件装备可以同时命中"
              "多个词条（并集口径），各行占比之和可能超过 100%，不是重复计数。", ""]
        L += _slot_stat_lines(stat, f"（n={stat.n}）", top)

    parts_present = sorted({it.part for it in items if it.part})
    if len(parts_present) > 1:
        L += ["", "### 分部位", ""]
        for part in parts_present:
            label = PART_LABELS.get(part, part)
            part_tuned_slots = [s for s in observed_slots(
                [it for it in items if it.part == part]) if s != 1]
            if not part_tuned_slots:
                continue
            L += ["", f"#### {label}", ""]
            for slot in part_tuned_slots:
                stat = slot_distribution(items, slot, part=part)
                L += _slot_stat_lines(stat, f"##### 第 {slot} 格 （n={stat.n}）", top)
            if len(part_tuned_slots) >= 2:
                lo_slot, hi_slot = min(part_tuned_slots), max(part_tuned_slots)
                stat = slot_range_distribution(items, lo_slot, hi_slot, part=part)
                L += _slot_stat_lines(
                    stat, f"##### 第 {lo_slot}-{hi_slot} 格并集 （n={stat.n}）", top)

    L += ["", "**怎么读**：各格分布若在 CI 内一致，说明槽位不影响词条池；"
          "某格系统性偏离才是「这一格有特殊规则」的证据。给定首词条/给定"
          "某格词条的条件查询（如「腿甲首词条为劲时第 2-5 格分布」「第 2 格"
          "出现会心后第 3 格及以后的分布」）组合太多，写不进固定报告——"
          "用 stats-client 网页的「槽位条件查询」，或本脚本的 "
          "`--given-slot`/`--target-slots` 参数（见 `--help`）。"]
    return L


def section_slot_query(
    sessions: list[dict], *, part: str | None, first_affix: str | None,
    given_slot: int | None, given_affix: str | None,
    target_lo: int, target_hi: int, top: int,
) -> list[str]:
    """单次槽位条件查询——回答一个具体问题，不是穷举所有组合。

    `given_slot`/`given_affix` 都给了才做条件查询（P(target 区间 | 第
    given_slot 格 = given_affix)）；否则是无条件查询（P(target 区间)，
    仍可以叠 ``part``/``first_affix`` 筛选），覆盖"总览全部装备第 2 格
    概率"和"某部位×某首词条下第 2-5 格概率"这两类问题。
    """
    L = ["## 8. 槽位条件查询", ""]
    items = [it for it in reconstruct_all(sessions) if not it.is_transferred_any]
    if not items:
        return L + ["（无样本）"]

    if given_slot is not None and given_affix:
        stat = conditional_slot_distribution(
            items, given_slot=given_slot, given_affix=given_affix,
            target_lo=target_lo, target_hi=target_hi,
            part=part, first_affix=first_affix)
    elif target_lo == target_hi:
        stat = slot_distribution(items, target_lo, part=part, first_affix=first_affix)
    else:
        stat = slot_range_distribution(items, target_lo, target_hi,
                                       part=part, first_affix=first_affix)

    L += [f"筛选：{stat.filters_desc}", ""]
    L += _slot_stat_lines(stat, f"（n={stat.n}）", top)
    if target_lo != target_hi:
        L += ["", "并集口径：分母是区间内至少一格已知的装备数，各行占比之和"
              "可能超过 100%。"]
    return L


def section_caveats(n_raw: int, n_used: int, max_per_install: int | None) -> list[str]:
    return [
        "## 附录：口径与已知偏差", "",
        f"- 原始事件 {n_raw} 条，实际参与统计 {n_used} 条"
        + (f"（`--max-per-install {max_per_install}` 抽样后）" if max_per_install else ""),
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
