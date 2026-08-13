"""SessionStore 单元测试

session.json 唯一读写咽喉的核心语义：
- 节点隔离：各顶层节点独立读写互不干扰
- 内存缓存 + 写即落盘：单写者快照，多入口不再相互覆盖
- update_node 浅合并、mutate_node 原子读改写
- 多线程并发写不丢键
- 损坏文件容错、get_node 深拷贝隔离
"""

import json
import threading

import pytest

from lvjiang.core.config import SessionStore


@pytest.fixture
def store(tmp_path):
    return SessionStore(tmp_path / "session.json")


class TestNodeOps:
    def test_get_missing_returns_default(self, store):
        assert store.get_node("ui_state") is None
        assert store.get_node("ui_state", {}) == {}

    def test_set_get_roundtrip(self, store):
        store.set_node("active_layout", "默认布局")
        assert store.get_node("active_layout") == "默认布局"

    def test_node_isolation(self, store):
        """不同节点独立读写，互不覆盖"""
        store.set_node("ui_state", {"window_size": [800, 600]})
        store.set_node("daily", {"script": "a.wf"})
        store.set_node("active_layout", "默认布局")
        assert store.get_node("ui_state") == {"window_size": [800, 600]}
        assert store.get_node("daily") == {"script": "a.wf"}
        assert store.get_node("active_layout") == "默认布局"

    def test_set_persists_to_disk(self, store):
        store.set_node("settings", {"adb": True})
        data = json.loads(store.path.read_text(encoding="utf-8"))
        assert data == {"settings": {"adb": True}}

    def test_delete_node(self, store):
        store.set_node("daily", {"x": 1})
        store.delete_node("daily")
        assert store.get_node("daily") is None
        store.delete_node("daily")  # 二次删除静默

    def test_update_node_shallow_merge(self, store):
        """多组件分写同一节点：各自 patch 合并保留"""
        store.update_node("ui_state", {"window_size": [800, 600]})
        store.update_node("ui_state", {"scene_editor_pos": [10, 20]})
        assert store.get_node("ui_state") == {
            "window_size": [800, 600],
            "scene_editor_pos": [10, 20],
        }

    def test_update_node_rebuilds_non_dict(self, store):
        store.set_node("ui_state", "坏值")
        store.update_node("ui_state", {"k": 1})
        assert store.get_node("ui_state") == {"k": 1}

    def test_mutate_node_atomic(self, store):
        """mutate_node 在锁内读-改-写，返回新值"""
        store.set_node("yysls", {"tuning": {"a": 1}})
        new = store.mutate_node(
            "yysls", lambda old: {**(old or {}), "extra": True})
        assert new == {"tuning": {"a": 1}, "extra": True}
        assert store.get_node("yysls") == new

    def test_get_node_returns_deepcopy(self, store):
        """调用方修改返回值不影响内部态"""
        store.set_node("daily", {"list": [1, 2]})
        got = store.get_node("daily")
        got["list"].append(999)
        got["injected"] = True
        assert store.get_node("daily") == {"list": [1, 2]}


class TestDiskSemantics:
    def test_existing_file_loaded_lazily(self, tmp_path):
        path = tmp_path / "session.json"
        path.write_text(json.dumps({"active_space": "默认"}), encoding="utf-8")
        store = SessionStore(path)
        assert store.get_node("active_space") == "默认"
        # 写入保留既有节点
        store.set_node("active_layout", "L1")
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data == {"active_space": "默认", "active_layout": "L1"}

    def test_corrupt_file_treated_as_empty(self, tmp_path):
        path = tmp_path / "session.json"
        path.write_text("{损坏的 json", encoding="utf-8")
        store = SessionStore(path)
        assert store.get_node("anything") is None
        store.set_node("k", 1)  # 可继续正常写入

    def test_non_dict_root_treated_as_empty(self, tmp_path):
        path = tmp_path / "session.json"
        path.write_text("[1, 2, 3]", encoding="utf-8")
        store = SessionStore(path)
        assert store.get_node("k") is None

    def test_reload_rereads_disk(self, tmp_path):
        path = tmp_path / "session.json"
        store = SessionStore(path)
        store.set_node("a", 1)
        # 外部直接改盘（模拟另一进程），reload 后可见
        data = json.loads(path.read_text(encoding="utf-8"))
        data["b"] = 2
        path.write_text(json.dumps(data), encoding="utf-8")
        assert store.get_node("b") is None
        store.reload()
        assert store.get_node("b") == 2

    def test_no_tmp_files_left(self, store):
        store.set_node("k", 1)
        leftovers = [p for p in store.path.parent.iterdir()
                     if p.name.startswith(".session_")]
        assert leftovers == []


