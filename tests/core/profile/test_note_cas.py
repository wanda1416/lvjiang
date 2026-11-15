"""备注（note）走 CAS：冲突时不再静默覆盖。"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
import yaml

from lvjiang.core.profile.models import MODEL_NOTE
from lvjiang.core.profile.service import (
    EXPECT_ABSENT,
    ProfileWriteConflict,
    profile_action,
    profile_read,
)


@pytest.fixture
def note_env(tmp_path, monkeypatch):
    """隔离环境：profile.yaml 里声明一个 note key，DB 落临时目录。"""
    session_dir = tmp_path / "session"
    session_dir.mkdir()

    import lvjiang.core.profile.repository as profile_db
    import lvjiang.core.profile.schema as profile_config

    profile_config._config = None
    profile_config._PROFILE_PATH = session_dir / "profile.yaml"
    profile_config._PROFILE_PATH.write_text(
        yaml.dump(
            {"note": [{"key": "user_note", "label": "状态备注"}]},
            allow_unicode=True,
        ),
        encoding="utf-8",
    )

    profile_db._db = None
    profile_db._DB_PATH = session_dir / "profile.db"

    yield SimpleNamespace(username="tester", session_dir=session_dir)

    profile_config._config = None
    profile_db._db = None


def _entry():
    from lvjiang.core.profile.repository import db_read_entry

    return db_read_entry("tester", MODEL_NOTE, "user_note")


def test_note_cas_writes_when_snapshot_matches(note_env):
    profile_action("tester", "user_note", set_value="旧文本")
    snapshot = _entry()
    assert profile_action(
        "tester", "user_note", set_value="新文本",
        expected_entry=snapshot, use_cas=True,
    ) == "新文本"
    assert profile_read("tester", "user_note") == "新文本"

    from lvjiang.core.profile.repository import db_get_history

    rows = db_get_history("tester", MODEL_NOTE, "user_note")
    assert rows[0].value_text == "新文本" and rows[0].old_value_text == "旧文本"


def test_note_cas_rejects_when_text_changed_elsewhere(note_env):
    profile_action("tester", "user_note", set_value="旧文本")
    snapshot = _entry()
    # 另一个窗口先写：文本与 event_at 都变了
    profile_action("tester", "user_note", set_value="别人改的")
    with pytest.raises(ProfileWriteConflict):
        profile_action(
            "tester", "user_note", set_value="我改的",
            expected_entry=snapshot, use_cas=True,
        )
    assert profile_read("tester", "user_note") == "别人改的"


def test_note_cas_creates_absent_row(note_env):
    assert profile_action(
        "tester", "user_note", set_value="第一条",
        expected_entry=EXPECT_ABSENT, use_cas=True,
    ) == "第一条"
    assert profile_read("tester", "user_note") == "第一条"


def test_note_without_cas_still_overwrites(note_env):
    """没有快照的调用方（脚本/回放）保持原语义：直接写。"""
    profile_action("tester", "user_note", set_value="旧文本")
    assert profile_action("tester", "user_note", set_value="新文本") == "新文本"
    assert profile_read("tester", "user_note") == "新文本"


def test_note_unchanged_text_records_nothing(note_env):
    """写回同一段文本不产生账本行（CAS 快照一致时也一样）。"""
    from lvjiang.core.profile.repository import db_get_history

    profile_action("tester", "user_note", set_value="同一段")
    snapshot = _entry()
    before = len(db_get_history("tester", MODEL_NOTE, "user_note"))
    profile_action(
        "tester", "user_note", set_value="同一段",
        expected_entry=snapshot, use_cas=True,
    )
    assert len(db_get_history("tester", MODEL_NOTE, "user_note")) == before
