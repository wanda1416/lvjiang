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
