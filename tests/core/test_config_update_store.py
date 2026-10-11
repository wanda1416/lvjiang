"""保护在线修正不污染用户配置、坏包不破坏可用预置的持续契约。"""
import io
import json
import zipfile

import pytest

from lvjiang.core.config.update_store import UpdateStore
from lvjiang.core.update_bundle import (
    digest,
    json_bytes,
    make_manifest,
    pack,
    series,
    tree_digest,
    unpack,
)


def write_files(root, files):
    for name, data in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)


def test_cumulative_unknown_files_and_local_survive(tmp_path):
    base = {"app.yaml": b"delay: 1", "workflows/test.wf": b"old"}
    write_files(tmp_path / "system", base)
    write_files(tmp_path / "local", {"app.yaml": b"delay: 9"})
    store = UpdateStore(tmp_path, "0.15.0.2", tree_digest(base))
    update = {"app.yaml": b"delay: 2", "new/.future": b"\xff\x00"}
    manifest, archive = make_manifest("0.15.0", 5, base, update)
    seen = []
    store.install(manifest, archive, validate=lambda path: None,
                  activate=lambda path, changed: seen.append((path / "app.yaml").read_bytes()))
    assert seen == [b"delay: 2"]
    assert (tmp_path / "system/new/.future").read_bytes() == b"\xff\x00"
    assert (tmp_path / "local/app.yaml").read_bytes() == b"delay: 9"
    assert unpack((tmp_path / "updates/v0.15.0/r0.zip").read_bytes()) == base
    assert (tmp_path / f"updates/v0.15.0/public/r5/{digest(json_bytes(manifest))}/update.zip").read_bytes() == archive
    # 下一修订撤回全部覆盖，无需反向补丁链。
    manifest, archive = make_manifest("0.15.0", 6, base, {})
    store.install(manifest, archive, validate=lambda path: None, activate=lambda *args: None)
    assert (tmp_path / "system/app.yaml").read_bytes() == base["app.yaml"]
    assert not (tmp_path / "system/new/.future").exists()


def test_activation_failure_restores_disk_and_memory(tmp_path):
    base = {"app.yaml": b"old"}
    write_files(tmp_path / "system", base)
    store = UpdateStore(tmp_path, "0.15.0", tree_digest(base))
    manifest, archive = make_manifest("0.15.0", 1, base, {"app.yaml": b"new"})
    activated = []

    def activate(path, changed):
        activated.append((path / "app.yaml").read_bytes())
        if activated[-1] == b"new":
            raise RuntimeError("模拟内存切换失败")

    with pytest.raises(RuntimeError, match="内存切换失败"):
        store.install(manifest, archive, validate=lambda path: None, activate=activate)
    assert activated == [b"new", b"old"]
    assert (tmp_path / "system/app.yaml").read_bytes() == b"old"
    assert not (tmp_path / "updates/transaction.json").exists()


def test_untrusted_baseline_and_wrong_series_are_rejected(tmp_path):
    base = {"app.yaml": b"official"}
    write_files(tmp_path / "system", {"app.yaml": b"modified"})
    store = UpdateStore(tmp_path, "0.15.0", tree_digest(base))
    with pytest.raises(ValueError, match="原始预置"):
        store.baseline()
    assert not (tmp_path / "updates/v0.15.0/r0.zip").exists()
    manifest, archive = make_manifest("0.15.1", 1, base, {})
    with pytest.raises(ValueError, match="系列"):
        store.install(manifest, archive, validate=lambda path: None, activate=lambda *args: None)
    assert series("0.15.0.3") == "0.15.0"
    assert series("0.15.0rc1") == "0.15.0"


def test_archive_rejects_escape_and_case_collisions():
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr("../local/app.yaml", "bad")
    with pytest.raises(ValueError, match="路径"):
        unpack(stream.getvalue())
    with pytest.raises(ValueError, match="冲突"):
        pack({"a": b"1", "A/b": b"2"})


def test_recovery_rolls_back_interrupted_swap(tmp_path):
    base = {"app.yaml": b"old"}
    token = "a" * 32
    write_files(tmp_path / "system", {"app.yaml": b"new"})
    write_files(tmp_path / f"updates/previous-{token}", base)
    journal = {"token": token, "app_series": "0.15.0", "previous_state": {"revision": 1}}
    (tmp_path / "updates/transaction.json").write_text(json.dumps(journal), encoding="utf-8")
    store = UpdateStore(tmp_path, "0.15.0", tree_digest(base))
    store.recover()
    assert (tmp_path / "system/app.yaml").read_bytes() == b"old"
    assert json.loads((tmp_path / "updates/v0.15.0/state.json").read_text(encoding="utf-8"))["revision"] == 1


