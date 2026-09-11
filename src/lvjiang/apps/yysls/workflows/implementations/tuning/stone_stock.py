"""律准石库存策略。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from loguru import logger

from lvjiang.apps.yysls.config import get_game_config
from lvjiang.apps.yysls.core.equip_parser import EquipmentData


def format_stone_units(units: int) -> str:
    """将 0.1 枚的整数单位格式化为大律准石等价数。"""
    whole, tenth = divmod(units, 10)
    return str(whole) if tenth == 0 else f"{whole}.{tenth}"


class StoneStockStrategy(ABC):
    """律准石库存数据源与记账策略。"""

    uses_cache = False

    @property
    @abstractmethod
    def needs_scan(self) -> bool: ...

    @property
    def needs_initial_check(self) -> bool:
        return False

    @property
    @abstractmethod
    def stock_units(self) -> int | None: ...

    @property
    def cache_invalid(self) -> bool:
        return False

    @property
    def invalid_reason(self) -> str:
        return ""

    @abstractmethod
    def reset(self) -> None: ...

    @abstractmethod
    def accept_scan(self, units: int | None) -> None: ...

    @abstractmethod
    def set_manual(self, units: int) -> None: ...

    def observe_equipment(self, equip: EquipmentData) -> None:
        del equip

    def required_tune_units(self, equip: EquipmentData,
                            target_affix: int) -> int | None:
        del equip, target_affix
        return None

    def record_tune(self, equip: EquipmentData, target_affix: int) -> None:
        del equip, target_affix

    def record_reset(self, equip: EquipmentData,
                     previous_affix_count: int) -> None:
        del equip, previous_affix_count

    def record_recycle(self, equip: EquipmentData,
                       current_affix_count: int) -> None:
        del equip, current_affix_count


class EntryScanStoneStock(StoneStockStrategy):
    """不缓存：每个库存检查点都重新识别。"""

    def __init__(self) -> None:
        self._stock_units: int | None = None

    @property
    def needs_scan(self) -> bool:
        return True

    @property
    def stock_units(self) -> int | None:
        return self._stock_units

    def reset(self) -> None:
        self._stock_units = None

    def accept_scan(self, units: int | None) -> None:
        self._stock_units = units

    def set_manual(self, units: int) -> None:
        self._stock_units = units


@dataclass
class _CachedLevelStock:
    stock_units: int | None = None
    initialized: bool = False
    initial_check_done: bool = False
    cache_invalid: bool = False
    invalid_reason: str = ""


class CachedStoneStock(StoneStockStrategy):
    """按装备等级隔离的运行期律准石库存账本。"""

    uses_cache = True

    def __init__(self) -> None:
        self.reset()

    def _pool(self) -> _CachedLevelStock:
        return self._level_pools.setdefault(
            self._active_level, _CachedLevelStock())

    def _select_equipment_level(self, equip: EquipmentData) -> None:
        level = int(equip.level or 0)
        if level != self._active_level:
            self._active_level = level
            logger.debug(f"切换律准石缓存池: level={level}")

    @property
    def needs_scan(self) -> bool:
        pool = self._pool()
        return not pool.initialized or pool.cache_invalid

    @property
    def needs_initial_check(self) -> bool:
        pool = self._pool()
        return not pool.initial_check_done and not pool.cache_invalid

    @property
    def stock_units(self) -> int | None:
        return self._pool().stock_units

    @property
    def cache_invalid(self) -> bool:
        return self._pool().cache_invalid

    @property
    def invalid_reason(self) -> str:
        return self._pool().invalid_reason

    def reset(self) -> None:
        self._level_pools: dict[int, _CachedLevelStock] = {}
        self._active_level = 0

    def accept_scan(self, units: int | None) -> None:
        pool = self._pool()
        if not pool.initialized or pool.cache_invalid:
            pool.stock_units = units
            pool.initialized = units is not None

    def set_manual(self, units: int) -> None:
        pool = self._pool()
        pool.stock_units = units
        pool.initialized = True

    def mark_initial_check_done(self) -> None:
        self._pool().initial_check_done = True

    def invalidate(self, reason: str) -> None:
        pool = self._pool()
        if not pool.cache_invalid:
            logger.error(
                f"律准石 cache_invalid: level={self._active_level}, {reason}")
        pool.cache_invalid = True
        pool.invalid_reason = reason

    def observe_equipment(self, equip: EquipmentData) -> None:
        self._select_equipment_level(equip)
        if equip.quality == "blue":
            self.invalidate("遇到不支持缓存记账的蓝色装备")

    def _delta(self, equip: EquipmentData, operation: str,
               affix_count: int) -> int | None:
        self._select_equipment_level(equip)
        pool = self._pool()
        if not pool.initialized or pool.cache_invalid:
            return None
        if equip.quality == "blue":
            self.invalidate("遇到不支持缓存记账的蓝色装备")
            return None
        if equip.quality not in ("gold", "purple"):
            self.invalidate(f"装备品阶无效: {equip.quality!r}")
            return None
        if affix_count not in range(1, 6):
            self.invalidate(f"词条数超出 1-5: {affix_count}")
            return None
        level_cfg = get_game_config().level_config_for(equip.level or 0)
        if level_cfg is None:
            self.invalidate(f"等级配置缺失: {equip.level}")
            return None
        rule = level_cfg.tuning_stones.get(equip.quality)
        if rule is None:
            self.invalidate(
                f"律准石规则缺失: level={equip.level}, "
                f"quality={equip.quality}")
            return None
        if operation == "reset" and level_cfg.reset_no_refund:
            return 0
        mapping = {
            "tune": rule.tune_cost,
            "reset": rule.reset_refund,
            "recycle": rule.recycle_refund,
        }[operation]
        if affix_count not in mapping:
            self.invalidate(
                f"律准石规则缺失: level={equip.level}, "
                f"quality={equip.quality}, operation={operation}, "
                f"affix_count={affix_count}")
            return None
        return mapping[affix_count]

    def _apply(self, delta: int, message: str) -> None:
        pool = self._pool()
        assert pool.stock_units is not None
        updated = pool.stock_units + delta
        if updated < 0:
            self.invalidate(
                f"库存扣减后为负数: {format_stone_units(updated)}")
            return
        pool.stock_units = updated
        logger.info(
            f"律准石缓存 level={self._active_level} {message}: "
            f"{format_stone_units(updated)}")

    def record_tune(self, equip: EquipmentData, target_affix: int) -> None:
        cost = self._delta(equip, "tune", target_affix)
        if cost is not None:
            self._apply(-cost, f"调律第 {target_affix} 词条 -"
                        f"{format_stone_units(cost)}")

    def required_tune_units(self, equip: EquipmentData,
                            target_affix: int) -> int | None:
        return self._delta(equip, "tune", target_affix)

    def record_reset(self, equip: EquipmentData,
                     previous_affix_count: int) -> None:
        refund = self._delta(equip, "reset", previous_affix_count)
        if refund is not None:
            self._apply(refund, f"重置 {previous_affix_count} 词条 +"
                        f"{format_stone_units(refund)}")

    def record_recycle(self, equip: EquipmentData,
                       current_affix_count: int) -> None:
        refund = self._delta(equip, "recycle", current_affix_count)
        if refund is not None:
            self._apply(refund, f"回收 {current_affix_count} 词条 +"
                        f"{format_stone_units(refund)}")


def create_stone_stock_strategy(use_cache: bool) -> StoneStockStrategy:
    return CachedStoneStock() if use_cache else EntryScanStoneStock()
