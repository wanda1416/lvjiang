"""手机任务设置里自动调律走 yysls 的设备设置适配器（tuning_config）。

宿主的适配器协议由 ``tests/core/ondevice/test_ondevice_task_settings.py`` 用假
适配器覆盖；这里验证调律适配器真实的取值、保存与执行用户隔离。
"""
import json

from lvjiang.core.config.interface import get_interface_store
from lvjiang.core.ondevice import task_settings
from lvjiang.core.users.document import (
    get_user_workflow_params,
    set_user_workflow_params,
)
from tests.core.ondevice.test_ondevice_task_settings import (
    editor,  # noqa: F401 - 共用夹具
)


def test_tuning_adapter_keeps_execution_user_and_desktop_parameters(editor, monkeypatch):  # noqa: F811
    from lvjiang.apps.yysls.ondevice import tuning_config
    from lvjiang.core.ondevice import plugins

    monkeypatch.setattr(plugins, "ensure_loaded", lambda *_args: None)
    monkeypatch.setattr(task_settings, "_adapter", lambda _task: tuning_config)
    monkeypatch.setattr(task_settings, "_tasks", lambda: [{"id": "auto_tuning", "name": "自动调律"}])
    set_user_workflow_params("editor", "auto_tuning", {"skip_tuning": True})
    view = json.loads(task_settings.get_settings("editor", "auto_tuning"))
    assert view["ok"] and view["kind"] == "tuning"
    values = {**view["values"], "selected_slots": ["ring"]}
    values["rules"]["huiyi_general"]["enabled"] = True
    result = json.loads(task_settings.save_settings("editor", "auto_tuning", view["token"], json.dumps(values)))
    assert result["ok"], result
    assert get_user_workflow_params("editor", "auto_tuning")["skip_tuning"] is True
    assert get_user_workflow_params("runner", "auto_tuning") is None
    assert get_interface_store().get_active("user") == "runner"
    view = json.loads(task_settings.get_settings("editor", "auto_tuning"))
    assert not json.loads(task_settings.save_settings("editor", "auto_tuning", view["token"], "{}", True))["ok"]
