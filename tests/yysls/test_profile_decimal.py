from __future__ import annotations

from types import SimpleNamespace

import pytest
import yaml

from lvjiang.apps.yysls.config.profile_models import (
    MODEL_REGEN,
    MODEL_STOCK,
    RegenKeyDef,
    StockKeyDef,
)
from lvjiang.apps.yysls.ui.profile.overview import _PARSE_ERROR, _parse_value


def test_parse_value_allows_float_only_when_decimal(monkeypatch):
    monkeypatch.setattr(
        "lvjiang.apps.yysls.ui.profile.overview.QMessageBox.warning",
        lambda *args, **kwargs: None,
    )

    assert _parse_value("12.5", MODEL_STOCK, StockKeyDef(key="a", decimal=True)) == 12.5
    assert _parse_value("12.5", MODEL_STOCK, StockKeyDef(key="a")) is _PARSE_ERROR


def test_parse_value_realtime_regen_still_uses_decimal_flag(monkeypatch):
    monkeypatch.setattr(
        "lvjiang.apps.yysls.ui.profile.overview.QMessageBox.warning",
        lambda *args, **kwargs: None,
    )

    kd = RegenKeyDef(
        key="xinli",
        label="心力",
        regen_type="realtime",
        regen_rate_value=0.125,
        regen_rate_unit="minute",
    )
    assert _parse_value("100.5", MODEL_REGEN, kd) is _PARSE_ERROR

    kd.decimal = True
    assert _parse_value("100.5", MODEL_REGEN, kd) == 100.5


@pytest.fixture
def decimal_env(tmp_path):
    session_dir = tmp_path / "session"
    session_dir.mkdir()

    import lvjiang.apps.yysls.config.user_profile as profile_config
    import lvjiang.apps.yysls.core.profile_engine.profile_db as profile_db

    profile_config._config = None
    profile_config._PROFILE_PATH = session_dir / "profile.yaml"
    profile_config._PROFILE_PATH.write_text(
        yaml.dump(
            {
                "stock": [
                    {"key": "tongbao", "label": "通宝", "decimal": True},
                    {"key": "changmingyu", "label": "长鸣玉"},
                ],
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    profile_db._db = None
    profile_db._DB_PATH = session_dir / "profile.db"

    yield SimpleNamespace(username="u")

    profile_config._config = None
    profile_db._db = None


def test_profile_action_normalizes_tiny_float_noise(decimal_env):
    from lvjiang.apps.yysls.core.profile_engine.profile_db import db_read_entry
    from lvjiang.apps.yysls.core.profile_engine.profile_ops import profile_action

    result = profile_action(decimal_env.username, "tongbao", set_value=12.0000000001)
    entry = db_read_entry(decimal_env.username, MODEL_STOCK, "tongbao")

    assert result == 12.0
    assert entry["value"] == 12.0


def test_profile_action_keeps_real_decimal(decimal_env):
    from lvjiang.apps.yysls.core.profile_engine.profile_db import db_read_entry
    from lvjiang.apps.yysls.core.profile_engine.profile_ops import profile_action

    result = profile_action(decimal_env.username, "tongbao", set_value=12.5)
    entry = db_read_entry(decimal_env.username, MODEL_STOCK, "tongbao")

    assert result == 12.5
    assert entry["value"] == 12.5


def test_profile_action_does_not_block_real_decimal_on_non_decimal_key(decimal_env):
    from lvjiang.apps.yysls.core.profile_engine.profile_db import db_read_entry
    from lvjiang.apps.yysls.core.profile_engine.profile_ops import profile_action

    result = profile_action(decimal_env.username, "changmingyu", set_value=12.5)
    entry = db_read_entry(decimal_env.username, MODEL_STOCK, "changmingyu")

    assert result == 12.5
    assert entry["value"] == 12.5
