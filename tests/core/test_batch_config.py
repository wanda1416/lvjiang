import json

from lvjiang.core.batch_config import (
    BATCH_DOCUMENT_TYPE,
    BatchConfig,
    BatchConfigItem,
    BatchConfigStore,
    BatchWorkflows,
    lifecycle_parameter_definitions,
)


def test_batch_workflows_round_trip_new_lifecycle():
    workflows = BatchWorkflows(
        batch_setup="batch/setup.wf",
        prepare_item="batch/prepare_item.wf",
        finish_item="batch/finish_item.wf",
        batch_teardown="batch/teardown.wf",
    )
    assert BatchWorkflows.from_dict(workflows.to_dict()) == workflows


def test_group_round_trip_preserves_actual_selection_order(tmp_path):
    path = tmp_path / "batch.json"
    group = BatchConfigItem(
        name="日常",
        task_ids=["b", "a"],
        usernames=["用户B", "用户A"],
        selected_task_ids=["a", "b"],
        selected_usernames=["用户A", "用户B"],
        rounds=3,
        workflow_params={
            "prepare_item": {"skip_online_role": False,
                             "online_role_max_wait": 300},
        },
        skip_lifecycle_for_single_item=False,
    )
    BatchConfigStore(path).save(BatchConfig({"日常": group}, "日常"))

    restored = BatchConfigStore(path).load().configs["日常"]
    assert restored.task_ids == ["b", "a"]
    assert restored.usernames == ["用户B", "用户A"]
    assert restored.selected_task_ids == ["a", "b"]
    assert restored.selected_usernames == ["用户A", "用户B"]
    assert restored.rounds == 3
    assert restored.workflow_params == group.workflow_params
    assert restored.skip_lifecycle_for_single_item is False


def test_group_rounds_default_and_normalize():
    assert BatchConfigItem.from_dict("默认", {}).rounds == 1
    assert BatchConfigItem.from_dict("过小", {"rounds": 0}).rounds == 1
    assert BatchConfigItem.from_dict("过大", {"rounds": 1000}).rounds == 999
    assert BatchConfigItem.from_dict("非法", {"rounds": "2"}).rounds == 1


def test_single_user_lifecycle_skip_defaults_true_and_rejects_invalid_value():
    assert BatchConfigItem.from_dict(
        "默认", {}).skip_lifecycle_for_single_item is True
    assert BatchConfigItem.from_dict(
        "关闭", {"skip_lifecycle_for_single_item": False}
    ).skip_lifecycle_for_single_item is False
    assert BatchConfigItem.from_dict(
        "非法", {"skip_lifecycle_for_single_item": "false"}
    ).skip_lifecycle_for_single_item is True


def test_old_session_batch_shape_is_not_accepted(tmp_path):
    path = tmp_path / "batch.json"
    path.write_text(json.dumps({
        "configs": {"旧配置": {"usernames": ["用户A"]}},
        "active_config": "旧配置",
        "script_ids": ["old-task"],
    }), encoding="utf-8")

    assert BatchConfigStore(path).load() == BatchConfig()


def test_saved_document_has_own_type_and_version(tmp_path):
    path = tmp_path / "batch.json"
    BatchConfigStore(path).save(BatchConfig())
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["document_type"] == BATCH_DOCUMENT_TYPE
    assert raw["version"] == 1


def test_lifecycle_parameter_definitions_reads_workflow_metadata():
    definitions = lifecycle_parameter_definitions(BatchWorkflows(
        prepare_item="batch/prepare_item.wf",
        finish_item="batch/finish_item.wf",
    ))
    assert [item["name"] for item in definitions["prepare_item"]] == [
        "skip_online_role", "online_role_max_wait", "restart_app_if_not_login",
    ]
    assert definitions["finish_item"] == [{
        "name": "stop_app",
        "label": "结束时直接停止应用",
        "type": "bool",
        "default": False,
    }]
