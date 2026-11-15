"""手机配置的持续契约：编辑隔离、字段所有权、依赖与并发写入门禁。"""
import json

import pytest

from lvjiang import constants
from lvjiang.core.config.interface import get_interface_store
from lvjiang.core.config.wf_configs import get_wf_config, set_wf_config
from lvjiang.core.ondevice import task_runner, task_settings
from lvjiang.core.users.document import (
    User,
    get_user_workflow_params,
    save_user_metadata,
    set_user_workflow_params,
)

_real_adapter = task_settings._adapter


@pytest.fixture
def editor(tmp_path, monkeypatch):
    monkeypatch.setattr(constants, "USERS_DIR", tmp_path / "users")
    monkeypatch.setattr(task_runner, "_STATE", task_runner._TaskState())
    monkeypatch.setattr(task_settings, "sync_status", lambda: {"synced": True, "synced_at": "first"})
    monkeypatch.setattr(task_runner, "_local_user_names", lambda: ["runner", "editor"])
    monkeypatch.setattr(task_settings, "_adapter", lambda _task: None)
    for name in ("runner", "editor"):
        save_user_metadata(User(name), constants.USERS_DIR)
    get_interface_store().set_active("user", "runner")
    definitions = [
        {"name": "claim", "label": "领奖", "type": "bool", "default": True},
        {"name": "limit", "type": "number", "default": 10, "min": 1, "max": 20, "require": "$claim"},
        {"name": "mode", "type": "select", "default": "a", "options": ["a", "b"]},
        {"name": "items", "type": "checkgroup", "default": {"x": True}, "options": ["x", "y"]},
        {"name": "text", "type": "text", "default": "", "require": "$limit > 10"},
        {"name": "desktop", "type": "number", "default": 5, "env": ["windows"]},
    ]
    monkeypatch.setattr(task_settings, "_tasks", lambda: [
        {"id": "sample", "name": "示例任务", "parameters": definitions},
        {"id": "empty", "name": "无参数任务", "parameters": []},
    ])
    set_wf_config("sample", {"limit": 12, "desktop": 7})
    return definitions


def _get():
    result = json.loads(task_settings.get_settings("editor", "sample"))
    assert result["ok"], result
    return result


def _save(view, values, reset=False):
    return json.loads(task_settings.save_settings("editor", "sample", view["token"], json.dumps(values), reset))


def test_edit_user_isolated_and_saved_values_are_used_on_reload(editor):
    tasks = json.loads(task_settings.list_settings("editor"))["tasks"]
    assert [task["id"] for task in tasks] == ["sample"]
    view = _get()
    assert "desktop" not in [item["name"] for item in view["definitions"]]
    preview = json.loads(task_settings.preview_parameters("editor", "sample", json.dumps({**view["values"], "limit": "10"})))
    assert "text" not in preview["visible"]
    values = {**view["values"], "limit": "15", "mode": "b", "items": {"x": False, "y": True}, "text": "示例文本"}
    assert _save(view, values)["ok"]
    assert get_interface_store().get_active("user") == "runner"
    assert get_user_workflow_params("runner", "sample") is None
    assert get_wf_config("sample") == {"limit": 12, "desktop": 7}
    loaded = _get()
    assert loaded["source"] == "user"
    assert loaded["values"]["limit"] == 15
    assert loaded["values"]["mode"] == "b"
    assert loaded["values"]["items"] == {"x": False, "y": True}
    assert loaded["values"]["text"] == "示例文本"


def test_hidden_and_unowned_fields_preserved_while_visible_fields_merge(editor):
    set_user_workflow_params("editor", "sample", {"limit": 18, "desktop": 9, "internal": "keep"})
    set_user_workflow_params("editor", "other", {"keep": True})
    view = _get()
    values = {**view["values"], "claim": False, "limit": "", "desktop": 100, "internal": "overwrite"}
    preview = json.loads(task_settings.preview_parameters("editor", "sample", json.dumps(values)))
    assert "limit" not in preview["visible"]
    assert get_user_workflow_params("editor", "sample")["limit"] == 18
    assert _save(view, values)["ok"]
    saved = get_user_workflow_params("editor", "sample")
    assert saved["claim"] is False
    assert saved["limit"] == 18 and saved["desktop"] == 9 and saved["internal"] == "keep"
    assert get_user_workflow_params("editor", "other") == {"keep": True}


def test_bad_values_return_field_errors_without_writing(editor):
    view = _get()
    result = _save(view, {**view["values"], "limit": "21", "mode": "unknown", "items": {"unknown": True}})
    assert not result["ok"]
    assert set(result["errors"]) == {"limit", "mode", "items"}
    assert get_user_workflow_params("editor", "sample") is None


