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
    store.install(manifest, archive,
                  activate=lambda path, changed: seen.append((path / "app.yaml").read_bytes()))
    assert seen == [b"delay: 2"]
    assert (tmp_path / "system/new/.future").read_bytes() == b"\xff\x00"
    assert (tmp_path / "local/app.yaml").read_bytes() == b"delay: 9"
    assert unpack((tmp_path / "updates/v0.15.0/r0.zip").read_bytes()) == base
    assert (tmp_path / f"updates/v0.15.0/public/r5/{digest(json_bytes(manifest))}/update.zip").read_bytes() == archive
    # 下一修订撤回全部覆盖，无需反向补丁链。
    manifest, archive = make_manifest("0.15.0", 6, base, {})
    store.install(manifest, archive, activate=lambda *args: None)
    assert (tmp_path / "system/app.yaml").read_bytes() == base["app.yaml"]
    assert not (tmp_path / "system/new/.future").exists()


def test_activation_failure_does_not_revert_committed_installation(tmp_path):
    base = {"app.yaml": b"old"}
    write_files(tmp_path / "system", base)
    store = UpdateStore(tmp_path, "0.15.0", tree_digest(base))
    manifest, archive = make_manifest("0.15.0", 1, base, {"app.yaml": b"new"})
    activated = []

    def activate(path, changed):
        activated.append((path / "app.yaml").read_bytes())
        if activated[-1] == b"new":
            raise RuntimeError("模拟内存切换失败")

    store.install(manifest, archive, activate=activate)
    assert activated == [b"new"]
    assert (tmp_path / "system/app.yaml").read_bytes() == b"new"
    assert store._state()["revision"] == 1
    assert not (tmp_path / "updates/transaction.json").exists()


def test_same_revision_is_untouched_and_archive_is_content_addressed(tmp_path):
    """修订号相同就不动；归档目录以完整清单摘要命名。"""
    base = {"app.yaml": b"old"}
    write_files(tmp_path / "system", base)
    store = UpdateStore(tmp_path, "0.15.0", tree_digest(base))
    first, first_archive = make_manifest("0.15.0", 1, base, {"app.yaml": b"new"})
    store.install(first, first_archive, activate=lambda *args: None)
    archive_path = store.root / "public/r1" / digest(json_bytes(first)) / "update.zip"
    assert archive_path.read_bytes() == first_archive
    # 同修订的不同内容不重装，也不写第二份归档。
    second, second_archive = make_manifest("0.15.0", 1, base, {"app.yaml": b"other"})
    assert store.install(second, second_archive, activate=lambda *args: None) == ()
    assert (tmp_path / "system/app.yaml").read_bytes() == b"new"
    assert not (store.root / "public/r1" / digest(json_bytes(second))).exists()
    store.prune_archives()
    assert archive_path.is_file()
    member_manifest = json_bytes({"key_id": "example"})
    encrypted = b"encrypted package"
    metadata = {"package_version": 3}
    store.cache_member(metadata, member_manifest, encrypted)
    member_dir = store.root / "member/r3" / digest(member_manifest)
    assert member_dir.joinpath("manifest.json").read_bytes() == member_manifest
    assert member_dir.joinpath("package.bin").read_bytes() == encrypted


def test_published_baseline_mismatch_does_not_block_install(tmp_path):
    """发布方基于另一份预置打的包，仍可叠加到客户端当前 system。"""
    client = {"app.yaml": b"client", "workflows/local.wf": b"wait 0.1\n"}
    write_files(tmp_path / "system", client)
    store = UpdateStore(tmp_path, "0.15.0", tree_digest(client))
    published, archive = make_manifest("0.15.0", 3, {"app.yaml": b"published"},
                                       {"workflows/added.wf": b"wait 0.2\n"})
    assert published["baseline_sha256"] != tree_digest(client)
    store.install(published, archive, activate=lambda *args: None)
    assert (tmp_path / "system/workflows/added.wf").read_bytes() == b"wait 0.2\n"
    # 补丁未提及的文件保持客户端版本，不要求两端预置一致。
    assert (tmp_path / "system/app.yaml").read_bytes() == b"client"
    assert (tmp_path / "system/workflows/local.wf").read_bytes() == b"wait 0.1\n"


def test_untrusted_baseline_and_wrong_series_are_rejected(tmp_path):
    base = {"app.yaml": b"official"}
    write_files(tmp_path / "system", {"app.yaml": b"modified"})
    store = UpdateStore(tmp_path, "0.15.0", tree_digest(base))
    with pytest.raises(ValueError, match="原始预置"):
        store.baseline()
    assert not (tmp_path / "updates/v0.15.0/r0.zip").exists()
    manifest, archive = make_manifest("0.15.1", 1, base, {})
    with pytest.raises(ValueError, match="系列"):
        store.install(manifest, archive, activate=lambda *args: None)
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
    (tmp_path / "session").mkdir(exist_ok=True)
    (tmp_path / "session/baseline.json").write_text(json.dumps({
        "app_series": "0.15.0", "sha256": tree_digest(base)}), encoding="utf-8")
    monkeypatch.setattr(resolver, "_resolver", resolver.ConfigResolver(
        system_dir=tmp_path / "system", local_dir=tmp_path / "local", dev_mode=False, config_dir=tmp_path))
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
        store.install(manifest, archive, activate=lambda *args: None)
        assert frozen.resolve_read("templates/example.png").read_bytes() == b"old image"
        assert frozen.resolve_read("workflows/subcall/task.wf").read_bytes() == b"old"
        # 批量外层已有快照，内层引擎仍须用其重新构造布局与重定位源脚本。
        assert engine.run(tmp_path / "system/workflows/subcall/task.wf") == (b"old image", b"old")
        assert engine._layout.content == b"original"
    # 本进程保留当前代次，后续相同修订任务复用；旧运行仍持有自己的代次。
    assert frozen.system_dir.exists()
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
                  activate=lambda *args: None)
    public = {"scenes.yaml": base["scenes.yaml"].replace(b"[base]", b"[base, new]")}
    manifest, archive = make_manifest("0.15.0", 2, base, public)
    store.install(manifest, archive, activate=lambda *args: None)
    import yaml
    data = yaml.safe_load((tmp_path / "system/scenes.yaml").read_text(encoding="utf-8"))
    assert data["scenes"]["basic"]["items"] == ["base", "new", "member"]


