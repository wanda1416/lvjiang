"""隔离配置编辑会话不污染全局解析器，并通过公开 API 提交。"""

import yaml

from lvjiang.core.config import edit_session as edit_session_module
from lvjiang.core.config.edit_session import ConfigEditSession
from lvjiang.core.config.resolver import ConfigResolver


def test_edit_session_isolated_until_commit(tmp_path):
    system = tmp_path / "system"
    local = tmp_path / "local"
    (system / "demo/entities").mkdir(parents=True)
    local.mkdir()
    (system / "demo/config.yaml").write_text("value: 1\n", encoding="utf-8")
    (system / "demo/entities/old.yaml").write_text(
        "key: old\nvalue: 1\n", encoding="utf-8")
    source = ConfigResolver(
        system_dir=system, local_dir=local, dev_mode=True)

    session = ConfigEditSession(
        source,
        merged_paths=("demo/config.yaml",),
        entity_dirs=("demo/entities",),
    )
    draft = session.resolver
    draft.save_merged("demo/config.yaml", {"value": 2})
    draft.delete_entity("demo/entities/old.yaml")
    draft.write_entity("demo/entities/new.yaml", "key: new\nvalue: 2\n")

    assert source.load_merged("demo/config.yaml") == {"value": 1}
    assert source.resolve_read("demo/entities/old.yaml") is not None
    assert source.resolve_read("demo/entities/new.yaml") is None

    session.commit()

    assert source.load_merged("demo/config.yaml") == {"value": 2}
    assert source.resolve_read("demo/entities/old.yaml") is None
    new_path = source.resolve_read("demo/entities/new.yaml")
    assert new_path is not None
    assert yaml.safe_load(new_path.read_text(encoding="utf-8")) == {
        "key": "new", "value": 2}
    session.close()


def test_close_discards_staged_changes(tmp_path):
    system = tmp_path / "system"
    local = tmp_path / "local"
    system.mkdir()
    local.mkdir()
    (system / "config.yaml").write_text("value: 1\n", encoding="utf-8")
    source = ConfigResolver(
        system_dir=system, local_dir=local, dev_mode=True)
    session = ConfigEditSession(
        source, merged_paths=("config.yaml",), entity_dirs=())

    session.resolver.save_merged("config.yaml", {"value": 9})
    session.close()

    assert source.load_merged("config.yaml") == {"value": 1}


def test_save_draft_does_not_restore_old_official_defaults_or_other_editors_changes(tmp_path, monkeypatch):
    from lvjiang.core.config import conflicts
    monkeypatch.setattr(conflicts, "_handler", lambda _files: True)
    system = tmp_path / "system"
    local = tmp_path / "local"
    system.mkdir()
    local.mkdir()
    config = system / "config.yaml"
    config.write_text("owned: 1\nofficial: 1\nother: 1\n", encoding="utf-8")
    source = ConfigResolver(system_dir=system, local_dir=local, dev_mode=False)
    session = ConfigEditSession(source, merged_paths=("config.yaml",), entity_dirs=())
    session.resolver.save_merged("config.yaml", {"owned": 2, "official": 1, "other": 1})
    config.write_text("owned: 1\nofficial: 2\nother: 1\nadded: 3\n", encoding="utf-8")
    (local / "config.yaml").write_text("other: 9\n", encoding="utf-8")
    session.commit()
    assert source.load_merged("config.yaml") == {"owned": 2, "official": 2, "other": 9, "added": 3}
    assert source.load_local("config.yaml") == {"owned": 2, "other": 9}
    session.commit()  # 不变的旧草稿不能在再次保存时回滚新默认值。
    assert source.load_local("config.yaml") == {"owned": 2, "other": 9}
    session.close()


def test_dev_session_commits_new_entity_to_selected_local_layer(tmp_path):
    system = tmp_path / "system"
    local = tmp_path / "local"
    system.mkdir()
    local.mkdir()
    source = ConfigResolver(system_dir=system, local_dir=local, dev_mode=True)
    session = ConfigEditSession(
        source, merged_paths=(), entity_dirs=("demo/entities",))

    rel_path = "demo/entities/private.yaml"
    session.resolver.write_entity(
        rel_path, "key: private\n", layer="local")
    session.commit()

    assert (local / rel_path).read_text(encoding="utf-8") == "key: private\n"
    assert not (system / rel_path).exists()
    session.close()


def test_dev_session_commits_layer_move_even_when_content_is_unchanged(
        tmp_path):
    system = tmp_path / "system"
    local = tmp_path / "local"
    rel_path = "demo/entities/moved.yaml"
    target = system / rel_path
    target.parent.mkdir(parents=True)
    target.write_text("key: moved\n", encoding="utf-8")
    local.mkdir()
    source = ConfigResolver(system_dir=system, local_dir=local, dev_mode=True)
    session = ConfigEditSession(
        source, merged_paths=(), entity_dirs=("demo/entities",))

    session.resolver.write_entity(
        rel_path, "key: moved\n", layer="local")
    session.resolver.delete_entity(rel_path, layer="system")
    session.commit()

    assert not target.exists()
    assert (local / rel_path).read_text(encoding="utf-8") == "key: moved\n"
    assert source.describe_entity(rel_path).layer == "local"
    session.close()


def test_windows_session_commit_does_not_expand_crlf_twice(
        tmp_path, monkeypatch):
    """Windows 工作副本已有 CRLF，提交前必须还原为逻辑换行。"""
    system = tmp_path / "system"
    local = tmp_path / "local"
    entity = system / "demo/entities/item.yaml"
    entity.parent.mkdir(parents=True)
    local.mkdir()
    entity.write_bytes(b"key: item\r\nvalue: 1\r\n")
    source = ConfigResolver(
        system_dir=system, local_dir=local, dev_mode=True)
    session = ConfigEditSession(
        source, merged_paths=(), entity_dirs=("demo/entities",))
    draft = session.resolver.resolve_read("demo/entities/item.yaml")
    assert draft is not None
    draft.write_bytes(b"key: item\r\nvalue: 2\r\n")

    written: list[str] = []
    original_write = source.write_entity

    def capture_write(rel_path, data, **kwargs):
        written.append(data)
        return original_write(rel_path, data, **kwargs)

    monkeypatch.setattr(edit_session_module.os, "linesep", "\r\n")
    monkeypatch.setattr(source, "write_entity", capture_write)
    session.commit()

    assert written == ["key: item\nvalue: 2\n"]
    assert "\r\r\n" not in entity.read_bytes().decode("utf-8")
    session.close()


def test_reset_discards_changes_and_keeps_session_editable(tmp_path):
    system = tmp_path / "system"
    local = tmp_path / "local"
    system.mkdir()
    local.mkdir()
    (system / "config.yaml").write_text("value: 1\n", encoding="utf-8")
    source = ConfigResolver(
        system_dir=system, local_dir=local, dev_mode=True)
    session = ConfigEditSession(
        source, merged_paths=("config.yaml",), entity_dirs=())

    session.resolver.save_merged("config.yaml", {"value": 9})
    session.reset()
    assert session.resolver.load_merged("config.yaml") == {"value": 1}

    session.resolver.save_merged("config.yaml", {"value": 2})
    session.commit()
    assert source.load_merged("config.yaml") == {"value": 2}
    session.close()
