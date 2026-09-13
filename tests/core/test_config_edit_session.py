"""隔离配置编辑会话不污染全局解析器，并通过公开 API 提交。"""

import yaml

from lvjiang.core.config import versioning
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


def test_remote_entity_superseding_system_is_edit_session_baseline(
        tmp_path, monkeypatch):
    rel_dir = "edit_session_remote_rules"
    rel_path = f"{rel_dir}/demo.yaml"
    monkeypatch.setitem(
        versioning.VERSIONED_DIRS,
        rel_dir,
        versioning.VersionedDir(
            rel_dir, "*.yaml", 1, allow_remote_new=True),
    )
    system = tmp_path / "system"
    local = tmp_path / "local"
    remote = tmp_path / "remote"
    for root, value, content_version in (
        (system, "system", 1),
        (remote, "remote", 2),
    ):
        path = root / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            f"content_version: {content_version}\nvalue: {value}\n",
            encoding="utf-8",
        )
    local.mkdir()
    source = ConfigResolver(
        system_dir=system,
        local_dir=local,
        remote_dir=remote,
        dev_mode=False,
    )

    session = ConfigEditSession(
        source, merged_paths=(), entity_dirs=(rel_dir,))

    effective = session.resolver.resolve_read(rel_path)
    assert effective is not None
    assert yaml.safe_load(effective.read_text(encoding="utf-8")) == {
        "content_version": 2,
        "value": "remote",
    }
    session.close()


def test_local_entity_still_wins_over_remote_edit_session_baseline(
        tmp_path, monkeypatch):
    rel_dir = "edit_session_local_rules"
    rel_path = f"{rel_dir}/demo.yaml"
    monkeypatch.setitem(
        versioning.VERSIONED_DIRS,
        rel_dir,
        versioning.VersionedDir(
            rel_dir, "*.yaml", 1, allow_remote_new=True),
    )
    system = tmp_path / "system"
    local = tmp_path / "local"
    remote = tmp_path / "remote"
    for root, value, content_version in (
        (system, "system", 1),
        (remote, "remote", 2),
        (local, "local", 1),
    ):
        path = root / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            f"content_version: {content_version}\nvalue: {value}\n",
            encoding="utf-8",
        )
    source = ConfigResolver(
        system_dir=system,
        local_dir=local,
        remote_dir=remote,
        dev_mode=False,
    )

    session = ConfigEditSession(
        source, merged_paths=(), entity_dirs=(rel_dir,))

    effective = session.resolver.resolve_read(rel_path)
    assert effective is not None
    assert yaml.safe_load(effective.read_text(encoding="utf-8"))["value"] == "local"
    session.close()


def test_ordinary_session_edit_does_not_copy_remote_version_to_system(
        tmp_path, monkeypatch):
    rel_dir = "edit_session_preserve_system_version"
    rel_path = f"{rel_dir}/demo.yaml"
    monkeypatch.setitem(
        versioning.VERSIONED_DIRS,
        rel_dir,
        versioning.VersionedDir(
            rel_dir, "*.yaml", 1, allow_remote_new=True),
    )
    system = tmp_path / "system"
    local = tmp_path / "local"
    remote = tmp_path / "remote"
    for root, value, content_version in (
        (system, "system", 2),
        (remote, "remote", 3),
    ):
        path = root / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            f"content_version: {content_version}\nvalue: {value}\n",
            encoding="utf-8",
        )
    local.mkdir()
    source = ConfigResolver(
        system_dir=system,
        local_dir=local,
        remote_dir=remote,
        dev_mode=True,
    )
    session = ConfigEditSession(
        source, merged_paths=(), entity_dirs=(rel_dir,))

    session.resolver.write_entity(
        rel_path, "content_version: 3\nvalue: edited\n")
    session.commit()

    assert versioning.read_version(system / rel_path) == 2
    assert yaml.safe_load((system / rel_path).read_text(
        encoding="utf-8"))["value"] == "edited"
    effective = source.resolve_read(rel_path)
    assert effective == remote / rel_path
    session.close()


def test_explicit_session_version_bump_can_supersede_remote(
        tmp_path, monkeypatch):
    rel_dir = "edit_session_explicit_bump"
    rel_path = f"{rel_dir}/demo.yaml"
    monkeypatch.setitem(
        versioning.VERSIONED_DIRS,
        rel_dir,
        versioning.VersionedDir(
            rel_dir, "*.yaml", 1, allow_remote_new=True),
    )
    system = tmp_path / "system"
    local = tmp_path / "local"
    remote = tmp_path / "remote"
    for root, value, content_version in (
        (system, "system", 2),
        (remote, "remote", 3),
    ):
        path = root / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            f"content_version: {content_version}\nvalue: {value}\n",
            encoding="utf-8",
        )
    local.mkdir()
    source = ConfigResolver(
        system_dir=system,
        local_dir=local,
        remote_dir=remote,
        dev_mode=True,
    )
    session = ConfigEditSession(
        source, merged_paths=(), entity_dirs=(rel_dir,))

    session.resolver.write_entity(
        rel_path,
        "content_version: 3\nvalue: edited\n",
        content_version=4,
    )
    session.commit()

    assert versioning.read_version(system / rel_path) == 4
    effective = source.resolve_read(rel_path)
    assert effective == system / rel_path
    assert yaml.safe_load(effective.read_text(encoding="utf-8"))["value"] == "edited"
    session.close()
