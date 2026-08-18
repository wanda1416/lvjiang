from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
import yaml


@pytest.fixture
def profile_func_env(tmp_path, monkeypatch):
    session_dir = tmp_path / "session"
    session_dir.mkdir()

    import lvjiang.apps.yysls.config.user_profile as profile_config
    import lvjiang.apps.yysls.core.profile_engine.profile_db as profile_db

    profile_config._config = None
    profile_config._PROFILE_PATH = session_dir / "profile.yaml"
    profile_config._PROFILE_PATH.write_text(
        yaml.dump(
            {
                "regen": [
                    {
                        "key": "xinli",
                        "label": "心力",
                        "cap": 600,
                        "regen_type": "realtime",
                        "regen_rate_value": 0.125,
                        "regen_rate_unit": "minute",
                        "sync_targets": [{"key": "stock:target_stock"}],
                    }
                ],
                "stock": [
                    {"key": "target_stock", "label": "同步目标"},
                ],
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )

    profile_db._db = None
    profile_db._DB_PATH = session_dir / "profile.db"

    yield SimpleNamespace(username="u", session_dir=session_dir)

    profile_config._config = None
    profile_db._db = None


def test_profile_all_computes_realtime_regen_value(profile_func_env):
    from lvjiang.apps.yysls.core.profile_engine.profile_db import db_upsert
    from lvjiang.apps.yysls.workflows.builtins.profile_funcs import (
        _profile_all,
        _profile_get,
    )

    updated_at = (datetime.now() - timedelta(minutes=4)).isoformat(timespec="seconds")
    db_upsert(profile_func_env.username, "regen", "xinli", 100, updated_at=updated_at)

    engine = SimpleNamespace(run_username=profile_func_env.username)
    by_get = _profile_get(engine, "xinli")
    by_all = _profile_all(engine)["regen"]["xinli"]["value"]

    assert 100.49 <= by_get <= 100.51
    assert by_all == pytest.approx(by_get, abs=0.02)


def test_profile_inc_preserves_realtime_fraction_progress(profile_func_env):
    from lvjiang.apps.yysls.core.profile_engine.profile_db import (
        db_read_entry,
        db_upsert,
    )
    from lvjiang.apps.yysls.workflows.builtins.profile_funcs import _profile_inc

    updated_at = (datetime.now() - timedelta(minutes=4)).isoformat(timespec="seconds")
    db_upsert(profile_func_env.username, "regen", "xinli", 100, updated_at=updated_at)

    engine = SimpleNamespace(run_username=profile_func_env.username)
    returned = _profile_inc(engine, "xinli", -20)
    entry = db_read_entry(profile_func_env.username, "regen", "xinli")
    stored_ts = datetime.fromisoformat(entry["updated_at"])

    assert returned == pytest.approx(80.5, abs=0.02)
    assert entry["value"] == 80
    # 容差 2 秒：isoformat(timespec="seconds") 截断小数秒，CI 环境时序不稳定
    assert datetime.now() - timedelta(minutes=4, seconds=2) <= stored_ts
    assert stored_ts <= datetime.now() - timedelta(minutes=3, seconds=58)


def test_realtime_sync_uses_semantic_delta_not_stored_integer_delta(profile_func_env):
    from lvjiang.apps.yysls.core.profile_engine.profile_db import (
        db_read_entry,
        db_upsert,
    )
    from lvjiang.apps.yysls.core.profile_engine.profile_ops import sync_write_adapter

    updated_at = (datetime.now() - timedelta(minutes=4)).isoformat(timespec="seconds")
    db_upsert(profile_func_env.username, "regen", "xinli", 100, updated_at=updated_at)
    db_upsert(profile_func_env.username, "stock", "target_stock", 1000)

    result = sync_write_adapter(
        profile_func_env.username,
        "regen",
        "xinli",
        delta=-20,
        source="test",
    )
    target = db_read_entry(profile_func_env.username, "stock", "target_stock")

    assert result is not None
    new_value, applied_delta = result
    assert new_value == pytest.approx(80.5, abs=0.02)
    assert applied_delta == pytest.approx(-20, abs=0.02)
    assert target["value"] == 1000
