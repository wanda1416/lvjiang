"""智能调律的「剩余词条补全搜索」策略层。

评估器（``smart_tuning.py``）负责方案快照、部位定位、属性聚合与缓存；
**怎么在候选词条里找一个能提升毕业率的补全**是独立的搜索问题，抽成策略：

    SearchProblem  ──►  SearchStrategy.search()  ──►  SearchOutcome

策略只看到三样东西：合法候选词条名、"给装备补上这些词条"的回调（含合法性
校验与承音上限取值）、"给这件装备打分"的回调（毕业率）。它不知道备战方案、
不碰缓存、不做部位判断——换算法只改本文件，评估器和自动调律流程不动。

算法结论（真实用户数据对拍，见 GreedySwapStrategy）：贪心在这个问题上几乎
总是最优，唯一失误来源是 势/敏 与整条会意/会心率在三率临界点上的取舍；
默认策略即"贪心补满 + 临界交换验证"，不做通用组合穷举。
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from decimal import ROUND_FLOOR, Decimal
from enum import Enum


class SearchStatus(str, Enum):
    IMPROVES = "improves"            # 找到一个满足比较条件的合法补全
    NO_IMPROVEMENT = "no_improvement"  # 穷尽（或可证明）所有合法补全均不满足
    UNKNOWN = "unknown"              # 取消 / 超预算 / 没有合法补全，必须放行


def floor_rate(rate: float, precision: float) -> Decimal:
    """按所选毕业率精度向下归档，避开二进制浮点边界漂移。"""
    value = Decimal(str(rate))
    step = Decimal(str(precision))
    return (value / step).to_integral_value(rounding=ROUND_FLOOR) * step


def passes(candidate: float, baseline: float, operator: str,
           precision: float = 0.001) -> bool:
    """双方按同一精度向下归档，再执行严格提升或允许持平比较。"""
    candidate_bucket = floor_rate(candidate, precision)
    baseline_bucket = floor_rate(baseline, precision)
    if operator == "gte":
        return candidate_bucket >= baseline_bucket
    return candidate_bucket > baseline_bucket


class SearchBudget:
    """搜索预算：墙钟上限 + 停止检查。

    ``stop_check`` 可能内部阻塞（工作流的暂停检查点就在里面），阻塞期间不计
    入预算——用户暂停 10 秒再恢复不应该被当成"计算超时"。
    """

    def __init__(self, seconds: float, stop_check: Callable[[], bool] | None = None):
        self._limit = float(seconds)
        self._stop_check = stop_check or (lambda: False)
        self._started = time.perf_counter()
        self._paused = 0.0
        self.stopped = False
        self.expired = False

    def exhausted(self) -> bool:
        """每个搜索节点调用一次；返回 True 表示应立即以 UNKNOWN 收尾。"""
        if self.stopped or self.expired:
            return True
        before = time.perf_counter()
        if self._stop_check():
            self.stopped = True
            return True
        after = time.perf_counter()
        # stop_check 里阻塞的时间（暂停）从预算里扣掉
        self._paused += after - before
        if after - self._started - self._paused > self._limit:
            self.expired = True
            return True
        return False

    @property
    def reason(self) -> str:
        if self.stopped:
            return "用户中断智能调律计算"
        if self.expired:
            return "计算超时"
        return ""


@dataclass
class SearchProblem:
    """一次补全搜索的输入。

    Attributes:
        equipment: 当前装备（已含真实词条），策略不得修改它。
        candidates: 去重后的合法候选词条名，顺序可作为默认分支顺序。
        missing: 还要补几条。0 表示装备已满，只需评估当前状态。
        baseline: 比较基准（方案当前毕业率）。
        operator: ``gt`` / ``gte``。
        complete: ``(equipment, names) -> dict | None``，把 names 补进空位并做
            合法性校验；非法返回 None。
        rate: ``dict -> float``，给一件（可能未补满的）装备打分。
        budget: 预算与停止检查。
        precision: 毕业率比较精度，双方按此向下归档后再比较。
    """

    equipment: dict
    candidates: list[str]
    missing: int
    baseline: float
    operator: str
    complete: Callable[[dict, tuple[str, ...]], dict | None]
    rate: Callable[[dict], float]
    budget: SearchBudget
    precision: float = 0.001


@dataclass
class SearchOutcome:
    status: SearchStatus
    reason: str
    maximum_rate: float | None = None
    evaluated: int = 0
    winning_affixes: tuple[str, ...] = field(default_factory=tuple)


class SearchStrategy:
    """策略接口：子类只需实现 ``search``。"""

    name = "base"

    def search(self, problem: SearchProblem) -> SearchOutcome:
        raise NotImplementedError


# ─── 默认策略：理论敏感度贪心 + 三率临界交换 ───────────────

#: 五维里同时喂伤害数值与三率的两条词条，及其对应的"整条率"词条。
#: 装备词条几乎都只作用于伤害数值或三率一侧，贪心可加；唯独 势/敏 各给
#: 半条率外加攻击，会意/会心率处在黄字判定临界点时，"半条 + 攻击"与
#: "整条率"谁更优取决于最终组合，单条边际看不出来——这是实测数据里贪心
#: 唯一的失误来源。
SWAP_PAIRS: tuple[tuple[str, str], ...] = (("势", "会意率"), ("敏", "会心率"))


class GreedySwapStrategy(SearchStrategy):
    """贪心补满，再对 势↔会意率、敏↔会心率 做双向临界交换验证。

    实测（5 位真实用户、486 个装备×方案案例）：纯贪心 484 次与穷举最优一致，
    2 次次优（差 6×10⁻⁵）且都是"贪心选了 势，而整条会意率恰好把会意率顶到
    40.00%"。交换必须双向：反例里贪心第一步选的是半条率，不是整条率，且
    "恰好达到上限"也算临界，所以不能靠"整条率是否溢出"来触发。交换验证
    至多 4 次额外求值，覆盖全部反例后与穷举一致；不做通用组合穷举。
    """

    name = "greedy_swap"

    def search(self, problem: SearchProblem) -> SearchOutcome:
        if problem.missing <= 0:
            completed = problem.complete(problem.equipment, ())
            if completed is None:
                return SearchOutcome(SearchStatus.UNKNOWN, "当前装备组合不合法")
            value = problem.rate(completed)
            if passes(value, problem.baseline, problem.operator,
                      problem.precision):
                return SearchOutcome(SearchStatus.IMPROVES, "当前状态已可提升", value, 1)
            return SearchOutcome(
                SearchStatus.NO_IMPROVEMENT, "装备已满且无法提升", value, 1)

        greedy, evaluated = self._greedy(problem)
        if greedy is None:
            if problem.budget.exhausted():
                return SearchOutcome(SearchStatus.UNKNOWN, problem.budget.reason,
                                     evaluated=evaluated)
            return SearchOutcome(SearchStatus.UNKNOWN, "没有可用的合法词条补全组合")
        best_names, best = greedy
        if passes(best, problem.baseline, problem.operator, problem.precision):
            return SearchOutcome(
                SearchStatus.IMPROVES, "理论敏感度贪心补全可提升",
                best, evaluated, best_names)

        swapped = 0
        for variant in self._swap_variants(best_names, problem.candidates):
            if problem.budget.exhausted():
                return SearchOutcome(
                    SearchStatus.UNKNOWN, problem.budget.reason, best, evaluated)
            completed = problem.complete(problem.equipment, variant)
            if completed is None:
                continue
            evaluated += 1
            swapped += 1
            value = problem.rate(completed)
            if value > best:
                best, best_names = value, variant
            if passes(value, problem.baseline, problem.operator, problem.precision):
                return SearchOutcome(
                    SearchStatus.IMPROVES, "三率临界交换后可提升",
                    value, evaluated, variant)
        note = f"（已比较 {swapped} 个三率临界交换）" if swapped else ""
        return SearchOutcome(
            SearchStatus.NO_IMPROVEMENT,
            f"理论敏感度补全无法提升{note}", best, evaluated, best_names)

    # ── 内部 ──

    @staticmethod
    def _greedy(
        problem: SearchProblem,
    ) -> tuple[tuple[tuple[str, ...], float] | None, int]:
        """逐条补入当前边际收益最大的候选；补不满或被打断返回 None。"""
        current = problem.equipment
        remaining = list(problem.candidates)
        chosen: list[str] = []
        evaluated = 0
        value = float("-inf")
        while len(chosen) < problem.missing:
            choices: list[tuple[float, str, dict]] = []
            for name in remaining:
                if problem.budget.exhausted():
                    return None, evaluated
                trial = problem.complete(current, (name,))
                if trial is not None:
                    choices.append((problem.rate(trial), name, trial))
                    evaluated += 1
            if not choices:
                return None, evaluated
            value, name, current = max(choices, key=lambda item: item[0])
            chosen.append(name)
            remaining.remove(name)
        # 只在补满后比较，避免拿未补满的中间态当"最高毕业率"
        return (tuple(chosen), value), evaluated

    @staticmethod
    def _swap_variants(
        names: tuple[str, ...], candidates: list[str],
    ) -> list[tuple[str, ...]]:
        """把选中的 势/敏 换成整条率、或反向，生成待验证的组合。"""
        variants: list[tuple[str, ...]] = []
        pool = set(candidates)
        for dim, rate_affix in SWAP_PAIRS:
            for source, target in ((dim, rate_affix), (rate_affix, dim)):
                if source in names and target in pool and target not in names:
                    variants.append(tuple(target if n == source else n for n in names))
        return variants


# ─── 注册表 ────────────────────────────────────────────────

_STRATEGIES: dict[str, SearchStrategy] = {}
DEFAULT_STRATEGY = GreedySwapStrategy.name


def register_strategy(strategy: SearchStrategy) -> SearchStrategy:
    _STRATEGIES[strategy.name] = strategy
    return strategy


def get_strategy(name: str | None = None) -> SearchStrategy:
    """按名取策略；未知名字回退默认策略（不能因为配置错误让判定挂掉）。"""
    return _STRATEGIES.get(name or DEFAULT_STRATEGY, _STRATEGIES[DEFAULT_STRATEGY])


def available_strategies() -> Iterable[str]:
    return tuple(_STRATEGIES)


register_strategy(GreedySwapStrategy())
