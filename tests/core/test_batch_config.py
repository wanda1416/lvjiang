from lvjiang.core.batch_config import BatchConfigItem, BatchWorkflows


def test_batch_workflows_round_trip_new_lifecycle():
    workflows = BatchWorkflows(
        batch_setup="batch/setup.wf",
        prepare_item="batch/prepare_item.wf",
        finish_item="batch/finish_item.wf",
        batch_teardown="batch/teardown.wf",
    )

    assert BatchWorkflows.from_dict(workflows.to_dict()) == workflows


def test_single_item_lifecycle_shortcut_defaults_on_and_round_trips():
    legacy = BatchConfigItem.from_dict({"name": "旧配置"})
    assert legacy.skip_lifecycle_for_single_item is True

    legacy.skip_lifecycle_for_single_item = False
    restored = BatchConfigItem.from_dict(legacy.to_dict())
    assert restored.skip_lifecycle_for_single_item is False


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


def test_saving_config_keeps_other_keys_in_the_batch_node(monkeypatch):
    """enabled_rows 及任何后来者都不能被 save_batch_config 抹掉。"""
    from lvjiang.core.batch_config import BatchConfig, save_batch_config

    store = _store(monkeypatch, {
        "enabled_rows": {"demo": [True, False]},
        "future_key": {"kept": 1},
    })

    save_batch_config(BatchConfig(active_config="demo", script_ids=["a"]))

    assert store.node["enabled_rows"] == {"demo": [True, False]}
    assert store.node["future_key"] == {"kept": 1}
    assert store.node["script_ids"] == ["a"]
    assert store.node["active_config"] == "demo"


def test_enabled_rows_helpers_round_trip(monkeypatch):
    from lvjiang.core.batch_config import (
        config_enabled_flags,
        load_enabled_rows,
        save_enabled_rows,
    )

    store = _store(monkeypatch, {"script_ids": ["a"]})
    save_enabled_rows({"demo": [True, False]})

    assert load_enabled_rows() == {"demo": [True, False]}
    # 写勾选状态不能顺手丢掉配置本体。
    assert store.node["script_ids"] == ["a"]

    config = BatchConfigItem(
        name="demo", columns=["role"],
        rows=[{"role": "甲"}, {"role": "乙"}, {"role": "丙"}],
    )
    # 数组比行数短：缺失位视为启用。
    assert config_enabled_flags(config) == [True, False, True]
