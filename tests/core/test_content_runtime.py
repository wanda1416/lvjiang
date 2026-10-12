"""两种模式的持续契约：修订复用、在线安装与下一任务加载。"""
import json

import pytest
import yaml

from lvjiang.core import scene_registry
from lvjiang.core.config import remote
from lvjiang.core.config import resolver as resolver_module
from lvjiang.core.config.content import load_content, rebuild_content
from lvjiang.core.config.resolver import ConfigResolver
from lvjiang.core.config.runtime_snapshot import configuration_snapshot
from lvjiang.core.layout_manager import load_layout_by_key
from lvjiang.core.system_preset import write_baseline_identity
from lvjiang.core.update_bundle import json_bytes, make_manifest


def project(tmp_path, monkeypatch, *, dev):
    system = tmp_path / "system"
    (system / "scenes").mkdir(parents=True)
    (system / "layouts/phone").mkdir(parents=True)
    (system / "scenes.yaml").write_text(
        "schema_version: 2\nscenes:\n  general:\n    name: 通用\n    items: [sample]\n", encoding="utf-8")
    (system / "layouts.yaml").write_text(
        "schema_version: 2\nlayouts:\n  phone:\n    name: 手机\n", encoding="utf-8")
    (system / "app.yaml").write_text("delay: 1\n", encoding="utf-8")
    (system / "scenes/sample.yaml").write_text(
        "content_version: 1\nkey: sample\nname: 示例\nregions: []\n", encoding="utf-8")
    (system / "layouts/phone/sample.json").write_text(
        '{"content_version":1,"regions":[]}', encoding="utf-8")
    resolver = ConfigResolver(system, tmp_path / "local", dev_mode=dev)
    monkeypatch.setattr(resolver_module, "_resolver", resolver)
    monkeypatch.setattr("lvjiang.core.update.get_version", lambda: "0.15.0")
    return resolver


@pytest.mark.parametrize("dev", [True, False], ids=["development", "release"])
def test_missing_index_rebuilds_and_unchanged_tasks_reuse_parsed_scenes(tmp_path, monkeypatch, dev):
    resolver = project(tmp_path, monkeypatch, dev=dev)
    parsed = []
    original = scene_registry.SceneRegistry._load_all
    def record(self, *args, **kwargs):
        parsed.append(True)
        return original(self, *args, **kwargs)
    monkeypatch.setattr(scene_registry.SceneRegistry, "_load_all", record)
    with configuration_snapshot() as first:
        assert scene_registry.get_registry().get_scene("sample") is not None
        assert first.update_at
    with configuration_snapshot() as second:
        assert scene_registry.get_registry().get_scene("sample") is not None
        assert second.generation is first.generation
    assert len(parsed) == 1
    assert not (tmp_path / "session/baseline.json").exists()
    # 根 YAML 无需改索引，下一任务也必须看到变化。
    (resolver.system_dir / "app.yaml").write_text("delay: 2\n", encoding="utf-8")
    with configuration_snapshot():
        assert resolver_module.get_resolver().load_merged("app.yaml")["delay"] == 2
    index = tmp_path / "session/content.json"
    data = json.loads(index.read_text(encoding="utf-8"))
    data["layouts"]["phone"].pop("sample")
    index.write_text(json.dumps(data), encoding="utf-8")
    assert load_content(resolver)["layouts"]["phone"]["sample"] == 1
    index.write_text("not json", encoding="utf-8")
    assert load_content(resolver)["scenes"]["sample"] == 1


def test_development_skips_online_install_but_refresh_loads_external_scene(tmp_path, monkeypatch):
    resolver = project(tmp_path, monkeypatch, dev=True)
    write_baseline_identity(resolver.system_dir, "0.15.0")
    monkeypatch.setattr(remote, "_fetch_bytes", lambda *_a, **_k: pytest.fail("开发版不应联网"))
    assert remote._installed_store() is None
    assert not remote.run_sync(remote.build_sync_job()).performed
    with configuration_snapshot():
        assert not scene_registry.get_registry().get_scene("sample").regions
        resolver.write_entity("scenes/sample.yaml",
                              "key: sample\nname: 示例\nregions:\n"
                              "  - key: button\n    name: 按钮\n    type: func\n")
        assert not scene_registry.get_registry().get_scene("sample").regions
    with configuration_snapshot():
        assert scene_registry.get_registry().get_scene("sample").regions[0].key == "button"
    # 手工编辑不维护修订号，主动刷新仍必须使缓存失效。
    path = resolver.system_dir / "scenes/sample.yaml"
    path.write_text(path.read_text(encoding="utf-8").replace("name: 示例", "name: 新名称"), encoding="utf-8")
    rebuild_content(resolver)
    with configuration_snapshot():
        assert scene_registry.get_registry().get_scene("sample").name == "新名称"


