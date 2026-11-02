"""撤销 = 反向写一次：走普通写入管线、按写入方意图记账、来源标注原来源。"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml


@pytest.fixture
def undo_env(tmp_path: Path, monkeypatch) -> None:
    from lvjiang.core.profile import repository, schema

    profile_path = tmp_path / "profile.yaml"
    profile_path.write_text(
        yaml.safe_dump(
            {
                "stock": [{"key": "credits", "label": "积分"}],
                "quota": [{"key": "weekly", "label": "周配额", "cap": 10}],
                "note": [{"key": "memo", "label": "备注"}],
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(schema, "_PROFILE_PATH", profile_path)
    monkeypatch.setattr(repository, "_DB_PATH", tmp_path / "profile.db")
    schema._config = None
    repository.reset_profile_db()


def _history(username: str, model: str, key: str) -> list[dict]:
    from lvjiang.core.profile.repository import db_get_history

    return db_get_history(username, type_=model, key=key)


def test_undo_delta_record_writes_reverse_delta(undo_env):
    from lvjiang.core.profile.service import (
        profile_action,
        profile_read,
        undo_profile_history,
    )

    profile_action("u", "credits", delta=5, source="手动登记")
    record = _history("u", "stock", "credits")[0]
    assert profile_read("u", "credits") == 5

    undo_profile_history(record["id"])

    assert profile_read("u", "credits") == 0
    latest = _history("u", "stock", "credits")[0]
    assert latest["change_type"] == "delta"
    assert latest["delta_value"] == -5
    assert latest["source"] == "撤销：手动登记"


def test_undo_set_record_restores_previous_value(undo_env):
    from lvjiang.core.profile.service import (
        profile_action,
        profile_read,
        undo_profile_history,
    )

    profile_action("u", "credits", delta=5, source="先攒分")
    profile_action("u", "credits", set_value=2, source="扫描到的真实值")
    record = _history("u", "stock", "credits")[0]
    assert record["change_type"] == "set"

    undo_profile_history(record["id"])

    assert profile_read("u", "credits") == 5
    latest = _history("u", "stock", "credits")[0]
    assert latest["source"] == "撤销：扫描到的真实值"


def test_undo_note_writes_back_old_text(undo_env):
    from lvjiang.core.profile.service import (
        profile_action,
        profile_read,
        undo_profile_history,
    )

    profile_action("u", "memo", set_value="旧内容", source="第一次")
    profile_action("u", "memo", set_value="新内容", source="第二次")
    record = _history("u", "note", "memo")[0]

    undo_profile_history(record["id"])

    assert profile_read("u", "memo") == "旧内容"
    latest = _history("u", "note", "memo")[0]
    assert latest["change_type"] == "set"
    assert latest["source"] == "撤销：第二次"


def test_undo_refuses_stale_record(undo_env):
    from lvjiang.core.profile.service import (
        ProfileOperationRefused,
        profile_action,
        undo_profile_history,
    )

    profile_action("u", "credits", delta=5, source="第一笔")
    record = _history("u", "stock", "credits")[0]
    profile_action("u", "credits", delta=-1, source="第二笔")

    with pytest.raises(ProfileOperationRefused):
        undo_profile_history(record["id"])