def test_prerelease_baseline_and_recovery_do_not_touch_final_archives(tmp_path):
    beta = {"app.yaml": b"beta"}
    final = {"app.yaml": b"final"}
    write_files(tmp_path / "system", beta)
    beta_store = UpdateStore(tmp_path, "0.15.0b1", tree_digest(beta))
    beta_store.baseline()
    write_files(tmp_path / "system", final)
    final_store = UpdateStore(tmp_path, "0.15.0", tree_digest(final))
    final_store.baseline()
    assert unpack((beta_store.root / "r0.zip").read_bytes()) == beta
    assert unpack((final_store.root / "r0.zip").read_bytes()) == final
    token = "b" * 32
    write_files(tmp_path / f"updates/previous-{token}", beta)
    journal = {"token": token, "app_series": "0.15.0", "app_version": "0.15.0b1",
               "previous_state": {"revision": 1}}
    (tmp_path / "updates/transaction.json").write_bytes(json_bytes(journal))
    final_store.recover()
    assert json.loads(beta_store.state_path.read_bytes())["revision"] == 1
    assert not final_store.state_path.exists()


def test_task_snapshot_keeps_lazy_files_while_next_task_sees_update(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from lvjiang.core import layout_manager
    from lvjiang.core.config import resolver
    from lvjiang.core.config.runtime_snapshot import (
        configuration_snapshot,
        task_configuration,
    )

    base = {"workflows/subcall/task.wf": b"old", "templates/example.png": b"old image"}
    write_files(tmp_path / "system", base)
    (tmp_path / "local").mkdir()
    (tmp_path / "system-baseline.json").write_text(json.dumps({
        "app_series": "0.15.0", "sha256": tree_digest(base)}), encoding="utf-8")
    monkeypatch.setattr(resolver, "_resolver", resolver.ConfigResolver(
        system_dir=tmp_path / "system", local_dir=tmp_path / "local", dev_mode=False))
    store = UpdateStore(tmp_path, "0.15.0", tree_digest(base))
    manifest, archive = make_manifest("0.15.0", 1, base, {"templates/example.png": b"new image"})
    monkeypatch.setattr(layout_manager, "load_layout_by_key", lambda key: SimpleNamespace(
        key=key, content=resolver.get_resolver().resolve_read("templates/example.png").read_bytes()))
    class Engine:
        _layout = SimpleNamespace(key="test", content=b"original")
        _reference_recognizer = _workflow = _ocr = None

        @task_configuration
        def run(self, source):
            return self._layout.content, source.read_bytes()
    engine = Engine()
    with configuration_snapshot():
        frozen = resolver.get_resolver()
        store.install(manifest, archive, validate=lambda path: None, activate=lambda *args: None)
        assert frozen.resolve_read("templates/example.png").read_bytes() == b"old image"
        assert frozen.resolve_read("workflows/subcall/task.wf").read_bytes() == b"old"
        # 批量外层已有快照，内层引擎仍须用其重新构造布局与重定位源脚本。
        assert engine.run(tmp_path / "system/workflows/subcall/task.wf") == (b"old image", b"old")
        assert engine._layout.content == b"original"
    assert not frozen.system_dir.exists()
    with configuration_snapshot():
        assert resolver.get_resolver().resolve_read("templates/example.png").read_bytes() == b"new image"
        assert engine.run(tmp_path / "system/workflows/subcall/task.wf") == (b"new image", b"old")


def test_public_revision_preserves_member_registry_fragments(tmp_path):
    base = {"scenes.yaml": b"schema_version: 2\nscenes:\n  basic:\n    items: [base]\n"}
    write_files(tmp_path / "system", base)
    store = UpdateStore(tmp_path, "0.15.0", tree_digest(base))
    manifest, archive = make_manifest("0.15.0", 1, base, {})
    member = {"scenes.yaml": b"scenes:\n  basic:\n    items: [member]\n", "scenes/member.yaml": b"key: member"}
    store.install(manifest, archive, member_files=member,
                  validate=lambda path: None, activate=lambda *args: None)
    public = {"scenes.yaml": base["scenes.yaml"].replace(b"[base]", b"[base, new]")}
    manifest, archive = make_manifest("0.15.0", 2, base, public)
    store.install(manifest, archive, validate=lambda path: None, activate=lambda *args: None)
    import yaml
    data = yaml.safe_load((tmp_path / "system/scenes.yaml").read_text(encoding="utf-8"))
    assert data["scenes"]["basic"]["items"] == ["base", "new", "member"]


def test_member_dependency_and_authorization_survive_index_rollback(tmp_path, monkeypatch):
    base = {"base.bin": b"base"}
    write_files(tmp_path / "system", base)
    store = UpdateStore(tmp_path, "0.15.0", tree_digest(base))
    member = {"member.bin": b"member"}
    metadata = {"config_version": 4, "public_revisions": {"0.15.0": 2}}
    with pytest.raises(ValueError, match="至少 r2"):
        store.install(member_files=member, member_metadata=metadata,
                      validate=lambda p: None, activate=lambda *a: None)
    assert not (tmp_path / "system/member.bin").exists()
    manifest, archive = make_manifest("0.15.0", 2, base, {})
    store.install(manifest, archive, member_files=member, member_metadata=metadata,
                  validate=lambda p: None, activate=lambda *a: None)
    store.install(member_files={}, member_metadata={"config_version": 3},
                  validate=lambda p: None, activate=lambda *a: None)
    assert not (tmp_path / "system/member.bin").exists()
    store.install(member_files=member, member_metadata={**metadata, "config_version": 3},
                  validate=lambda p: None, activate=lambda *a: None)
    assert (tmp_path / "system/member.bin").read_bytes() == b"member"
    from lvjiang.core.config import protected_remote, resolver
    from lvjiang.core.config.runtime_snapshot import configuration_snapshot
    (tmp_path / "system-baseline.json").write_text(json.dumps({
        "app_series": "0.15.0", "sha256": tree_digest(base)}), encoding="utf-8")
    monkeypatch.setattr(resolver, "_resolver", resolver.ConfigResolver(
        system_dir=tmp_path / "system", local_dir=tmp_path / "local", dev_mode=False))
    monkeypatch.setattr(protected_remote, "authorized", lambda doc: False)
    with configuration_snapshot():
        assert resolver.get_resolver().resolve_read("member.bin") is None
        assert resolver.get_resolver().resolve_read("base.bin").read_bytes() == b"base"
    assert store._state()["revision"] == 2


def test_rollback_republish_and_manifest_deletion_rebuild_from_r0(tmp_path):
    base = {"base.bin": b"base", "old.bin": b"old"}
    write_files(tmp_path / "system", base)
    write_files(tmp_path / "local", {"old.bin": b"local"})
    store = UpdateStore(tmp_path, "0.15.0", tree_digest(base))
    def install(revision, files, deleted=()):
        manifest, archive = make_manifest("0.15.0", revision, base, files, delete_files=deleted)
        store.install(manifest, archive, validate=lambda p: None, activate=lambda *a: None)
        return manifest
    install(1, {"base.bin": b"first"})
    old = install(2, {"base.bin": b"second"}, ("old.bin",))
    assert not (tmp_path / "system/old.bin").exists()
    assert (tmp_path / "local/old.bin").read_bytes() == b"local"
    install(1, {"base.bin": b"first"})
    assert (tmp_path / "system/old.bin").read_bytes() == b"old"
    new = install(2, {"base.bin": b"replacement"})
    assert digest(json_bytes(old)) != digest(json_bytes(new))
    assert len(list((store.root / "public/r2").iterdir())) == 2
    store.install(reset_public=True, validate=lambda p: None, activate=lambda *a: None)
    assert (tmp_path / "system/base.bin").read_bytes() == b"base"
    assert store._state()["revision"] == 0
    store.prune_archives()
    assert (store.root / "r0.zip").exists()
    assert (store.root / f"public/r2/{digest(json_bytes(new))}").is_dir()
    assert not (store.root / f"public/r2/{digest(json_bytes(old))}").exists()


def test_manifest_rejects_unsafe_conflicting_deletion_and_member_wrong_digest(tmp_path):
    base = {"base.bin": b"base"}
    for deleted in (("../local/file",), ("base.bin",)):
        with pytest.raises(ValueError):
            make_manifest("0.15.0", 1, base, base, delete_files=deleted)
    write_files(tmp_path / "system", base)
    store = UpdateStore(tmp_path, "0.15.0", tree_digest(base))
    manifest, archive = make_manifest("0.15.0", 2, base, {})
    with pytest.raises(ValueError, match="摘要"):
        store.install(manifest, archive, member_files={"member.bin": b"member"},
                      member_metadata={"public_manifests": {"0.15.0": "0" * 64}},
                      validate=lambda p: None, activate=lambda *a: None)
    assert (tmp_path / "system/base.bin").read_bytes() == b"base"
    required = digest(json_bytes(manifest))
    metadata = {"public_revisions": {"0.15.0": 2}, "public_manifests": {"0.15.0": required}}
    member = {"member.bin": b"member"}
    store.install(manifest, archive, member_files=member, member_metadata=metadata,
                  validate=lambda p: None, activate=lambda *a: None)
    newer, newer_archive = make_manifest("0.15.0", 3, base, {}, ancestors={"2": required})
    store.install(newer, newer_archive, validate=lambda p: None, activate=lambda *a: None)
    assert (store.system / "member.bin").read_bytes() == b"member"
    replacement, replacement_archive = make_manifest("0.15.0", 2, base, {"base.bin": b"different"})
    with pytest.raises(ValueError, match="摘要"):
        store.install(replacement, replacement_archive, validate=lambda p: None, activate=lambda *a: None)
    assert store._state()["revision"] == 3
