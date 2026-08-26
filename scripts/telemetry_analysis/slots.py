"""槽位终态重建 + 槽位条件概率查询。

背景：`sections.section_slot`（旧「第 N 格的词条分布」）统计的是**每一次
roll 观测**，包括后来被重置抹掉、或被同一格后续重新调出的值覆盖的旧观测——
这回答的是"调律过程中这一格出现过什么"，不是"这件装备最终这一格是什么"。
两者是不同的问题，后者（终态）才是"腿甲部位首词条为劲时第 2-5 格最终是
什么"这类问题真正要问的。

`docs/10-game/20-affix-analysis/README.md` 里"遥测数据答不了（没有词条
组合）"的说法是旧版逐轮上报 schema（一轮一条事件）时代写的，`一件装备一条`
的新 schema（`initial_affixes[]` + `rolls[].slot`，按 `resets` 能重建终态）
已经能回答了——该文档需要同步更新，见 PR 说明。

**终态重建规则**（与 `docs/.../README.md`"重置会把装备清回只剩首词条"
一致，`sections.section_conditional` 已经用这套规则处理条件概率）：

1. 起始状态 = `initial_affixes`（下标 i 对应第 i+1 格），转律词条不计入。
2. 逐轮回放 `rolls`：`resets` 比上一轮大 → 状态清回 `{1: 首词条}`；
   否则把这一轮的 `(slot, affix)` 写进当前状态（覆盖旧值）。转律轮同样
   不计入（转律机制不同，见 `section_transfer`）。
3. 回放完的最终状态就是这件装备终态的 `{槽位: 词条}`。

**转律的处理是整条排除，不是单格排除**：会话内只要出现过一次转律
（初始词条或某轮标了 `is_transferred`），整件装备都不进入槽位统计
（`SlotItem.is_transferred_any`）。转律换的是哪一格本身就带机制差异，
"这件装备槽位分布"问的是纯普通调律的结果，混进转律件会把两种机制的
结果叠在一起——这与 `section_affix_dist`/`section_cap_pct` 默认排除
转律样本是同一个理由。
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from .metrics import wilson


@dataclass
class SlotItem:
    """一件装备的终态槽位快照 + 分层维度。"""

    part: str | None
    weapon_type: str
    level: int | None
    quality: str
    mode: str | None
    active_rule: str | None
    is_transferred_any: bool
    slots: dict[int, str] = field(default_factory=dict)  # {槽位: 词条}，不含转律


def reconstruct_final_state(session: dict) -> SlotItem:
    initial = session.get("initial_affixes") or []
    slots: dict[int, str] = {}
    has_transfer = False
    for i, a in enumerate(initial, start=1):
        if a.get("is_transferred"):
            has_transfer = True
            continue
        name = a.get("affix")
        if name:
            slots[i] = name

    # 与 section_conditional 一致：不额外检查首词条自身是否标了 is_transferred，
    # 复现同一套已经过审的重建规则，不在这里另起一套判断口径。
    first_affix = initial[0].get("affix") if initial else None
    prev_resets = 0
    for r in session.get("rolls") or []:
        if r.get("is_transferred"):
            has_transfer = True
            continue
        resets = r.get("resets")
        resets = resets if isinstance(resets, int) else prev_resets
        if resets > prev_resets:
            slots = {1: first_affix} if first_affix else {}
            prev_resets = resets
        slot, name = r.get("slot"), r.get("affix")
        if isinstance(slot, int) and name:
            slots[slot] = name

    return SlotItem(
        part=session.get("part"), weapon_type=session.get("weapon_type") or "",
        level=session.get("level"), quality=session.get("quality") or "",
        mode=session.get("mode"), active_rule=session.get("active_rule"),
        is_transferred_any=has_transfer, slots=slots,
    )


def reconstruct_all(sessions: list[dict]) -> list[SlotItem]:
    return [reconstruct_final_state(s) for s in sessions]


def _apply_filters(items: list[SlotItem], *, part: str | None = None,
                   first_affix: str | None = None,
                   exclude_transferred: bool = True) -> list[SlotItem]:
    out = []
    for it in items:
        if exclude_transferred and it.is_transferred_any:
            continue
        if part and it.part != part:
            continue
        if first_affix and it.slots.get(1) != first_affix:
            continue
        out.append(it)
    return out


@dataclass
class SlotStat:
    """一次槽位查询的结果：affix -> (命中数, 概率, Wilson 区间)。样本量 ``n``
    是分母——单格查询时是"该格已知的装备数"，区间并集查询时是"区间内至少
    一格已知的装备数"（不是装备数 × 格数，也不是命中次数之和）。"""

    n: int
    counts: Counter
    filters_desc: str

    def rows(self, top: int = 15) -> list[tuple[str, int, float, tuple[float, float]]]:
        out = []
        for affix, k in self.counts.most_common(top):
            p = k / self.n if self.n else 0.0
            out.append((affix, k, p, wilson(k, self.n)))
        return out