def test_member_install_and_withdrawal_ignore_public_revision_dependency(tmp_path, monkeypatch):
    """会员包按授权安装与撤回，不因发布方修订序号拒绝。"""
    monkeypatch.setattr("lvjiang.core.update.get_version", lambda: "0.15.0")
    base = {"base.bin": b"base"}
    write_files(tmp_path / "system", base)
    store = UpdateStore(tmp_path, "0.15.0", tree_digest(base))
    member = {"member.bin": b"member"}
    metadata = {"config_version": 4, "public_revisions": {"0.15.0": 2}}
    store.install(member_files=member, member_metadata=metadata,
                  activate=lambda *a: None)
    assert (tmp_path / "system/member.bin").read_bytes() == b"member"
    manifest, archive = make_manifest("0.15.0", 2, base, {})
    store.install(manifest, archive, member_files=member, member_metadata=metadata,
                  activate=lambda *a: None)
    store.install(member_files={}, member_metadata={"config_version": 3},
                  activate=lambda *a: None)
    assert not (tmp_path / "system/member.bin").exists()
    store.install(member_files=member, member_metadata={**metadata, "config_version": 3},
                  activate=lambda *a: None)
    assert (tmp_path / "system/member.bin").read_bytes() == b"member"
    from lvjiang.core.config import protected_remote, resolver
    from lvjiang.core.config.runtime_snapshot import configuration_snapshot
    (tmp_path / "session").mkdir(exist_ok=True)
    (tmp_path / "session/baseline.json").write_text(json.dumps({
        "app_series": "0.15.0", "sha256": tree_digest(base)}), encoding="utf-8")
    monkeypatch.setattr(resolver, "_resolver", resolver.ConfigResolver(
        system_dir=tmp_path / "system", local_dir=tmp_path / "local", dev_mode=False, config_dir=tmp_path))
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
        store.install(manifest, archive, activate=lambda *a: None)
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
    store.install(reset_public=True, activate=lambda *a: None)
    assert (tmp_path / "system/base.bin").read_bytes() == b"base"
    assert store._state()["revision"] == 0
    store.prune_archives()
    assert (store.root / "r0.zip").exists()
    # 撤回后只保留可回滚的上一选择，更早的同修订归档被回收。
    assert (store.root / f"public/r2/{digest(json_bytes(new))}").is_dir()
    assert not (store.root / f"public/r2/{digest(json_bytes(old))}").exists()


def test_manifest_rejects_unsafe_deletion_path():
    base = {"base.bin": b"base"}
    with pytest.raises(ValueError):
        make_manifest("0.15.0", 1, base, base, delete_files=("../local/file",))


def test_member_withdrawal_resets_member_revision(tmp_path):
    """撤回会员必须同时把会员修订归零，否则之后会被当成「远端未变」永不拉取。"""
    base = {"base.bin": b"base"}
    write_files(tmp_path / "system", base)
    store = UpdateStore(tmp_path, "0.15.0", tree_digest(base))
    store.install(member_files={"member.bin": b"member"},
                  member_metadata={"config_version": 3}, activate=lambda *a: None)
    assert store._state()["member_index_revision"] == 3
    store.install(member_files={}, member_metadata={}, activate=lambda *a: None)
    assert store._state()["member_index_revision"] == 0
    assert not (tmp_path / "system/member.bin").exists()


def test_member_update_replays_recorded_public_revision(tmp_path):
    """公开修订未变、只有会员变化时，重放上一版公开层再叠加会员。"""
    base = {"base.bin": b"base"}
    write_files(tmp_path / "system", base)
    store = UpdateStore(tmp_path, "0.15.0", tree_digest(base))
    manifest, archive = make_manifest("0.15.0", 3, base, {"base.bin": b"public3"})
    store.install(manifest, archive, activate=lambda *a: None)
    store.install(member_files={"member.bin": b"m1"},
                  member_metadata={"config_version": 1}, activate=lambda *a: None)
    store.install(member_files={"member.bin": b"m2"},
                  member_metadata={"config_version": 2}, activate=lambda *a: None)
    assert (tmp_path / "system/base.bin").read_bytes() == b"public3"
    assert (tmp_path / "system/member.bin").read_bytes() == b"m2"
    assert store._state()["revision"] == 3
    assert store._state()["member_index_revision"] == 2


def test_missing_public_archive_degrades_to_r0(tmp_path):
    """归档被清理后仍能只装会员，不因缺文件整轮失败。"""
    base = {"base.bin": b"base"}
    write_files(tmp_path / "system", base)
    store = UpdateStore(tmp_path, "0.15.0", tree_digest(base))
    manifest, archive = make_manifest("0.15.0", 3, base, {"base.bin": b"public3"})
    store.install(manifest, archive, activate=lambda *a: None)
    for item in (store.root / "public/r3").iterdir():
        (item / "update.zip").unlink()
    store.install(member_files={"member.bin": b"member"},
                  member_metadata={"config_version": 2}, activate=lambda *a: None)
    assert (tmp_path / "system/base.bin").read_bytes() == b"base"
    assert (tmp_path / "system/member.bin").read_bytes() == b"member"
    assert store._state()["revision"] == 0
