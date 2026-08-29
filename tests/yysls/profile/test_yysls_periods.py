from datetime import date, datetime
from types import SimpleNamespace

from lvjiang.apps.yysls.profile import periods
from lvjiang.core.profile.periods import get_profile_period


def _season():
    return SimpleNamespace(
        start_date=date(2026, 8, 1),
        first_half_end_date=date(2026, 8, 15),
        end_date=date(2026, 8, 31),
    )


def test_season_boundary_comes_from_yysls_game_config(monkeypatch):
    monkeypatch.setattr(
        periods,
        "get_game_config",
        lambda: SimpleNamespace(get_season_configs=lambda: [_season()]),
    )
    result = periods.resolve_season_boundary(
        "05:00", datetime(2026, 8, 20, 12), 0
    )
    assert result == datetime(2026, 8, 1, 5)


def test_half_season_boundary_uses_second_half_start(monkeypatch):
    monkeypatch.setattr(
        periods,
        "get_game_config",
        lambda: SimpleNamespace(get_season_configs=lambda: [_season()]),
    )
    result = periods.resolve_half_season_boundary(
        "05:00", datetime(2026, 8, 20, 12), 0
    )
    assert result == datetime(2026, 8, 16, 5)


def test_registered_season_labels_are_translated_by_app_catalog():
    """周期标签是动态 tr() 入参，AST 翻译棘轮无法自动发现。"""
    import lvjiang.i18n as i18n

    original = i18n.current_language()
    try:
        i18n.init_i18n("en_US")
        i18n.load_app_i18n("yysls")
        season = get_profile_period("season")
        half_season = get_profile_period("half_season")
        assert season is not None
        assert half_season is not None
        assert i18n.tr(season.label) == "Season"
        assert i18n.tr(half_season.label) == "Half Season"
    finally:
        i18n.init_i18n(original)