def slot_distribution(items: list[SlotItem], slot: int, *, part: str | None = None,
                      first_affix: str | None = None,
                      exclude_transferred: bool = True) -> SlotStat:
    """P(第 slot 格 = 词条)——终态口径。"""
    sub = _apply_filters(items, part=part, first_affix=first_affix,
                         exclude_transferred=exclude_transferred)
    c = Counter(it.slots[slot] for it in sub if slot in it.slots)
    desc = f"第 {slot} 格" + (f"，部位={part}" if part else "") + \
          (f"，首词条={first_affix}" if first_affix else "")
    return SlotStat(n=sum(c.values()), counts=c, filters_desc=desc)


def slot_range_distribution(items: list[SlotItem], slot_lo: int, slot_hi: int, *,
                            part: str | None = None, first_affix: str | None = None,
                            exclude_transferred: bool = True) -> SlotStat:
    """P(词条出现在 [slot_lo, slot_hi] 区间任意一格)——并集口径。

    分母 ``n`` 是"区间内至少一格已知"的装备数；``sum(counts.values())``
    可能大于 ``n``——一件装备的区间内可以同时命中多个不同词条，这是并集
    口径的正常现象，不是重复计数。
    """
    sub = _apply_filters(items, part=part, first_affix=first_affix,
                         exclude_transferred=exclude_transferred)
    c: Counter = Counter()
    n = 0
    for it in sub:
        present = {it.slots[s] for s in range(slot_lo, slot_hi + 1) if s in it.slots}
        if not present:
            continue
        n += 1
        c.update(present)
    rng = f"第 {slot_lo} 格" if slot_lo == slot_hi else f"第 {slot_lo}-{slot_hi} 格"
    desc = rng + (f"，部位={part}" if part else "") + \
          (f"，首词条={first_affix}" if first_affix else "")
    return SlotStat(n=n, counts=c, filters_desc=desc)


def conditional_slot_distribution(
    items: list[SlotItem], *, given_slot: int, given_affix: str,
    target_lo: int, target_hi: int, part: str | None = None,
    first_affix: str | None = None, exclude_transferred: bool = True,
) -> SlotStat:
    """P(target 区间任意一格出现某词条 | 第 given_slot 格 = given_affix)。

    用于"第 2 格出现会心以后，第 3 格及以后出现各词条的概率"这类问题：
    ``given_slot=2, given_affix="会心", target_lo=3, target_hi=<最大格数>``。
    """
    sub = _apply_filters(items, part=part, first_affix=first_affix,
                         exclude_transferred=exclude_transferred)
    sub = [it for it in sub if it.slots.get(given_slot) == given_affix]
    stat = slot_range_distribution(sub, target_lo, target_hi,
                                   exclude_transferred=False)  # 已经过滤过
    target_desc = (f"第 {target_lo} 格" if target_lo == target_hi
                   else f"第 {target_lo}-{target_hi} 格")
    stat.filters_desc = (f"第 {given_slot} 格={given_affix} 之后，{target_desc}"
                         + (f"，部位={part}" if part else "")
                         + (f"，首词条={first_affix}" if first_affix else ""))
    return stat


def observed_slots(items: list[SlotItem]) -> list[int]:
    """数据里实际出现过的槽位号，从小到大——渲染报告时用来决定要列几格，
    不同部位的槽位数不一样（武器/戒指等可能比甲类少），不能写死上限。"""
    seen: set[int] = set()
    for it in items:
        seen.update(it.slots)
    return sorted(seen)


def parse_slot_range(text: str) -> tuple[int, int]:
    """把 ``"3"`` 或 ``"3-5"`` 这种字符串解析成 ``(lo, hi)``——CLI 的
    ``--target-slots`` 和网页表单共用这个解析，格式不对时抛
    ``ValueError``（调用方各自决定怎么报错）。"""
    text = text.strip()
    if "-" in text:
        lo_s, _, hi_s = text.partition("-")
        lo, hi = int(lo_s), int(hi_s)
    else:
        lo = hi = int(text)
    if lo < 1 or hi < lo:
        raise ValueError(f"格位区间不合法：{text!r}")
    return lo, hi
