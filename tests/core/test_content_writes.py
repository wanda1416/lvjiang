"""修订自动维护及配置编辑冲突不静默覆盖。"""
import json

import pytest
import yaml

from lvjiang.core.config import conflicts
from lvjiang.core.config.content import load_content
from lvjiang.core.config.edit_session import ConfigEditSession
from lvjiang.core.config.resolver import ConfigResolver


def test_actual_edit_increments_revision_but_noop_does_not(tmp_path):
    resolver = ConfigResolver(tmp_path / "system", tmp_path / "local", dev_mode=True)
    first = resolver.write_entity("scenes/sample.yaml", "key: sample\nname: 示例\n")
    assert yaml.safe_load(first.read_text(encoding="utf-8"))["content_version"] == 1
    old = load_content(resolver)
    resolver.write_entity("scenes/sample.yaml", "key: sample\nname: 示例\n")
    assert load_content(resolver) == old
    resolver.write_entity("scenes/sample.yaml", "key: sample\nname: 修改\n")
    assert load_content(resolver)["scenes"]["sample"] == 2
    data = json.loads((tmp_path / "session/content.json").read_text(encoding="utf-8"))
    assert data["update_at"] != old["update_at"]
    assert first.read_text(encoding="utf-8").startswith("content_version: 2")


def test_edit_conflict_cancel_keeps_disk_and_confirm_saves_draft(tmp_path, monkeypatch):
    resolver = ConfigResolver(tmp_path / "system", tmp_path / "local", dev_mode=True)
    path = resolver.write_entity("scenes/sample.yaml", "key: sample\nname: 示例\n")
    session = ConfigEditSession(resolver, merged_paths=(), entity_dirs=("scenes",))
    try:
        session.resolver.write_entity("scenes/sample.yaml", "key: sample\nname: 草稿\n")
        # 同号手工修改也必须检出，不能只依赖整数。
        path.write_text("content_version: 1\nkey: sample\nname: 外部修改\n", encoding="utf-8")
        monkeypatch.setattr(conflicts, "_handler", lambda _files: False)
        with pytest.raises(conflicts.ConfigWriteConflict):
            session.commit()
        assert "外部修改" in path.read_text(encoding="utf-8")
        monkeypatch.setattr(conflicts, "_handler", lambda _files: True)
        session.commit()
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert data["name"] == "草稿"
        assert data["content_version"] == 2
    finally:
        session.close()


def test_cancelled_immediate_scene_edit_does_not_leak_into_registry(tmp_path, monkeypatch):
    from lvjiang.core.scene_definition import SceneRegistry
    resolver = ConfigResolver(tmp_path / "system", tmp_path / "local", dev_mode=True)
    path = resolver.write_entity("scenes/sample.yaml", "key: sample\nname: 示例\n")
    registry = SceneRegistry(resolver=resolver)
    path.write_text("content_version: 1\nkey: sample\nname: 外部修改\n", encoding="utf-8")
    monkeypatch.setattr(conflicts, "_handler", lambda _files: False)
    with pytest.raises(conflicts.ConfigWriteConflict):
        registry.rename_scene("sample", "sample", "未保存的名称")
    assert registry.get_scene("sample").name == "外部修改"
    assert "未保存的名称" not in path.read_text(encoding="utf-8")


def test_new_layout_keeps_own_baselines_and_batches_multi_file_save(tmp_path, monkeypatch):
    from unittest.mock import Mock

    from lvjiang.core import layout_manager, scene_registry
    from lvjiang.core.config import content
    from lvjiang.core.config import resolver as resolver_module

    resolver = ConfigResolver(config_dir=tmp_path, dev_mode=True)
    monkeypatch.setattr(resolver_module, "_resolver", resolver)
    monkeypatch.setattr(scene_registry, "SCENE_REGIONS", {"first": [], "second": []})
    rebuild = Mock(wraps=content.content_changed)
    monkeypatch.setattr(content, "content_changed", rebuild)
    manager = layout_manager.LayoutConfigManager()
    layout = manager.new_layout("sample", "示例", activate=False)
    assert rebuild.call_count == 1
    assert set(layout._file_baselines) == {
        "layouts.yaml", "layouts/sample/first.json", "layouts/sample/second.json"}
    # 另一个读取上下文重置全局基线，不能撤掉此对象的保存保护。
    resolver.reset_edit_baselines()
    path = resolver.system_dir / "layouts.yaml"
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    doc["layouts"]["sample"]["canvas"]["w_ratio"] = 0.5
    path.write_text(yaml.safe_dump(doc), encoding="utf-8")
    monkeypatch.setattr(conflicts, "_handler", lambda _files: False)
    assert not manager.save_layout(layout)
    assert "磁盘配置已更新" in manager.last_save_error
    assert yaml.safe_load(path.read_text(encoding="utf-8"))["layouts"]["sample"]["canvas"]["w_ratio"] == 0.5
    monkeypatch.setattr(conflicts, "_handler", lambda _files: True)
    assert manager.save_layout(layout)
    monkeypatch.setattr(conflicts, "_handler", lambda _files: pytest.fail("确认保存后应更新基线"))
    assert manager.save_layout(layout)


def test_failed_nested_write_batch_flushes_saved_changes_once(tmp_path, monkeypatch):
    from unittest.mock import Mock

    from lvjiang.core.config import content
    resolver = ConfigResolver(config_dir=tmp_path, dev_mode=True)
    rebuild = Mock(wraps=content.content_changed)
    monkeypatch.setattr(content, "content_changed", rebuild)
    with pytest.raises(RuntimeError, match="partial failure"):
        with resolver.batch_changes():
            resolver.write_entity("scenes/first.yaml", "key: first\nname: 示例\n")
            with resolver.batch_changes():
                resolver.write_entity("scenes/second.yaml", "key: second\nname: 示例\n")
            raise RuntimeError("partial failure")
    assert rebuild.call_count == 1
    assert load_content(resolver)["scenes"] == {"first": 1, "second": 1}
