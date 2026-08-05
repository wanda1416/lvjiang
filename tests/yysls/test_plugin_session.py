"""PluginSession 插件会话配置测试"""

import json

import pytest

from lvjiang.apps.yysls.session import PluginSession


@pytest.fixture
def plugin_path(tmp_path):
    return tmp_path / "session.json"


class TestSectionReadWrite:
    def test_roundtrip(self, plugin_path):
        s = PluginSession(path=plugin_path)
        s.set_section("tuning", {"selected_slots": ["ring"]})
        # 落盘到主 session.json 的 yysls 节点
        saved = json.loads(plugin_path.read_text(encoding="utf-8"))
        assert saved["yysls"]["tuning"] == {"selected_slots": ["ring"]}
        # 重新加载可还原
        s2 = PluginSession(path=plugin_path)
        assert s2.get_section("tuning") == {"selected_slots": ["ring"]}

    def test_missing_section_returns_empty_dict(self, plugin_path):
        s = PluginSession(path=plugin_path)
        assert s.get_section("tuning") == {}

    def test_non_dict_section_returns_empty_dict(self, plugin_path):
        plugin_path.parent.mkdir(parents=True, exist_ok=True)
        plugin_path.write_text(json.dumps({"yysls": {"tuning": [1, 2]}}), encoding="utf-8")
        s = PluginSession(path=plugin_path)
        assert s.get_section("tuning") == {}

    def test_read_does_not_create_file(self, plugin_path):
        s = PluginSession(path=plugin_path)
        s.get_section("tuning")
        assert not plugin_path.exists()  # 只读不落盘

    def test_save_preserves_other_keys(self, plugin_path):
        """read-modify-write 不覆盖主 session.json 的其他顶层节点"""
        plugin_path.write_text(json.dumps({
            "ui_state": {"window_size": [800, 600]},
            "settings": {"adb_capture_streaming": True},
        }), encoding="utf-8")
        s = PluginSession(path=plugin_path)
        s.set_section("tuning", {"skip_tuning": True})

        saved = json.loads(plugin_path.read_text(encoding="utf-8"))
        # 插件数据写入 yysls 节点
        assert saved["yysls"]["tuning"] == {"skip_tuning": True}
        # 其他节点原样保留
        assert saved["ui_state"] == {"window_size": [800, 600]}
        assert saved["settings"] == {"adb_capture_streaming": True}
