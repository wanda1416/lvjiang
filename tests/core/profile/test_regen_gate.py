"""引擎 tick 不把周期恢复写进账本。消耗本身当时落库，见 test_set_only。"""

from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

import yaml

from lvjiang.core.profile import repository, schema


def test_tick_does_not_materialize_recovery(tmp_path, monkeypatch) -> None:
    session_dir = tmp_path / "session"
    session_dir.mkdir()
    profile_yaml = session_dir / "profile.yaml"
    profile_yaml.write_text(
        yaml.dump(
            {
                "regen": [
                    {"key": "energy", "label": "体力", "cap": 100,
                     "regen_type": "boundary", "regen_amount": 10,
                     "regen_period": "day", "reset_time": "05:00"},
                ],
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(schema, "_PROFILE_PATH", profile_yaml, raising=False)
    monkeypatch.setattr(schema, "_config", None, raising=False)
    monkeypatch.setattr(repository, "_DB_PATH", session_dir / "profile.db", raising=False)
    repository.reset_profile_db()

    now = datetime.now()
    boundary = now.replace(hour=5, minute=0, second=0, microsecond=0)
    if boundary > now:
        boundary -= timedelta(days=1)
    anchor = (boundary - timedelta(minutes=1)).isoformat(timespec="seconds")
    repository.db_upsert("u1", "regen", "energy", 90, event_at=anchor)

    from lvjiang.core.profile.engine import ProfileProcessor

    processor = ProfileProcessor(SimpleNamespace(list_users=lambda: ["u1"]))
    processor._last_clean = 1e18
    processor.tick()

    entry = repository.db_read_entry("u1", "regen", "energy")
    assert entry["value"] == 90
    assert entry["event_at"] == anchor
    assert repository.db_get_history("u1", "regen", "energy") == []
    repository.reset_profile_db()
