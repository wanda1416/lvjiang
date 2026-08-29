"""旧 local/workflows.yaml → session 的一次性迁移

0.5.4 之前顺序/勾选/显示名存在配置层，会把系统新增脚本冻住。迁移把它搬进
session 并归档旧文件。本文件随迁移代码一起删除。
"""

import yaml

from lvjiang.workflows import preferences as prefs_mod


class _FakeStore:
    def __init__(self):
        self.nodes: dict = {}

    def get_node(self, key, default=None):
        return self.nodes.get(key, default)

    def update_node(self, key, patch):
        self.nodes.setdefault(key, {}).update(patch)


def _setup(monkeypatch, tmp_path, legacy: dict | None):
    store = _FakeStore()
    monkeypatch.setattr("lvjiang.core.config.get_session_store", lambda: store)
    local = tmp_path / "local"
    local.mkdir()
    if legacy is not None:
        (local / "workflows.yaml").write_text(
            yaml.dump(legacy, allow_unicode=True), encoding="utf-8")
    monkeypatch.setattr(
        "lvjiang.core.config.resolver.get_resolver",
        lambda: type("R", (), {"local_dir": local})())
    return store, local


class TestMigration:
    def test_migrates_exposed_and_names(self, monkeypatch, tmp_path):
        store, local = _setup(monkeypatch, tmp_path, {
            "exposed": ["b", "a"],
            "overrides": {"a": {"name": "甲"}, "b": {"scope": "dedicated"}},
        })
        assert prefs_mod.migrate_legacy_workflows_yaml() is True
        got = store.nodes["daily"]["scripts"]
        assert got["order"] == ["b", "a"]
        assert got["names"] == {"a": "甲"}
        assert got["scopes"] == {"b": "dedicated"}
        assert got["visible"] == {"b": True, "a": True}

    def test_legacy_file_archived(self, monkeypatch, tmp_path):
        _, local = _setup(monkeypatch, tmp_path, {"exposed": ["a"]})
        prefs_mod.migrate_legacy_workflows_yaml()
        assert not (local / "workflows.yaml").exists()
        assert (local / "workflows.yaml.migrated").exists()

    def test_noop_without_legacy_file(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path, None)
        assert prefs_mod.migrate_legacy_workflows_yaml() is False

    def test_noop_when_session_already_has_prefs(self, monkeypatch, tmp_path):
        """已搬过就不再动，避免覆盖用户在新模型下的改动。"""
        store, local = _setup(monkeypatch, tmp_path, {"exposed": ["a"]})
        store.nodes["daily"] = {"scripts": {"order": ["z"]}}
        assert prefs_mod.migrate_legacy_workflows_yaml() is False
        assert store.nodes["daily"]["scripts"]["order"] == ["z"]
        assert (local / "workflows.yaml").exists()

    def test_broken_yaml_does_not_raise(self, monkeypatch, tmp_path):
        _, local = _setup(monkeypatch, tmp_path, None)
        (local / "workflows.yaml").write_text("{[bad", encoding="utf-8")
        assert prefs_mod.migrate_legacy_workflows_yaml() is False

    def test_only_positive_visibility_migrated(self, monkeypatch, tmp_path):
        """旧 exposed 只搬正向勾选：分不清「用户取消了」和「当时还不存在」，
        宁可多显示也不静默藏掉系统新增的脚本。"""
        store, _ = _setup(monkeypatch, tmp_path, {"exposed": ["a"]})
        prefs_mod.migrate_legacy_workflows_yaml()
        assert store.nodes["daily"]["scripts"]["visible"] == {"a": True}
