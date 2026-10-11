"""业务配置不完整及单个刷新失败，不能阻止其它文件安装和刷新。"""

import os
import subprocess
import sys
from types import SimpleNamespace

import yaml

from lvjiang import apps
from lvjiang.core.config import protected_remote, remote, resolver
from lvjiang.core.config.member_download import MemberDownload
from lvjiang.core.config.update_store import UpdateStore
from lvjiang.core.maps import MapManager
from lvjiang.core.update_bundle import make_manifest, tree_digest
from lvjiang.workflows.grammar.parser.api import parse_text


def test_member_installs_without_business_selfcheck_or_global_refresh_failure(tmp_path, monkeypatch):
    baseline = {"workflows/existing.wf": b"wait 0.1\n"}
    system = tmp_path / "system"
    (system / "workflows").mkdir(parents=True)
    (system / "workflows/existing.wf").write_bytes(baseline["workflows/existing.wf"])
    local = tmp_path / "local"
    local.mkdir()
    config = resolver.ConfigResolver(system_dir=system, local_dir=local, dev_mode=False)
    monkeypatch.setattr(resolver, "_resolver", config)
    access = protected_remote.ProtectedAccess("https://example.invalid", "test-only-license")
    member = MemberDownload({
        "maps/example/map.yaml": b"key: example\nname: Example\nbase_image: base.png\nui:\n  scene: map_example\n",
        "workflows/new.wf": b"wait 0.2\n",
        "workflows/layout_specific.wf": b"click [missing_scene].[unbound]\n",
        "scenes/broken.yaml": b"[ invalid yaml",
        "example/settings.yaml": b"[ invalid settings",
    }, {"config_version": 1, "package_version": 1})
    monkeypatch.setattr(remote, "fetch_public_update", lambda *a, **kw: None)
    monkeypatch.setattr(remote, "fetch_member_update", lambda *a, **kw: member)
    monkeypatch.setattr(remote, "is_enabled", lambda: True)
    monkeypatch.setattr(protected_remote, "snapshot", lambda: access)
    monkeypatch.setattr(protected_remote, "authorized", lambda metadata: True)
    refreshed = []
    def broken(*args):
        raise ValueError("单个配置加载失败")
    config.add_batch_change_listener(broken)
    config.add_batch_change_listener(lambda changes: refreshed.append("listener"))
    monkeypatch.setattr(apps, "get_registered_app_ids", lambda: ("example",))
    monkeypatch.setattr(apps, "load_app", lambda app: SimpleNamespace(
        configuration_reload_callbacks=[broken, lambda: refreshed.append("plugin")]))
    job = remote.SyncJob(tmp_path, "0.15.0", tree_digest(baseline), protected=access)
    result = remote.run_sync(job)
    assert not (system / "workflows/new.wf").exists()
    remote.apply_outcome(result)
    assert refreshed == ["listener", "plugin"]
    for name, data in member.files.items():
        assert (system / name).read_bytes() == data
    assert parse_text((system / "workflows/new.wf").read_text(encoding="utf-8")) is not None
    assert parse_text((system / "workflows/layout_specific.wf").read_text(encoding="utf-8")) is not None
    maps = MapManager(config)
    assert maps.base_image_path(maps.load("example")) is None
    assert UpdateStore(tmp_path, "0.15.0", tree_digest(baseline))._state()["member_index_revision"] == 1
    assert not (tmp_path / "updates/transaction.json").exists()


def test_member_overlays_public_and_registry_keys_without_ownership_gate(tmp_path):
    system = tmp_path / "system"
    system.mkdir()
    baseline = {"shared.bin": b"r0", "scenes.yaml": b"scenes:\n  basic:\n    name: Base\n    items: [base]\n"}
    for name, data in baseline.items():
        (system / name).write_bytes(data)
    store = UpdateStore(tmp_path, "0.15.0", tree_digest(baseline))
    manifest, archive = make_manifest("0.15.0", 1, baseline, {"shared.bin": b"public"})
    member = {"shared.bin": b"member", "scenes.yaml": b"scenes:\n  basic:\n    name: Member\n    items: [member]\n"}
    store.install(manifest, archive, member_files=member, activate=lambda *_: None)
    assert (system / "shared.bin").read_bytes() == b"member"
    registry = yaml.safe_load((system / "scenes.yaml").read_text(encoding="utf-8"))
    assert registry["scenes"]["basic"] == {"name": "Member", "items": ["base", "member"]}
    # 登记表损坏也只保留该文件原文，不阻断其它内容叠加。
    member["scenes.yaml"] = b"[ invalid yaml"
    store.install(member_files=member, activate=lambda *_: None)
    assert (system / "scenes.yaml").read_bytes() == member["scenes.yaml"]
    assert (system / "shared.bin").read_bytes() == b"member"
    store.install(member_files={}, activate=lambda *_: None)
    assert (system / "shared.bin").read_bytes() == b"public"
    assert (system / "scenes.yaml").read_bytes() == baseline["scenes.yaml"]


def test_empty_scene_registry_does_not_abort_next_startup(tmp_path):
    system = tmp_path / "config/system"
    system.mkdir(parents=True)
    (system / "scenes.yaml").write_text("schema_version: 2\nscenes: {}\n", encoding="utf-8")
    env = {**os.environ, "LVJIANG_ROOT": str(tmp_path)}
    result = subprocess.run(
        [sys.executable, "-c", "from lvjiang.core.scene_registry import get_registry; assert not get_registry().get_groups()"],
        env=env, capture_output=True, text=True, encoding="utf-8", timeout=15,
    )
    assert result.returncode == 0, result.stderr
