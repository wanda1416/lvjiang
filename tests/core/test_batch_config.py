from lvjiang.core.batch_config import BatchWorkflows


def test_batch_workflows_round_trip_new_lifecycle():
    workflows = BatchWorkflows(
        batch_setup="batch/setup.wf",
        prepare_item="batch/prepare_item.wf",
        finish_item="batch/finish_item.wf",
        batch_teardown="batch/teardown.wf",
    )

    assert BatchWorkflows.from_dict(workflows.to_dict()) == workflows
