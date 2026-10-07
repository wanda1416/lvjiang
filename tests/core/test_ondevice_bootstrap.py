"""新装手机无需 PC：初始化、用户资料维护和任务入口共用同一套存储。"""
import json
import sqlite3
from contextlib import closing
from types import SimpleNamespace

import pytest

from lvjiang import constants
from lvjiang.core.config.session import get_session_store, reset_session_store
from lvjiang.core.ondevice import bootstrap, offline, task_runner, user_settings
from lvjiang.core.profile import repository
from lvjiang.core.system_preset import build_system_preset
from lvjiang.core.user_config import get_user_workflow_params, set_user_workflow_params


@pytest.fixture
def phone(tmp_path, monkeypatch):
    root = tmp_path / "phone"
    session = root / "config/session"
    monkeypatch.setattr(constants, "PROJECT_ROOT", root)
    monkeypatch.setattr(constants, "SESSION_CONFIG_DIR", session)
    monkeypatch.setattr(constants, "SESSION_PATH", session / "session.json")
    monkeypatch.setattr(constants, "USERS_DIR", session / "users")
    monkeypatch.setattr(repository, "_DB_PATH", session / "profile.db")
    repository.reset_profile_db()
    reset_session_store()
    monkeypatch.setattr(bootstrap, "reset_configuration", lambda: None)
    monkeypatch.setattr(task_runner, "_STATE", task_runner._TaskState())
    monkeypatch.setattr(task_runner, "_ENGINE", None)
    system = tmp_path / "system"
    system.mkdir()
    for name in ("app.yaml", "ocr.yaml", "layouts.yaml"):
        (system / name).write_text("{}")
    archive = tmp_path / "preset.zip"
    build_system_preset(system, archive)
    result = json.loads(bootstrap.initialize(str(archive)))
    assert result["ok"], result
    return session


def test_never_synced_phone_has_default_user_empty_database_and_can_start_task(phone, monkeypatch):
    status = offline.configuration_status()
    assert status["ready"] and not status["synced"]
    assert status["execution_username"] == "default" and status["layout"] == "android"
    assert json.loads(task_runner.list_users())["users"] == ["default"]
    with closing(sqlite3.connect(phone / "profile.db")) as db:
        assert db.execute("SELECT COUNT(*) FROM profile_entries").fetchone() == (0,)
    task = {"id": "sample", "name": "Sample", "parameters": [], "class": ""}
    monkeypatch.setattr(task_runner, "_resolve_task", lambda _id: task)
    monkeypatch.setattr(task_runner, "_build_source", lambda *_: "collect $result = 1")
    engine = SimpleNamespace(execute=lambda *_args, **_kwargs: {"result": 1})
    monkeypatch.setattr(task_runner, "_get_engine", lambda: engine)
    monkeypatch.setattr("lvjiang.core.ondevice.plugins.ensure_loaded", lambda: None)
    monkeypatch.setattr(task_runner, "list_tasks", lambda **_: json.dumps({
        "ok": True, "tasks": [task]}))
    runtime = json.loads(offline.check_runtime(ocr=False))
    assert runtime["ok"] and runtime["sync"]["ready"]
    assert not runtime["sync"]["synced"]
    monkeypatch.setattr("lvjiang.core.ondevice.a11y.is_ready", lambda: True)
    assert json.loads(task_runner.start_task("sample"))["ok"]
    task_runner._STATE._thread.join(2)
    assert task_runner._STATE.snapshot()["state"] == "done"
    assert engine.run_username == "default"


def test_phone_user_creation_and_attributes_do_not_switch_execution_user_or_overwrite_parameters(phone):
    assert json.loads(user_settings.create_user("second"))["ok"]
    assert get_session_store().get_active("user") == "default"
    set_user_workflow_params("second", "task", {"keep": True})
    assert json.loads(user_settings.save_attributes("second", '{"角色":"示例"}', "{}"))["ok"]
    assert json.loads(user_settings.get_user("second"))["attributes"] == {"角色": "示例"}
    assert get_user_workflow_params("second", "task") == {"keep": True}
    assert get_session_store().get_active("user") == "default"
    task_runner._STATE.begin("sample", "Sample")
    assert not json.loads(user_settings.create_user("third"))["ok"]
    assert not json.loads(user_settings.save_attributes("second", "{}", "{}"))["ok"]
