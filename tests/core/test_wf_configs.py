"""wf_configs 核心模块测试

覆盖：
- get/set/delete/get_all 基本读写
- 深拷贝隔离（读写互不干扰）
"""
import pytest

from lvjiang.core.config.wf_configs import (
    delete_wf_config,
    get_all_wf_configs,
    get_wf_config,
    set_wf_config,
    update_wf_config,
)

# ─── 基本读写 ──────────────────────────────────────────────


class TestBasicOperations:
    def test_get_nonexistent_returns_empty(self, session_store):
        assert get_wf_config("no_such_wf") == {}

    def test_set_then_get(self, session_store):
        set_wf_config("activity_jianghu", {"max_refresh": "20"})
        cfg = get_wf_config("activity_jianghu")
        assert cfg == {"max_refresh": "20"}

    def test_set_does_not_affect_other_wf(self, session_store):
        set_wf_config("wf_a", {"key": "a"})
        set_wf_config("wf_b", {"key": "b"})
        assert get_wf_config("wf_a") == {"key": "a"}
        assert get_wf_config("wf_b") == {"key": "b"}

    def test_delete_wf(self, session_store):
        set_wf_config("wf_x", {"data": 1})
        delete_wf_config("wf_x")
        assert get_wf_config("wf_x") == {}

    def test_delete_nonexistent_is_silent(self, session_store):
        delete_wf_config("never_existed")  # 不抛异常
        assert get_wf_config("never_existed") == {}  # 仍为空

    def test_get_all(self, session_store):
        set_wf_config("wf_1", {"a": 1})
        set_wf_config("wf_2", {"b": 2})
        all_cfgs = get_all_wf_configs()
        assert "wf_1" in all_cfgs
        assert "wf_2" in all_cfgs

    def test_overwrite_existing(self, session_store):
        set_wf_config("wf", {"v": 1})
        set_wf_config("wf", {"v": 2})
        assert get_wf_config("wf") == {"v": 2}

    def test_update_merges_fields_for_single_wf(self, session_store):
        set_wf_config("wf", {"owned": 1, "foreign": {"keep": True}})
        updated = update_wf_config("wf", {"owned": 2})
        assert updated == {"owned": 2, "foreign": {"keep": True}}
        assert get_wf_config("wf") == {"owned": 2, "foreign": {"keep": True}}

    def test_update_does_not_affect_other_wf(self, session_store):
        set_wf_config("wf_a", {"a": 1})
        set_wf_config("wf_b", {"b": 1})
        update_wf_config("wf_a", {"a": 2})
        assert get_wf_config("wf_a") == {"a": 2}
        assert get_wf_config("wf_b") == {"b": 1}


# ─── 深拷贝隔离 ────────────────────────────────────────────


class TestDeepCopyIsolation:
    def test_get_returns_independent_copy(self, session_store):
        """修改 get 返回值不影响 store"""
        set_wf_config("wf", {"nested": {"key": "original"}})
        cfg = get_wf_config("wf")
        cfg["nested"]["key"] = "mutated"
        assert get_wf_config("wf")["nested"]["key"] == "original"

    def test_set_deep_copies_input(self, session_store):
        """set 后修改传入 dict 不影响 store"""
        config = {"nested": {"key": "original"}}
        set_wf_config("wf", config)
        config["nested"]["key"] = "mutated"
        assert get_wf_config("wf")["nested"]["key"] == "original"

    def test_get_all_returns_independent_copy(self, session_store):
        set_wf_config("wf", {"data": 1})
        all_cfgs = get_all_wf_configs()
        all_cfgs["wf"]["data"] = 999
        assert get_wf_config("wf")["data"] == 1


# ─── fixture ────────────────────────────────────────────────


@pytest.fixture
def store_path(tmp_path, monkeypatch):
    """把 SessionStore 指向 tmp_path 下的隔离文件"""
    import lvjiang.constants as constants_mod
    path = tmp_path / "session.json"
    monkeypatch.setattr(constants_mod, "SESSION_PATH", path)
    return path


@pytest.fixture
def session_store(store_path):
    """确保每个用例拿到干净的 SessionStore"""
    from lvjiang.core.config.session import reset_session_store
    reset_session_store()
    yield
    reset_session_store()
