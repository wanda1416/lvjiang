"""手机批量的草稿边界、启动令牌与执行占用契约。"""
import json

import pytest

from lvjiang.core.batch_config import BatchConfig, BatchConfigItem
from lvjiang.core.ondevice import batch, task_runner


@pytest.fixture
def batch_state(monkeypatch, tmp_path):
    from lvjiang import constants
    from lvjiang.core.config import wf_configs
    from lvjiang.core.user_config import User, save_user_metadata
    from lvjiang.workflows import discovery
    monkeypatch.setattr(constants, "USERS_DIR", tmp_path / "users")
    save_user_metadata(User(name="viewer", attributes={"role": "one"}), constants.USERS_DIR)
    item = BatchConfigItem(id="group", name="测试批量", task_ids=["first", "second"],
                           usernames=["viewer"], default_task_ids=["first", "second"], default_usernames=["viewer"])
    store = BatchConfig({item.id: item})
    monkeypatch.setattr(batch, "load_batch_config", lambda: store)
    from lvjiang.core import batch_config
    def mutate(mutator):
        mutator(store)
        return store
    monkeypatch.setattr(batch_config, "mutate_batch_config", mutate)
    monkeypatch.setattr(task_runner, "_local_user_names", lambda: ["viewer"])
    monkeypatch.setattr(task_runner, "_STATE", task_runner._TaskState())
    catalog = [{"id": key, "name": key, "batchable": True, "wf_file": "demo.wf", "parameters": [{"name": "count", "type": "int", "default": 1}]} for key in item.task_ids]
    monkeypatch.setattr(discovery, "list_exposed_scripts", lambda *args, **kw: catalog)
    monkeypatch.setattr(discovery, "discover_scripts", lambda: catalog)
    shared = {"count": 2}
    monkeypatch.setattr(wf_configs, "get_wf_config", lambda task: dict(shared))
    from lvjiang.core.ondevice import task_settings
    monkeypatch.setattr(task_settings, "validate_for_run", lambda *args: None)
    return item, shared


def test_mobile_order_and_group_browsing_do_not_change_definition(batch_state):
    item, _ = batch_state
    initial = item.to_dict()
    data = json.loads(batch.view(item.id))
    draft = data["draft"]
    draft["tasks"]["order"] = ["second", "first"]
    draft["tasks"]["checked"] = ["second"]
    saved = json.loads(batch.save(item.id, json.dumps(draft)))
    assert saved["ok"]
    assert json.loads(batch.view(item.id))["draft"]["tasks"] == {"order": ["second", "first"], "checked": ["second"]}
    assert item.to_dict() == initial
    restored = json.loads(batch.save(item.id, "{}", reset=True))
    assert restored["draft"]["tasks"]["order"] == ["first", "second"]
    assert item.to_dict() == initial


def test_prepared_plan_freezes_params_and_rejects_changed_start(batch_state):
    item, shared = batch_state
    worker, token, _ = batch._plan(item.id)
    assert worker._task_plan[(0, "first")].params["count"] == 2
    shared["count"] = 8
    assert worker._task_plan[(0, "first")].params["count"] == 2
    result = json.loads(batch.start(item.id, token))
    assert not result["ok"] and "重新准备" in result["message"]
    assert not task_runner.is_running()


def test_running_batch_blocks_single_task_and_draft_edits(batch_state):
    item, _ = batch_state
    task_runner._STATE.begin("batch:group", "测试批量")
    assert not json.loads(task_runner.start_task("first"))["ok"]
    assert not json.loads(batch.prepare(item.id))["ok"]
    assert not json.loads(batch.save(item.id, "{}"))["ok"]
    assert json.loads(task_runner.stop_task())["ok"]


def test_attribute_unit_requires_preparation_protocol(batch_state):
    item, _ = batch_state
    item.execution_unit_key = "role"
    data = json.loads(batch.view(item.id))
    assert data["entries"][0]["members"] == ["viewer"]
    result = json.loads(batch.prepare(item.id))
    assert not result["ok"] and "选人协议" in result["message"]


def test_definition_editor_does_not_change_active_group_or_run_draft(batch_state):
    from lvjiang.core.batch_run import active_group_id, set_active_group_id
    item, _ = batch_state
    set_active_group_id(item.id)
    view = json.loads(batch.view(item.id))
    draft = view["draft"]
    draft["tasks"]["order"] = ["second", "first"]
    assert json.loads(batch.save(item.id, json.dumps(draft)))["ok"]
    from lvjiang.core.batch_run import load_draft
    before = load_draft(item.id).to_dict()
    model = json.loads(batch.editor(item.id))
    definition = model["definition"]
    definition["name"] = "修改名称"
    definition["task_ids"] = ["first", "second"]
    assert json.loads(batch.save_definition(item.id, model["token"], json.dumps(definition)))["ok"]
    assert active_group_id() == item.id
    assert load_draft(item.id).to_dict() == before
    # 切换到新建编辑上下文不改变活动选择或已保存的默认值。
    json.loads(batch.editor())
    assert active_group_id() == item.id
    assert batch._groups()[item.id].default_task_ids == ["first", "second"]
    stale = json.loads(batch.save_definition(item.id, model["token"], json.dumps(definition)))
    assert not stale["ok"] and "已变化" in stale["message"]


def test_delete_definition_only_repairs_deleted_reference(batch_state):
    from lvjiang.core.batch_run import active_group_id, set_active_group_id
    item, _ = batch_state
    payload = json.loads(batch.editor())["definition"]
    payload["name"] = "另一个配置"
    created = json.loads(batch.save_definition("", "", json.dumps(payload)))
    assert created["ok"]
    other = created["group_id"]
    set_active_group_id(item.id)
    model = json.loads(batch.editor(other))
    assert json.loads(batch.delete_definition(other, model["token"]))["ok"]
    assert active_group_id() == item.id
    model = json.loads(batch.editor(item.id))
    assert json.loads(batch.delete_definition(item.id, model["token"]))["ok"]
    assert active_group_id() == ""
