"""Per-user sticky-note repository tests."""

import json

import pytest

from lvjiang.core.user_notes import (
    MAX_NOTES,
    MAX_TEXT_LENGTH,
    UserNotesRepository,
)


@pytest.fixture
def repo(tmp_path):
    return UserNotesRepository("测试用户", users_dir=tmp_path)


def test_missing_file_loads_as_empty_without_creating_file(repo):
    assert repo.list_notes() == []
    assert not repo.path.exists()


def test_add_persists_newest_first_and_preserves_chinese(repo):
    first = repo.add("第一条")
    second = repo.add("第二条")

    reloaded = UserNotesRepository(repo.username, users_dir=repo.path.parent)
    notes = reloaded.list_notes()

    assert [note.id for note in notes] == [second.id, first.id]
    assert [note.text for note in notes] == ["第二条", "第一条"]
    data = json.loads(repo.path.read_text(encoding="utf-8"))
    assert data["schema_version"] == 1
    assert data["revision"] == 2


def test_update_keeps_position_and_created_time(repo):
    original = repo.add("原内容")
    other = repo.add("其他内容")

    updated = repo.update(original.id, "新内容")
    notes = repo.list_notes()

    assert [note.id for note in notes] == [other.id, original.id]
    assert updated.created_at == original.created_at
    assert updated.text == "新内容"


def test_delete_removes_only_requested_note(repo):
    keep = repo.add("保留")
    remove = repo.add("删除")

    repo.delete(remove.id)

    assert [note.id for note in repo.list_notes()] == [keep.id]


@pytest.mark.parametrize("text", ["", "   ", "\n\t"])
def test_blank_text_is_rejected_without_creating_file(repo, text):
    with pytest.raises(ValueError, match="不能为空"):
        repo.add(text)
    assert not repo.path.exists()


def test_too_long_text_is_rejected(repo):
    with pytest.raises(ValueError, match=str(MAX_TEXT_LENGTH)):
        repo.add("x" * (MAX_TEXT_LENGTH + 1))


def test_note_limit_is_enforced(repo):
    state = {
        "schema_version": 1,
        "revision": 1,
        "notes": [
            {
                "id": str(index),
                "text": "note",
                "created_at": "2026-09-01T00:00:00Z",
                "updated_at": "2026-09-01T00:00:00Z",
            }
            for index in range(MAX_NOTES)
        ],
    }
    repo.path.write_text(json.dumps(state), encoding="utf-8")

    with pytest.raises(ValueError, match=str(MAX_NOTES)):
        repo.add("超额")


def test_corrupt_file_is_not_overwritten(repo):
    repo.path.write_text("{broken", encoding="utf-8")

    with pytest.raises(ValueError, match="无法读取"):
        repo.add("新内容")

    assert repo.path.read_text(encoding="utf-8") == "{broken"


def test_delete_file_removes_all_note_content(repo):
    repo.add("待删除")

    repo.delete_file()

    assert not repo.path.exists()
    assert repo.list_notes() == []


def test_invalid_username_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="非法用户名"):
        UserNotesRepository("../escape", users_dir=tmp_path)