def test_task_entry_records_and_logs_content_timestamp(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from loguru import logger

    from lvjiang.core.config.runtime_snapshot import task_configuration
    resolver = project(tmp_path, monkeypatch, dev=True)
    class Engine:
        _layout = SimpleNamespace(key="phone")
        _ocr = _reference_recognizer = _workflow = None

        @task_configuration
        def run(self, source):
            return self.configuration_update_at
    messages = []
    sink = logger.add(lambda message: messages.append(message.record["message"]), level="INFO")
    try:
        engine = Engine()
        timestamp = engine.run(tmp_path / "task.wf")
        assert timestamp == load_content(resolver)["update_at"]
        assert any("content.json update_at=" + timestamp in message for message in messages)
    finally:
        logger.remove(sink)


def test_release_public_member_install_refreshes_next_task_not_running_task(tmp_path, monkeypatch):
    from lvjiang.core.config import protected_remote, update_activation
    from lvjiang.core.config.member_download import MemberDownload
    from lvjiang.core.config.update_download import PublicDownload
    from lvjiang.core.update_bundle import read_tree
    resolver = project(tmp_path, monkeypatch, dev=False)
    identity = write_baseline_identity(resolver.system_dir, "0.15.0")
    assert (tmp_path / "session/baseline.json").is_file()
    assert not (tmp_path / "system-baseline.json").exists()
    assert remote._installed_store() is not None
    public_files = {
        "scenes/sample.yaml": b"content_version: 1\nkey: sample\nname: public\nregions:\n  - key: button\n    name: button\n    type: func\n",
        "layouts/phone/sample.json": json_bytes({"content_version": 1, "regions": [
            {"key": "button", "x_ratio": 0.1, "y_ratio": 0.1, "w_ratio": 0.1, "h_ratio": 0.1}]}),
    }
    manifest, archive = make_manifest("0.15.0", 1, read_tree(resolver.system_dir), public_files)
    member = MemberDownload({
        "scenes.yaml": b"scenes:\n  general:\n    items: [member]\n",
        "scenes/member.yaml": b"content_version: 1\nkey: member\nname: member\nregions: []\n",
    }, {"config_version": 1})
    monkeypatch.setattr(remote, "is_enabled", lambda: True)
    monkeypatch.setattr(protected_remote, "snapshot", lambda: None)
    monkeypatch.setattr(protected_remote, "authorized", lambda _metadata: True)
    monkeypatch.setattr(remote, "fetch_public_update", lambda *_a, **_k: PublicDownload(json_bytes(manifest), archive))
    monkeypatch.setattr(remote, "fetch_member_update", lambda *_a, **_k: member)
    monkeypatch.setattr(update_activation, "activate_configuration", lambda *_a: None)
    job = remote.SyncJob(tmp_path, "0.15.0", identity["sha256"])
    with configuration_snapshot() as old:
        assert scene_registry.get_registry().get_scene("sample").name == "示例"
        result = remote.run_sync(job)
        assert not result.errors
        remote.apply_outcome(result)
        assert scene_registry.get_registry().get_scene("sample").name == "示例"
        assert scene_registry.get_registry().get_scene("member") is None
    with configuration_snapshot() as latest:
        assert latest.update_at != old.update_at
        assert scene_registry.get_registry().get_scene("sample").name == "public"
        assert scene_registry.get_registry().get_scene("member") is not None
        assert [r.key for r in load_layout_by_key("phone").regions["sample"]] == ["button"]
    assert yaml.safe_load((resolver.system_dir / "scenes.yaml").read_text(encoding="utf-8"))["scenes"]["general"]["items"] == ["sample", "member"]
