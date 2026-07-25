"""PluginSession 插件会话配置测试"""

import json

import pytest

from src.apps.yysls.session import PluginSession


@pytest.fixture
def plugin_path(tmp_path):
    return tmp_path / "yysls" / "session.json"


class TestSectionReadWrite:
    def test_roundtrip(self, plugin_path):
        s = PluginSession(path=plugin_path)
        s.set_section("tuning", {"selected_slots": ["ring"]})
        # 落盘 + 重新加载可还原
        assert json.loads(plugin_path.read_text(encoding="utf-8"))["tuning"] == {
            "selected_slots": ["ring"]}
        s2 = PluginSession(path=plugin_path)
        assert s2.get_section("tuning") == {"selected_slots": ["ring"]}

    def test_missing_section_returns_empty_dict(self, plugin_path):
        s = PluginSession(path=plugin_path)
        assert s.get_section("tuning") == {}

    def test_non_dict_section_returns_empty_dict(self, plugin_path):
        plugin_path.parent.mkdir(parents=True)
        plugin_path.write_text(json.dumps({"tuning": [1, 2]}), encoding="utf-8")
        s = PluginSession(path=plugin_path)
        assert s.get_section("tuning") == {}

    def test_read_does_not_create_file(self, plugin_path):
        s = PluginSession(path=plugin_path)
        s.get_section("tuning")
        assert not plugin_path.exists()  # 只读不落盘
