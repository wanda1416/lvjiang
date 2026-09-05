"""自动调律律准石规则解析与缓存记账。"""

from types import SimpleNamespace

from lvjiang.apps.yysls.config import (
    LevelConfig,
    TuningStoneRule,
    get_game_config,
)
from lvjiang.apps.yysls.core.equip_parser import EquipmentData
from lvjiang.apps.yysls.workflows.implementations.tuning.stone_stock import (
    CachedStoneStock,
    EntryScanStoneStock,
)


def _rule_level() -> LevelConfig:
    return LevelConfig(
        level=105,
        reset_no_refund=True,
        tuning_stones={
            "gold": TuningStoneRule(
                tune_cost={1: 0, 2: 60, 3: 120, 4: 240, 5: 360},
                recycle_refund={
                    1: 60, 2: 108, 3: 204, 4: 396, 5: 684},
            ),
        },
    )


def _equip(affixes: int, quality: str = "gold") -> EquipmentData:
    return EquipmentData(level=105, quality=quality,
                         affixes=[SimpleNamespace()] * affixes)


def test_system_105_gold_rule_uses_tenths():
    cfg = get_game_config().level_config_for(105)
    assert cfg is not None and cfg.reset_no_refund
    rule = cfg.tuning_stones["gold"]
    assert rule.tune_cost == {1: 0, 2: 60, 3: 120, 4: 240, 5: 360}
    assert rule.recycle_refund == {
        1: 60, 2: 108, 3: 204, 4: 396, 5: 684}


def test_cache_uses_target_affix_and_cumulative_refund(monkeypatch):
    import lvjiang.apps.yysls.workflows.implementations.tuning.stone_stock as mod

    manager = SimpleNamespace(level_config_for=lambda _level: _rule_level())
    monkeypatch.setattr(mod, "get_game_config", lambda: manager)
    stock = CachedStoneStock()
    stock.accept_scan(1000)

    # 已有 3 条时调律写第 4 条，不是按累计轮数。
    stock.record_tune(_equip(3), target_affix=4)
    assert stock.stock_units == 760
    # 105 级重置无返还；重置后的下一次明确写第 2 条。
    stock.record_reset(_equip(4), previous_affix_count=4)
    stock.record_tune(_equip(1), target_affix=2)
    assert stock.stock_units == 700
    # 未调律装备也按当前 1 词条的累计值返还。
    stock.record_recycle(_equip(1), current_affix_count=1)
    assert stock.stock_units == 760


def test_refund_before_initial_scan_is_not_double_counted(monkeypatch):
    import lvjiang.apps.yysls.workflows.implementations.tuning.stone_stock as mod

    manager = SimpleNamespace(level_config_for=lambda _level: _rule_level())
    monkeypatch.setattr(mod, "get_game_config", lambda: manager)
    stock = CachedStoneStock()
    stock.record_recycle(_equip(1), current_affix_count=1)
    assert stock.stock_units is None
    stock.accept_scan(1000)
    assert stock.stock_units == 1000


def test_blue_equipment_invalidates_cache():
    stock = CachedStoneStock()
    stock.accept_scan(1000)
    stock.observe_equipment(_equip(1, "blue"))
    assert stock.cache_invalid
    assert stock.needs_scan


def test_entry_scan_strategy_never_applies_deltas():
    stock = EntryScanStoneStock()
    stock.accept_scan(1000)
    stock.record_tune(_equip(1), target_affix=2)
    stock.record_recycle(_equip(1), current_affix_count=1)
    assert stock.stock_units == 1000
    assert stock.needs_scan
