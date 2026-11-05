"""配置索引重建与外部改动的刷新契约。

任务不再复制配置副本：任务直接读磁盘，配置改动在下一个任务生效。
"""
import json

import pytest

from lvjiang.core import scene_registry
from lvjiang.core.config import remote
from lvjiang.core.config import resolver as resolver_module
from lvjiang.core.config.content import rebuild_content
from lvjiang.core.config.resolver import ConfigResolver
from lvjiang.core.system_preset import write_baseline_identity


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
    resolver = ConfigResolver(system, tmp_path / "local", dev_mode=dev, config_dir=tmp_path)
    monkeypatch.setattr(resolver_module, "_resolver", resolver)
    monkeypatch.setattr("lvjiang.core.update.get_version", lambda: "0.15.0")
    return resolver


def isolated_registry(monkeypatch):
    """用 tmp 配置另建一个注册表实例，绝不改写会话级全局单例。

    全局单例由 conftest 按真实配置初始化，被用例改掉会让后续布局/场景校验
    对着错误数据跑（初始化会重建模块级缓存，所以连缓存重建一起屏蔽）。
    """
    monkeypatch.setattr(scene_registry, "_registry", None)
    monkeypatch.setattr(scene_registry, "_rebuild_scene_globals", lambda: None)
    scene_registry.initialize_scene_registry()
    return scene_registry.get_registry()


def test_missing_or_damaged_index_rebuilds_from_disk(tmp_path, monkeypatch):
    resolver = project(tmp_path, monkeypatch, dev=True)
    index = tmp_path / "session/content.json"
    assert not index.exists()
    data = rebuild_content(resolver)
    assert data["scenes"]["sample"] == 1
    assert data["layouts"]["phone"]["sample"] == 1
    assert json.loads(index.read_text(encoding="utf-8"))["update_at"] == data["update_at"]
    # 索引被手工改坏后重建必须补齐条目，而不是沿用旧内容。
    index.write_text("not json", encoding="utf-8")
    assert rebuild_content(resolver)["scenes"]["sample"] == 1


def test_scene_registry_sees_writes_and_external_edits(tmp_path, monkeypatch):
    resolver = project(tmp_path, monkeypatch, dev=True)
    isolated_registry(monkeypatch)
    assert not scene_registry.get_registry().get_scene("sample").regions
    # 走写入通道：修订自增；界面侧由 root resolver 的监听器触发原位重载。
    resolver.write_entity("scenes/sample.yaml",
                          "key: sample\nname: 示例\nregions:\n"
                          "  - key: button\n    name: 按钮\n    type: func\n")
    scene_registry.reload_scene_registry()
    assert scene_registry.get_registry().get_scene("sample").regions[0].key == "button"
    # 手工编辑不维护修订号，主动刷新仍必须使缓存失效。
    path = resolver.system_dir / "scenes/sample.yaml"
    path.write_text(path.read_text(encoding="utf-8").replace("name: 示例", "name: 新名称"), encoding="utf-8")
    rebuild_content(resolver)
    scene_registry.reload_scene_registry()
    assert scene_registry.get_registry().get_scene("sample").name == "新名称"


@pytest.mark.parametrize("identity", ['{"app_series":"0.15.0"}', '{"sha256":', '[]'])
def test_damaged_baseline_skips_online_restore_not_task_loading(tmp_path, monkeypatch, identity):
    resolver = project(tmp_path, monkeypatch, dev=False)
    session = tmp_path / "session"
    session.mkdir(exist_ok=True)
    (session / "baseline.json").write_text(identity, encoding="utf-8")
    monkeypatch.setattr(remote, "_fetch_bytes", lambda *_a, **_k: pytest.fail("损坏基底不应联网"))
    isolated_registry(monkeypatch)
    assert scene_registry.get_registry().get_scene("sample") is not None
    assert not remote.run_sync(remote.build_sync_job()).performed
    assert (resolver.system_dir / "scenes/sample.yaml").is_file()


def test_release_baseline_identity_written_without_extra_cloud_state(tmp_path, monkeypatch):
    resolver = project(tmp_path, monkeypatch, dev=False)
    write_baseline_identity(resolver.system_dir, "0.15.0", config_dir=resolver.config_dir)
    assert (tmp_path / "session/baseline.json").is_file()
    assert not (tmp_path / "system-baseline.json").exists()
    assert remote._installed_store() is not None
