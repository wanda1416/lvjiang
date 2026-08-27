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
