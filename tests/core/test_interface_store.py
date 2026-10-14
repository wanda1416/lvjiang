"""界面状态独立保存，多个窗口写入时保留彼此的更新。"""
import json

import pytest

from lvjiang.core.config.interface import InterfaceStore


class TestActives:
    """激活项（当前在用哪个用户/布局/空间/环境/方案）存在 interface.json。"""

    def test_round_trip(self, tmp_path):
        path = tmp_path / "interface.json"
        store = InterfaceStore(path)

        assert store.get_active("plan", "") == ""

        store.set_active("plan", "7f3a")

        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["actives"] == {"plan": "7f3a"}

    def test_env_is_an_active_kind(self, tmp_path):
        store = InterfaceStore(tmp_path / "interface.json")

        store.set_active("env", "android")

        assert store.get_active("env") == "android"

    def test_unknown_active_kind_still_rejected(self, tmp_path):
        store = InterfaceStore(tmp_path / "interface.json")

        with pytest.raises(KeyError):
            store.get_active("没这个东西")
        with pytest.raises(KeyError):
            store.set_active("没这个东西", "x")


def test_interface_writers_merge_latest_disk_state(tmp_path):
    path = tmp_path / "interface.json"
    first = InterfaceStore(path)
    second = InterfaceStore(path)
    first.update_node("server_config", {"skip_version": "1.0.0"})
    second.update_node("server_config", {"announcement": {"etag": "test"}})
    assert InterfaceStore(path).get_node("server_config") == {
        "skip_version": "1.0.0", "announcement": {"etag": "test"}}
