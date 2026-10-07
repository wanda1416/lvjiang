"""独立存储迁移必须保留旧数据并能从中断位置继续完成。"""
import json

import pytest

from lvjiang.core.config.interface import InterfaceStore
from lvjiang.core.config.session import SessionStore


def test_migration_moves_only_owned_nodes_and_never_falls_back_again(tmp_path, monkeypatch):
    source = tmp_path / "session.json"
    old = {"version": 2, "settings": {"language": "zh_CN"}, "users": ["tester"],
           "ui_state": {"main_page": {"window_size": [1000, 700]}},
           "profile": {"overview_groups": {"demo": {"columns": ["name"]}},
                       "alert_history": {"tester:key": "now"}},
           "alert_info": [{"id": "test", "message": "测试"}],
           "server_config": {"skip_version": "0.0.0"}}
    source.write_text(json.dumps(old), encoding="utf-8")
    target = tmp_path / "interface.json"
    store = InterfaceStore(target)
    assert store.get_node("profile") == {"overview_groups": {"demo": {"columns": ["name"]}}}
    assert store.get_node("alert_history") == {"tester:key": "now"}
    assert store.get_node("alert_info") == old["alert_info"]
    assert store.get_node("server_config") == old["server_config"]
    assert store.get_node("ui_state") == old["ui_state"]
    assert json.loads(source.read_text()) == {"version": 2, "settings": {"language": "zh_CN"}, "users": ["tester"]}
    # 新文件存在时，即使旧文件残留了旧节点，也不回读/覆盖新文件。
    source.write_text(json.dumps(old))
    store.update_node("server_config", {"skip_version": "1.0.0"})
    original_read = SessionStore._read_disk

    def forbid_legacy_read(self, **kwargs):
        if self.path == source:
            raise AssertionError("新文件存在时不得回读旧存储")
        return original_read(self, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(SessionStore, "_read_disk", forbid_legacy_read)
        assert InterfaceStore(target).get_node("server_config")["skip_version"] == "1.0.0"
    assert json.loads(source.read_text()) == old


def test_migration_write_failure_preserves_source_and_cleanup_can_resume(tmp_path, monkeypatch):
    source = tmp_path / "session.json"
    source.write_text('{"profile":{"alert_history":{"test":"now"}},"settings":{"keep":true}}')
    target = tmp_path / "interface.json"
    old_bytes = source.read_bytes()
    original_write = SessionStore._write_disk_atomic

    def fail_new(self, data):
        if self.path == target:
            raise OSError("new file failed")
        original_write(self, data)

    with monkeypatch.context() as patch:
        patch.setattr(SessionStore, "_write_disk_atomic", fail_new)
        with pytest.raises(OSError, match="new file"):
            InterfaceStore(target)
    assert source.read_bytes() == old_bytes
    assert not target.exists()

    def fail_cleanup(self, data):
        if self.path == source:
            raise OSError("cleanup failed")
        original_write(self, data)

    with monkeypatch.context() as patch:
        patch.setattr(SessionStore, "_write_disk_atomic", fail_cleanup)
        with pytest.raises(OSError, match="cleanup"):
            InterfaceStore(target)
    assert source.read_bytes() == old_bytes
    assert json.loads(target.read_text())["migration"]["version"] == 1
    assert InterfaceStore(target).get_node("alert_history") == {"test": "now"}
    assert json.loads(source.read_text()) == {"settings": {"keep": True}}
    assert "migration" not in json.loads(target.read_text())


def test_migration_refuses_corrupt_source_without_creating_destination(tmp_path):
    source = tmp_path / "session.json"
    source.write_text("broken")
    target = tmp_path / "interface.json"
    with pytest.raises(ValueError):
        InterfaceStore(target)
    assert source.read_text() == "broken"
    assert not target.exists()


def test_interface_writers_merge_latest_disk_state(tmp_path):
    path = tmp_path / "interface.json"
    first = InterfaceStore(path)
    second = InterfaceStore(path)
    first.update_node("server_config", {"skip_version": "1.0.0"})
    second.update_node("server_config", {"remote_config": {"checked": True}})
    assert InterfaceStore(path).get_node("server_config") == {
        "skip_version": "1.0.0", "remote_config": {"checked": True}}


def test_gui_startup_loads_and_migrates_before_creating_application(tmp_path, monkeypatch):
    from lvjiang import app, constants
    from lvjiang.core.config.interface import interface_path
    from lvjiang.core.config.session import reset_session_store

    constants.SESSION_PATH.parent.mkdir(parents=True, exist_ok=True)
    constants.SESSION_PATH.write_text('{"ui_state":{"main_page":{"window_size":[1230,710]}}}')
    reset_session_store()

    class StopBeforeQt(Exception):
        pass

    def create_application(args):
        assert interface_path().is_file()
        assert "ui_state" not in json.loads(constants.SESSION_PATH.read_text())
        assert json.loads(interface_path().read_text())["ui_state"]["main_page"]["window_size"] == [1230, 710]
        raise StopBeforeQt

    monkeypatch.setattr(app, "QApplication", create_application)
    with pytest.raises(StopBeforeQt):
        app.run_app()
    interface_path().unlink()
    constants.SESSION_PATH.write_text("broken")
    reset_session_store()
    with pytest.raises(ValueError):
        app.run_app()  # 读取失败也必须在 create_application 之前停止。
