"""工作台持续契约：只读浏览、任务可见性与历史来源隔离。"""
import json

from lvjiang.core.ondevice import task_runner, workbench


def test_catalog_keeps_parameterless_and_invalid_tasks_without_selecting_user(monkeypatch):
    from lvjiang.core.config.session import get_session_store
    from lvjiang.core.ondevice import task_settings

    get_session_store().set_active("user", "runner")
    monkeypatch.setattr("lvjiang.core.ondevice.plugins.ensure_loaded", lambda: None)
    monkeypatch.setattr("lvjiang.workflows.discovery.list_exposed_scripts", lambda *a, **kw: [
        {"id": "plain", "name": "无需参数", "parameters": []},
        {"id": "invalid", "name": "尚未配置", "parameters": []},
    ])

    def validate(task, username):
        assert username == "viewer"
        if task["id"] == "invalid":
            raise ValueError("请先配置任务")

    monkeypatch.setattr(task_settings, "validate_for_run", validate)
    result = json.loads(workbench.task_catalog("viewer"))
    assert result["ok"]
    assert [t["id"] for t in result["tasks"]] == ["plain", "invalid"]
    assert not result["tasks"][0]["configurable"]
    assert result["tasks"][1]["reason"] == "请先配置任务"
    assert get_session_store().get_active("user") == "runner"


def test_profile_distinguishes_zero_from_unknown_and_browsing_is_readonly(monkeypatch):
    from lvjiang.core.config.session import get_session_store
    from lvjiang.core.profile import schema, service
    from lvjiang.core.profile.models import KeyDef
    from lvjiang.core.profile.schema import ProfileSchema

    get_session_store().set_active("user", "runner")
    monkeypatch.setattr(task_runner, "_local_user_names", lambda: ["viewer", "runner"])
    config = ProfileSchema(keys_by_model={"stock": [KeyDef(key="zero"), KeyDef(key="unknown")]})
    monkeypatch.setattr(schema, "get_profile_config", lambda: config)
    monkeypatch.setattr(service, "profile_read_all", lambda username: {"stock": {"zero": {"value": 0}}})
    result = json.loads(workbench.profile_view("viewer"))
    assert result["ok"]
    assert [(i["key"], i["value"], i["collected"]) for i in result["items"]] == [
        ("zero", 0, True), ("unknown", None, False),
    ]
    assert get_session_store().get_active("user") == "runner"


def test_profile_history_passes_user_and_page_without_writing(monkeypatch):
    from lvjiang.core.profile import repository, schema
    from lvjiang.core.profile.models import KeyDef
    from lvjiang.core.profile.schema import ProfileSchema

    monkeypatch.setattr(task_runner, "_local_user_names", lambda: ["viewer"])
    monkeypatch.setattr(schema, "get_profile_config", lambda: ProfileSchema(keys_by_model={"stock": [KeyDef(key="count")]}))
    calls = []
    monkeypatch.setattr(repository, "db_get_history", lambda *args, **kw: calls.append((args, kw)) or [{"id": 51}])
    monkeypatch.setattr(repository, "db_count_history", lambda *args: 51)
    data = json.loads(workbench.profile_history("viewer", "stock", "count", 50))
    assert data == {"ok": True, "records": [{"id": 51}], "total": 51}
    assert calls == [(("viewer", "stock", "count"), {"limit": 50, "offset": 50})]
    assert not json.loads(workbench.profile_history("missing", "stock", "count"))["ok"]


def test_recovery_only_marks_interrupted_ondevice_runs(tmp_path, monkeypatch):
    from lvjiang.core.daily_history import TaskHistoryRepository, TaskRunSession

    repository = TaskHistoryRepository(tmp_path / "history.db")
    for source in ("single", "ondevice"):
        TaskRunSession(username="tester", task_id="task", task_name="测试任务", task_scope="daily",
                       params={}, source=source, repository=repository, log_root=tmp_path / "logs")
    monkeypatch.setattr(task_runner, "_STATE", task_runner._TaskState())
    workbench.recover_interrupted(repository)
    assert repository.list_task_runs(source="single")[0].status == "running"
    recovered = repository.list_task_runs(source="ondevice")[0]
    assert recovered.status == "failed" and "结果待核对" in recovered.error_message
    monkeypatch.setattr(workbench, "TaskHistoryRepository", lambda: repository)
    assert len(json.loads(workbench.task_history())["records"]) == 1
    detail = json.loads(workbench.task_history(recovered.task_run_id))
    assert detail["ok"] and detail["record"]["username"] == "tester"


def test_notices_fall_back_to_cached_applicable_content(monkeypatch):
    from lvjiang.core import announcement, version_info

    manifest = announcement.AnnouncementManifest(1, 1, "", (
        announcement.Announcement("visible", "info", "可见公告", "正文"),
        announcement.Announcement("future", "info", "未来公告", "正文", min_app_version="9.0.0"),
    ))
    monkeypatch.setattr(announcement, "load_cached_manifest", lambda: manifest)
    monkeypatch.setattr(announcement, "get_cached_etag", lambda: "")
    monkeypatch.setattr(version_info, "get_version", lambda: "1.0.0")

    def offline(**kwargs):
        raise announcement.AnnouncementNetworkError("网络不可用")

    monkeypatch.setattr(announcement, "fetch_announcement_manifest", offline)
    result = json.loads(workbench.public_content("notices"))
    assert result["ok"] and "显示缓存" in result["message"]
    assert [item["id"] for item in result["notices"]] == ["visible"]
