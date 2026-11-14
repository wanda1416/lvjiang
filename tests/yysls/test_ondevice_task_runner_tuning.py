"""设备端任务运行器上的自动调律：任务列表曝光与用户配置冻结。

宿主机制（类工作流的设备可见性、CONFIG_SNAPSHOT_LOADER）由
``tests/core/ondevice/test_task_runner.py`` 用假工作流覆盖；这里验证
``AutoTuningWorkflow`` 自己的声明（显示名、设备可见、按用户配置冻结）。
"""
import json

from lvjiang.core.ondevice import task_runner
from tests.core.ondevice.test_task_runner import (  # noqa: F401 - 共用夹具与替身
    _clean_state,
    _fake_tasks,
    _FakeEngine,
    _patch_discovery,
    _patch_engine,
    _patch_source,
    _status,
    _wait_until,
)


def test_device_lists_tuning_without_exposing_other_dedicated_tasks(monkeypatch):
    from lvjiang.apps.yysls.workflows.implementations.auto_tuning import (
        AutoTuningWorkflow,
    )
    from lvjiang.workflows import discovery, implementations
    from lvjiang.workflows.preferences import DailyScriptPrefs

    monkeypatch.setattr(implementations, "list_workflows", lambda: ["auto_tuning"])
    monkeypatch.setattr(implementations, "get_workflow_class", lambda _name: AutoTuningWorkflow)
    monkeypatch.setattr(discovery, "_discover_wf_scripts", lambda *_: {
        "helper": {"id": "helper", "scope": "dedicated", "source_layer": "system"},
        "pc_only": {"id": "pc_only", "env": ["windows"], "source_layer": "system"},
    })
    monkeypatch.setattr(discovery, "load_preferences", lambda: DailyScriptPrefs([], {}, {}))
    monkeypatch.setattr("lvjiang.core.ondevice.plugins.ensure_loaded", lambda: None)

    assert "auto_tuning" not in [item["id"] for item in discovery.list_exposed_scripts("android")]
    tasks = json.loads(task_runner.list_tasks())["tasks"]
    assert tasks == [{"id": "auto_tuning", "name": "自动调律", "source": "class"}]
    monkeypatch.setattr(discovery, "load_preferences",
                        lambda: DailyScriptPrefs([], {"auto_tuning": False}, {}))
    assert json.loads(task_runner.list_tasks())["tasks"] == []


def test_tuning_freezes_synced_user_config_instead_of_shared_config(monkeypatch):
    from lvjiang import constants
    from lvjiang.apps.yysls.workflows.implementations.auto_tuning import (
        AutoTuningWorkflow,
    )
    from lvjiang.core.config.wf_configs import set_wf_config
    from lvjiang.core.user_config import set_user_workflow_params

    _patch_discovery(monkeypatch, _fake_tasks({"id": "auto_tuning", "class": "auto_tuning"}))
    monkeypatch.setattr("lvjiang.workflows.implementations.get_workflow_class",
                        lambda _name: AutoTuningWorkflow)
    set_wf_config("auto_tuning", {"selected_slots": ["head"]})
    saved = {"selected_slots": ["ring"], "rules": {"huiyi_general": {"enabled": True}}}
    set_user_workflow_params("tester", "auto_tuning", saved, constants.USERS_DIR)
    engine = _FakeEngine("ok")
    _patch_engine(monkeypatch, engine)
    _patch_source(monkeypatch)

    assert json.loads(task_runner.start_task("auto_tuning"))["ok"]
    assert _wait_until(lambda: _status()["state"] == task_runner.STATE_DONE)
    snapshot = engine.workflow_config_snapshot
    assert snapshot["selected_slots"] == ["ring"]
    assert snapshot["rules"] == saved["rules"]
    set_user_workflow_params("tester", "auto_tuning", {"selected_slots": ["chest"]}, constants.USERS_DIR)
    assert snapshot["selected_slots"] == ["ring"]
