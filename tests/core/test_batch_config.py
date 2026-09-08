from lvjiang.core.batch_config import BatchConfigItem, BatchWorkflows


def test_batch_workflows_round_trip_new_lifecycle():
    workflows = BatchWorkflows(
        batch_setup="batch/setup.wf",
        prepare_item="batch/prepare_item.wf",
        finish_item="batch/finish_item.wf",
        batch_teardown="batch/teardown.wf",
    )

    assert BatchWorkflows.from_dict(workflows.to_dict()) == workflows


def test_usernames_round_trip():
    legacy = BatchConfigItem.from_dict({"name": "旧配置"})
    legacy.usernames = ["用户B", "用户A"]
    restored = BatchConfigItem.from_dict(legacy.to_dict())
    assert restored.usernames == ["用户B", "用户A"]


# ─── batch 节点是共享的，保存不能整节点覆写 ────────────────


class _Store:
    """最小 SessionStore 替身：只关心 batch 节点的读改写。"""

    def __init__(self, node):
        self.node = node

    def get_node(self, _name, default=None):
        return self.node if self.node is not None else default

    def mutate_node(self, _name, merge):
        self.node = merge(self.node)
        return self.node


def _store(monkeypatch, node):
    store = _Store(node)
    monkeypatch.setattr(
        "lvjiang.core.config.session.get_session_store", lambda: store
    )
    return store


def test_saving_config_replaces_obsolete_batch_keys(monkeypatch):
    from lvjiang.core.batch_config import BatchConfig, save_batch_config

    store = _store(monkeypatch, {
        "enabled_rows": {"demo": [True, False]},
        "future_key": {"kept": 1},
    })

    save_batch_config(BatchConfig(active_config="demo", script_ids=["a"]))

    assert "enabled_rows" not in store.node
    assert "future_key" not in store.node
    assert store.node["script_ids"] == ["a"]
    assert store.node["active_config"] == "demo"
