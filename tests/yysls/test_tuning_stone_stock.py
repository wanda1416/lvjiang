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


def _equip(
    affixes: int, quality: str = "gold", level: int = 105,
) -> EquipmentData:
    return EquipmentData(level=level, quality=quality,
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
    stock.observe_equipment(_equip(3))
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


def test_cached_stock_is_isolated_by_equipment_level(monkeypatch):
    import lvjiang.apps.yysls.workflows.implementations.tuning.stone_stock as mod

    manager = SimpleNamespace(level_config_for=lambda _level: _rule_level())
    monkeypatch.setattr(mod, "get_game_config", lambda: manager)
    stock = CachedStoneStock()
    level_110 = _equip(3, level=110)
    level_105 = _equip(3, level=105)

    stock.observe_equipment(level_110)
    stock.accept_scan(1000)
    stock.record_tune(level_110, target_affix=4)
    assert stock.stock_units == 760
    stock.mark_initial_check_done()

    stock.observe_equipment(level_105)
    assert stock.needs_scan
    assert stock.needs_initial_check
    assert stock.stock_units is None
    stock.accept_scan(500)
    stock.record_tune(level_105, target_affix=4)
    assert stock.stock_units == 260

    stock.observe_equipment(level_110)
    assert not stock.needs_scan
    assert not stock.needs_initial_check
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


def test_entry_scan_small_only_and_failed_scan_do_not_reuse_previous_stock():
    from lvjiang.apps.yysls.workflows.implementations.tuning.executor import (
        SMALL_STONE_LABEL,
        STONE_LABEL,
        TuningExecutor,
    )
    stock = EntryScanStoneStock()
    wf = SimpleNamespace(stone_stock=stock, equipment_session=SimpleNamespace(equipment=None))
    executor = TuningExecutor(wf)
    executor._initial_stock_check_done = True
    failures = []
    executor._handle_hard_stone_failure = lambda *args: failures.append(args) or False
    settings = SimpleNamespace(stone_check_enabled=True, stone_min_count=1)
    small = SimpleNamespace(label=SMALL_STONE_LABEL, count=50, count_recognized=True)
    executor._accept_stone_scan({SMALL_STONE_LABEL: small})
    assert executor._check_stone_stock(settings, {"slot": small})
    assert stock.stock_units == 50
    invalid = SimpleNamespace(label=STONE_LABEL, count=0, count_recognized=False)
    assert not executor._check_stone_stock(settings, {"slot": invalid})
    assert stock.stock_units is None
    stock.accept_scan(500)
    executor._accept_stone_scan({STONE_LABEL: invalid})
    assert not executor._check_stone_stock(settings, None)
    assert stock.stock_units is None
    assert len(failures) == 2
    valid = SimpleNamespace(label=STONE_LABEL, count=10, count_recognized=True)
    assert executor._check_stone_stock(settings, {"slot": valid})
    assert stock.stock_units == 100


def test_valid_cached_stock_survives_failed_optional_scan():
    stock = CachedStoneStock()
    stock.accept_scan(500)
    stock.accept_scan(None)
    assert stock.stock_units == 500
    stock.invalidate("缺少消耗规则")
    stock.accept_scan(None)
    assert stock.stock_units is None
    assert stock.needs_scan
