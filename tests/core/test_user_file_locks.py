from lvjiang.core.user_file_locks import collect_legacy_user_file_locks


def test_collect_legacy_user_file_locks(tmp_path):
    metadata = tmp_path / "alice.json.lock"
    session = tmp_path / "alice.session.json.lock"
    metadata.touch()
    session.touch()

    assert collect_legacy_user_file_locks(tmp_path) == 2
    assert not metadata.exists()
    assert not session.exists()
    assert (tmp_path / ".lock/alice.json.lock").exists()
    assert (tmp_path / ".lock/alice.session.json.lock").exists()


def test_collect_does_not_replace_existing_destination(tmp_path):
    source = tmp_path / "alice.json.lock"
    target = tmp_path / ".lock/alice.json.lock"
    target.parent.mkdir()
    source.write_text("legacy", encoding="utf-8")
    target.write_text("current", encoding="utf-8")

    assert collect_legacy_user_file_locks(tmp_path) == 0
    assert source.read_text(encoding="utf-8") == "legacy"
    assert target.read_text(encoding="utf-8") == "current"