def test_running_and_paused_tasks_reject_save_and_reset(editor):
    view = _get()
    task_runner._STATE.begin("sample", "示例任务")
    assert _get()["readonly"]
    assert not _save(view, view["values"])["ok"]
    task_runner._STATE.request_pause()
    task_runner._STATE.acknowledge_pause()
    assert not _save(view, {}, reset=True)["ok"]
    assert get_user_workflow_params("editor", "sample") is None


def test_sync_or_other_editor_changes_reject_stale_draft(editor, monkeypatch):
    view = _get()
    monkeypatch.setattr(task_settings, "sync_status", lambda: {"synced": True, "synced_at": "second"})
    assert _save(view, view["values"])["conflict"]
    view = _get()
    set_user_workflow_params("editor", "sample", {"limit": 19})
    assert _save(view, {}, reset=True)["conflict"]
    assert get_user_workflow_params("editor", "sample")["limit"] == 19


def test_restore_shared_only_changes_selected_user_and_task(editor):
    set_user_workflow_params("editor", "sample", {"limit": 19})
    set_user_workflow_params("editor", "other", {"keep": True})
    assert _save(_get(), {}, reset=True)["ok"]
    assert _get()["source"] == "global" and _get()["values"]["limit"] == 12
    assert get_user_workflow_params("editor", "other") == {"keep": True}
    assert get_interface_store().get_active("user") == "runner"


def test_plugin_settings_adapter_keeps_execution_user(editor, monkeypatch):
    """类工作流通过 DEVICE_SETTINGS_MODULE 声明设备设置适配器；宿主只负责
    令牌、编辑隔离与执行用户不被切换，取值和落盘交给插件适配器。"""
    import sys
    from types import ModuleType

    from lvjiang.core.ondevice import plugins
    from lvjiang.workflows import implementations

    adapter = ModuleType("demo_app_device_settings")

    def get_settings(username):
        saved = get_user_workflow_params(username, "demo_task") or {}
        return {"kind": "demo", "values": {"level": 1, **saved}}

    def save_settings(username, values):
        set_user_workflow_params(username, "demo_task", {"level": int(values["level"])})

    adapter.get_settings = get_settings
    adapter.save_settings = save_settings
    monkeypatch.setitem(sys.modules, adapter.__name__, adapter)

    class DemoWorkflow:
        DEVICE_SETTINGS_MODULE = adapter.__name__

    monkeypatch.setattr(plugins, "ensure_loaded", lambda *_args: None)
    monkeypatch.setattr(implementations, "get_workflow_class", lambda _name: DemoWorkflow)
    monkeypatch.setattr(task_settings, "_adapter", _real_adapter)
    monkeypatch.setattr(task_settings, "_tasks", lambda: [
        {"id": "demo_task", "name": "示例插件任务", "class": "demo_task"}])
    listed = json.loads(task_settings.list_settings("editor"))["tasks"]
    assert listed == [{"id": "demo_task", "name": "示例插件任务", "dedicated": True, "source": "user"}]
    view = json.loads(task_settings.get_settings("editor", "demo_task"))
    assert view["ok"] and view["kind"] == "demo" and view["values"] == {"level": 1}
    result = json.loads(task_settings.save_settings(
        "editor", "demo_task", view["token"], json.dumps({"level": 3})))
    assert result["ok"], result
    assert get_user_workflow_params("editor", "demo_task") == {"level": 3}
    assert get_user_workflow_params("runner", "demo_task") is None
    assert get_interface_store().get_active("user") == "runner"
    view = json.loads(task_settings.get_settings("editor", "demo_task"))
    assert view["values"] == {"level": 3}
    # 用户专属配置没有「通用配置」可恢复。
    assert not json.loads(task_settings.save_settings("editor", "demo_task", view["token"], "{}", True))["ok"]


def test_one_missing_editor_module_does_not_hide_other_task_settings(editor, monkeypatch):
    def adapter(task):
        if task["id"] == "empty":
            raise ModuleNotFoundError("配置模块不可用")
        return None
    monkeypatch.setattr(task_settings, "_adapter", adapter)
    result = json.loads(task_settings.list_settings("editor"))
    assert result["ok"]
    assert result["tasks"][0]["id"] == "sample"
    assert "error" not in result["tasks"][0]
    assert "配置模块不可用" in result["tasks"][1]["error"]


def test_preserved_parameter_that_no_longer_fits_script_blocks_execution(editor):
    task = {"id": "sample", "parameters": editor}
    set_user_workflow_params("editor", "sample", {"mode": "removed_option"})
    with pytest.raises(ValueError, match="任务设置"):
        task_settings.validate_for_run(task, "editor")
