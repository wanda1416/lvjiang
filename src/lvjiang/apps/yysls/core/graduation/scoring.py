"""毕业率评分内核：“一套装备 → 毕业率”只有这一份实现。

链路固定四段：``effective_equipped``（游戏规则归一化）→ 装备基础属性 +
词条聚合 → ``build_graduation_attrs``（并入基础属性、套抗性）→ 计算器。
备战方案面板、培养/转律建议、智能调律都从这里取分；最优组合的向量内环
为性能单独实现，由对拍测试守卫与本模块一致。

评分按最终属性签名缓存：不同词条名映射到相同属性输入（例如非本流派
属攻）只算一次。``evaluated`` 统计真正调用计算器的次数。
"""
from __future__ import annotations

import json
import time
from collections.abc import Callable

from ..combat.combat_attrs import (
    CombatAttributes,
    aggregate_equipment_attrs,
    build_graduation_attrs,
    compute_equip_base_attrs,
    effective_equipped,
)


class BudgetExceeded(Exception):
    """预算（时间或外部取消）耗尽；调用方决定保留已有结果还是放弃。"""


def equipment_attrs(equipped: dict, game_config) -> CombatAttributes:
    """归一化后的装备属性总和（基础外功 + 词条/定音/五维换算）。"""
    effective = effective_equipped(equipped, game_config)
    return compute_equip_base_attrs(
        effective, game_config.get_base_attr_values,
    ) + aggregate_equipment_attrs(effective, normalize=False)


def graduation_input(
    base_attrs: CombatAttributes, equipped: dict, school: str, game_config=None,
) -> CombatAttributes:
    """“基础属性 + 一套装备 → 毕业率输入”：归一化、聚合、并入基础属性、套抗性。

    不需要计算器的调用方（备战方案面板只做属性展示与后台求值）直接用它；
    ``LoadoutScorer.attrs`` 内部也是它。
    """
    if game_config is None:
        from ...config import get_game_config
        game_config = get_game_config()
    return build_graduation_attrs(
        base_attrs, equipment_attrs(equipped, game_config), school)


class LoadoutScorer:
    def __init__(
        self,
        calculator,
        base_attrs: CombatAttributes,
        school: str,
        game_config=None,
        *,
        stop_check: Callable[[], bool] | None = None,
        time_budget: float = 0.0,
    ) -> None:
        if game_config is None:
            from ...config import get_game_config
            game_config = get_game_config()
        self.calculator = calculator
        self.base_attrs = base_attrs
        self.school = school
        self.game_config = game_config
        self._stop_check = stop_check
        self._deadline = (
            time.monotonic() + time_budget if time_budget > 0 else None)
        self._cache: dict[str, float] = {}
        self.evaluated = 0

    # ── 预算 ──

    def check_budget(self) -> None:
        if self._stop_check is not None and self._stop_check():
            raise BudgetExceeded
        if self._deadline is not None and time.monotonic() > self._deadline:
            raise BudgetExceeded

    # ── 属性 ──

    def equipment_attrs(self, equipped: dict) -> CombatAttributes:
        return equipment_attrs(equipped, self.game_config)

    def attrs(self, equipped: dict) -> CombatAttributes:
        """整套装备 + 基础属性并套抗性后的毕业率输入。"""
        return graduation_input(
            self.base_attrs, equipped, self.school, self.game_config)

    # ── 评分 ──

    @staticmethod
    def signature(attrs: CombatAttributes) -> str:
        return json.dumps(
            attrs.to_dict(), ensure_ascii=False, sort_keys=True,
            separators=(",", ":"))

    def rate_attrs(self, attrs: CombatAttributes) -> float:
        signature = self.signature(attrs)
        cached = self._cache.get(signature)
        if cached is not None:
            return cached
        self.check_budget()
        value = float(self.calculator.calculate(attrs).graduation_rate)
        self._cache[signature] = value
        self.evaluated += 1
        return value

    def rate(self, equipped: dict) -> float:
        return self.rate_attrs(self.attrs(equipped))
