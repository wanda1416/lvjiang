"""wf_configs 核心模块测试

覆盖：
- get/set/delete/get_all 基本读写
- 深拷贝隔离（读写互不干扰）
- session.json 旧 wf_configs 节点一次性迁出到 workflow.json
- 写路径标脏（workflow 文档待同步）
"""
import json

import pytest

from lvjiang.core.config.wf_configs import (
    get_all_wf_configs,
    get_wf_config,
    set_wf_config,
    update_wf_config,
)

# ─── 基本读写 ──────────────────────────────────────────────


class TestBasicOperations:


    def test_set_does_not_affect_other_wf(self, session_store):
        set_wf_config("wf_a", {"key": "a"})
        set_wf_config("wf_b", {"key": "b"})
        assert get_wf_config("wf_a") == {"key": "a"}
        assert get_wf_config("wf_b") == {"key": "b"}


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


# ─── fixture ────────────────────────────────────────────────


@pytest.fixture
def store_path(tmp_path, monkeypatch):
    """把 SessionStore 与 workflow.json 指向 tmp_path 下的隔离文件"""
    import lvjiang.constants as constants_mod
    path = tmp_path / "session.json"
    monkeypatch.setattr(constants_mod, "SESSION_PATH", path)
    monkeypatch.setattr(constants_mod, "WORKFLOW_CONFIG_PATH",
                        tmp_path / "workflow.json")
    return path


@pytest.fixture
def session_store(store_path):
    """确保每个用例拿到干净的 SessionStore"""
    from lvjiang.core.config.session import reset_session_store
    reset_session_store()
    yield
    reset_session_store()


class TestMigration:
    def test_legacy_session_node_migrates_once(self, session_store, store_path):
        """session.json 的 wf_configs 节点一次性迁出到 workflow.json 并删除旧节点。"""
        store_path.write_text(
            json.dumps({"users": ["甲"], "wf_configs": {"task": {"count": 5}}}),
            encoding="utf-8")
        from lvjiang.core.config.session import get_session_store

        assert get_wf_config("task") == {"count": 5}
        # 旧节点已删，其余内容保留；workflow.json 是标准文档结构
        data = json.loads(store_path.read_text(encoding="utf-8"))
        assert data == {"users": ["甲"]}
        document = json.loads(
            store_path.with_name("workflow.json").read_text(encoding="utf-8"))
        assert document == {"workflow_params": {"task": {"count": 5}}}
        # 可重复执行：再次读取不受影响，也不重建旧节点
        assert get_wf_config("task") == {"count": 5}
        assert json.loads(store_path.read_text(encoding="utf-8")) == {"users": ["甲"]}
        assert get_session_store().get_node("wf_configs") is None

    def test_no_legacy_node_means_empty(self, session_store):
        assert get_wf_config("anything") == {}
        assert get_all_wf_configs() == {}


class TestDirtyMarking:
    def test_write_marks_workflow_document_dirty(self, session_store):
        """写路径标脏：账户级 workflow 文档待下一轮文档通道上传。"""
        import lvjiang.constants as constants_mod
        account = constants_mod.SESSION_CONFIG_DIR / "account.json"
        account.parent.mkdir(parents=True, exist_ok=True)
        account.write_text('{"x": 1}\n', encoding="utf-8")
        from lvjiang.core.sync.dirty import dirty_keys

        set_wf_config("task", {"count": 1})
        assert "__global__/workflow" in dirty_keys()


class TestPrune:
    def test_prune_removes_orphans_and_transient_ids_only(self, session_store):
        from lvjiang.core.config.wf_configs import prune_wf_configs

        set_wf_config("daily_jianghu", {"a": 1})
        set_wf_config("auto_tuning", {"b": 2})            # 专用脚本：不在入口展示，但存在
        set_wf_config("activity_jianghu", {"c": 3})       # 改名前的旧 id
        set_wf_config("__loaded__:purchase_bugan", {"d": 4})  # 编辑器临时 id

        removed = prune_wf_configs(["daily_jianghu", "auto_tuning", "purchase_bugan"])

        assert removed == ["__loaded__:purchase_bugan", "activity_jianghu"]
        assert set(get_all_wf_configs()) == {"daily_jianghu", "auto_tuning"}
        assert get_wf_config("auto_tuning") == {"b": 2}