class TestConcurrency:
    def test_concurrent_updates_no_lost_keys(self, tmp_path):
        """多线程并发 update_node 各自节点键不丢失（单写者快照语义）"""
        store = SessionStore(tmp_path / "session.json")
        n_threads, n_writes = 8, 25

        def worker(tid: int):
            for i in range(n_writes):
                store.update_node("ui_state", {f"t{tid}_k{i}": i})

        threads = [threading.Thread(target=worker, args=(t,))
                   for t in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        node = store.get_node("ui_state")
        assert len(node) == n_threads * n_writes
        # 落盘内容与内存态一致
        disk = json.loads(store.path.read_text(encoding="utf-8"))
        assert len(disk["ui_state"]) == n_threads * n_writes

    def test_concurrent_mixed_ops(self, tmp_path):
        """不同节点并发读写不串扰"""
        store = SessionStore(tmp_path / "session.json")

        def writer(key: str):
            for i in range(20):
                store.set_node(key, {"i": i})

        threads = [threading.Thread(target=writer, args=(f"node_{t}",))
                   for t in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        for t in range(6):
            assert store.get_node(f"node_{t}") == {"i": 19}


class TestDefaultPath:
    def test_path_follows_constants(self, tmp_path, monkeypatch):
        """缺省路径动态取 constants.SESSION_PATH（monkeypatch 友好）"""
        from lvjiang import constants

        monkeypatch.setattr(constants, "SESSION_PATH", tmp_path / "s.json")
        store = SessionStore()
        store.set_node("k", 1)
        assert (tmp_path / "s.json").exists()


class TestAlertStorage:
    """告警存储接口测试"""

    def test_get_alerts_empty(self, store):
        """无告警时返回空列表"""
        from lvjiang.core.config.session import get_alerts, reset_session_store
        reset_session_store()
        # 使用测试 store
        import lvjiang.core.config.session as session_mod
        session_mod._store = store
        assert get_alerts() == []

    def test_add_and_get_alerts(self, store):
        """添加告警后可读取"""
        from lvjiang.core.config.session import (
            add_alert,
            get_alerts,
            reset_session_store,
        )
        reset_session_store()
        import lvjiang.core.config.session as session_mod
        session_mod._store = store

        add_alert("test:1", "测试告警", "2026-08-11T12:00:00")
        alerts = get_alerts()
        assert len(alerts) == 1
        assert alerts[0]["id"] == "test:1"
        assert alerts[0]["message"] == "测试告警"
        assert alerts[0]["timestamp"] == "2026-08-11T12:00:00"

    def test_add_alert_dedup(self, store):
        """同 ID 告警不重复添加"""
        from lvjiang.core.config.session import (
            add_alert,
            get_alerts,
            reset_session_store,
        )
        reset_session_store()
        import lvjiang.core.config.session as session_mod
        session_mod._store = store

        assert add_alert("test:1", "第一次", "2026-08-11T12:00:00") is True
        assert add_alert("test:1", "第二次", "2026-08-11T12:01:00") is False
        alerts = get_alerts()
        assert len(alerts) == 1
        assert alerts[0]["message"] == "第一次"  # 保留第一次

    def test_add_alert_lifo_order(self, store):
        """新告警插入栈顶（列表头部）"""
        from lvjiang.core.config.session import (
            add_alert,
            get_alerts,
            reset_session_store,
        )
        reset_session_store()
        import lvjiang.core.config.session as session_mod
        session_mod._store = store

        add_alert("test:1", "第一条", "2026-08-11T12:00:00")
        add_alert("test:2", "第二条", "2026-08-11T12:01:00")
        alerts = get_alerts()
        assert len(alerts) == 2
        assert alerts[0]["id"] == "test:2"  # 最新的在前
        assert alerts[1]["id"] == "test:1"

    def test_dismiss_alert(self, store):
        """移除指定 ID 的告警"""
        from lvjiang.core.config.session import (
            add_alert,
            dismiss_alert,
            get_alerts,
            reset_session_store,
        )
        reset_session_store()
        import lvjiang.core.config.session as session_mod
        session_mod._store = store

        add_alert("test:1", "第一条", "2026-08-11T12:00:00")
        add_alert("test:2", "第二条", "2026-08-11T12:01:00")
        dismiss_alert("test:1")
        alerts = get_alerts()
        assert len(alerts) == 1
        assert alerts[0]["id"] == "test:2"

    def test_dismiss_nonexistent_alert(self, store):
        """移除不存在的告警静默成功"""
        from lvjiang.core.config.session import (
            add_alert,
            dismiss_alert,
            get_alerts,
            reset_session_store,
        )
        reset_session_store()
        import lvjiang.core.config.session as session_mod
        session_mod._store = store

        add_alert("test:1", "第一条", "2026-08-11T12:00:00")
        dismiss_alert("nonexistent")
        alerts = get_alerts()
        assert len(alerts) == 1
