"""配置组定义层：只存"配置组是什么"，运行态一律不进 batch.json。"""
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
        recover_unattended="batch/recover_to_login.wf",
    )
    assert BatchWorkflows.from_dict(workflows.to_dict()) == workflows


def test_group_round_trip_preserves_definition(tmp_path):
    path = tmp_path / "batch.json"
    group = BatchConfigItem(
        name="日常",
        task_ids=["b", "a"],
        usernames=["用户B", "用户A"],
        default_task_ids=["a"],
        default_usernames=["用户A"],
        default_units={"account": ["acc1"]},
        execution_unit_key="account",
        profile_sort_key="weekly_work",
        profile_sort_direction="desc",
        workflow_params={
            "prepare_item": {"skip_online_role": False,
                             "online_role_max_wait": 300},
        },
        skip_lifecycle_for_single_item=False,
    )
    store = BatchConfigStore(path)
    store.save(BatchConfig({group.id: group}))

    restored = store.load().by_name("日常")
    assert restored is not None
    assert restored.id == group.id, "稳定 ID 必须随盘往返，草稿和历史都按它关联"
    assert restored.task_ids == ["b", "a"]
    assert restored.usernames == ["用户B", "用户A"]
    assert restored.default_task_ids == ["a"]
    assert restored.default_usernames == ["用户A"]
    assert restored.default_units == {"account": ["acc1"]}
    assert restored.execution_unit_key == "account"
    assert restored.profile_sort_key == "weekly_work"
    assert restored.profile_sort_direction == "desc"
    assert restored.workflow_params == group.workflow_params
    assert restored.skip_lifecycle_for_single_item is False


def test_definition_has_no_runtime_fields():
    """轮数、本次勾选、无人值守都属于运行草稿，不该在定义层出现。

    它们回到定义层就意味着主页面的一次临时选择又会写回配置组——那是这次
    分层要解决的问题本身。
    """
    item = BatchConfigItem(name="组")
    for field in ("rounds", "unattended", "selected_task_ids",
                  "selected_usernames", "selected_units"):
        assert not hasattr(item, field), field
    assert "rounds" not in item.to_dict()
    assert "unattended" not in item.to_dict()


def test_defaults_are_pruned_to_visible_items():
    """可见范围缩小后，失效的默认勾选不能留着——否则「恢复默认」会勾出不存在的条目。"""
    item = BatchConfigItem.from_dict("组", {
        "task_ids": ["a"],
        "usernames": ["用户A"],
        "default_task_ids": ["a", "b"],
        "default_usernames": ["用户A", "用户B"],
    })

    assert item.default_task_ids == ["a"]
    assert item.default_usernames == ["用户A"]


def test_v1_selection_is_read_as_default_selection(tmp_path):
    """v1 的 selected_* 在旧版本里既是上次勾选又是下次初始值。

    拆层后只保留后一半语义读进默认勾选；rounds / unattended 属于运行草稿，
    直接丢掉。写出去只有新格式，所以这是一次性单向读取，不是常驻兼容层。
    """
    path = tmp_path / "batch.json"
    path.write_text(json.dumps({
        "document_type": BATCH_DOCUMENT_TYPE,
        "version": 1,
        "active_group": "日常",
        "groups": {"日常": {
            "task_ids": ["a", "b"],
            "usernames": ["用户A"],
            "selected_task_ids": ["b"],
            "selected_usernames": ["用户A"],
            "selected_units": {"account": ["acc1"]},
            "rounds": 4,
            "unattended": True,
        }},
    }), encoding="utf-8")

    cfg = BatchConfigStore(path).load()
    item = cfg.by_name("日常")
    assert item is not None
    assert item.id, "v1 没有 ID，读入时必须补一个稳定 ID"
    assert item.default_task_ids == ["b"]
    assert item.default_usernames == ["用户A"]
    assert item.default_units == {"account": ["acc1"]}
    assert cfg.configs == {item.id: item}, "读入后一律按 ID 索引"


def test_v1_migration_is_persisted_and_ids_stay_stable(tmp_path):
    """v1 没有 ID，首次读取必须一次性落盘，不能每次 load 都随机生成。"""
    path = tmp_path / "batch.json"
    path.write_text(json.dumps({
        "document_type": BATCH_DOCUMENT_TYPE,
        "version": 1,
        "active_group": "第二组",
        "groups": {
            "第一组": {"task_ids": ["a"]},
            "第二组": {"task_ids": ["b"]},
        },
    }), encoding="utf-8")
    store = BatchConfigStore(path)

    first = store.load()
    second = store.load()

    assert list(first.configs) == list(second.configs)
    assert first.names() == ["第一组", "第二组"]
    written = json.loads(path.read_text(encoding="utf-8"))
    assert written["version"] == 2
    assert list(written["groups"]) == list(first.configs)


def test_single_user_lifecycle_skip_defaults_true_and_rejects_invalid_value():
    assert BatchConfigItem.from_dict(
        "默认", {}).skip_lifecycle_for_single_item is True
    assert BatchConfigItem.from_dict(
        "关闭", {"skip_lifecycle_for_single_item": False}
    ).skip_lifecycle_for_single_item is False
    assert BatchConfigItem.from_dict(
        "非法", {"skip_lifecycle_for_single_item": "false"}
    ).skip_lifecycle_for_single_item is True
    assert BatchConfigItem.from_dict("默认", {}).profile_sort_direction == "asc"
    assert BatchConfigItem.from_dict(
        "非法", {"profile_sort_direction": "invalid"}
    ).profile_sort_direction == "asc"


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
    assert raw["version"] == 2
    assert "active_group" not in raw, "活动配置组属于主页面状态，存 session"


def test_rename_keeps_identity_and_position():
    """按 ID 索引：重命名不动位置，也不会让草稿、历史的关联断掉。"""
    first = BatchConfigItem(name="甲")
    second = BatchConfigItem(name="乙")
    cfg = BatchConfig()
    cfg.add(first)
    cfg.add(second)

    first.name = "甲改"

    assert cfg.names() == ["甲改", "乙"]
    assert cfg.by_name("甲改") is first
    assert cfg.resolve_id(first.id) == first.id


def test_resolve_id_falls_back_to_first_group():
    cfg = BatchConfig()
    item = cfg.add(BatchConfigItem(name="组"))

    assert cfg.resolve_id("已删除的 ID") == item.id
    assert BatchConfig().resolve_id("任意") == ""


def test_lifecycle_parameter_definitions_reads_workflow_metadata():
    definitions = lifecycle_parameter_definitions(BatchWorkflows(
        prepare_item="batch/prepare_item.wf",
        finish_item="batch/finish_item.wf",
    ))
    assert [item["name"] for item in definitions["prepare_item"]] == [
        "max_roll_account", "skip_online_role", "online_role_max_wait",
        "allow_restart_app",
    ]
    # 只钉结构，不钉文案：标签是会被反复润色的展示文字，钉死它只会在改措辞
    # 时误报，却拦不住"没读到 wf 的参数声明"这个真正的回归。
    stop_app = definitions["finish_item"]
    assert [item["name"] for item in stop_app] == ["stop_app"]
    assert stop_app[0]["type"] == "bool"
    assert stop_app[0]["default"] is False
    assert stop_app[0]["label"]
