"""工作台持续契约：只读浏览、任务可见性与历史来源隔离。"""
import base64
import json

from lvjiang.core.ondevice import task_runner, workbench


def test_qrcode_refresh_is_explicit_and_keeps_cache_on_failure(tmp_path, monkeypatch):
    from lvjiang.core import group_qrcode
    monkeypatch.setattr("lvjiang.constants.SESSION_CONFIG_DIR", tmp_path)
    group_qrcode.save_qrcode(b"old")
    monkeypatch.setattr(group_qrcode, "fetch_qrcode_bytes", lambda: b"latest")
    cached = json.loads(workbench.public_content("qrcode"))
    assert base64.b64decode(cached["image"]) == b"old"
    refreshed = json.loads(workbench.public_content("qrcode", True))
    assert base64.b64decode(refreshed["image"]) == b"latest"
    def unavailable():
        raise group_qrcode.QrCodeError("请检查网络")
    monkeypatch.setattr(group_qrcode, "fetch_qrcode_bytes", unavailable)
    failed = json.loads(workbench.public_content("qrcode", True))
    assert failed["ok"] and "请检查网络" in failed["message"]
    assert base64.b64decode(failed["image"]) == b"latest"


def test_catalog_keeps_parameterless_and_invalid_tasks_without_selecting_user(monkeypatch):
    from lvjiang.core.config.interface import get_interface_store
    from lvjiang.core.ondevice import task_settings

    get_interface_store().set_active("user", "runner")
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
    assert get_interface_store().get_active("user") == "runner"


def test_profile_distinguishes_zero_from_unknown_and_browsing_is_readonly(monkeypatch):
    from lvjiang.core.config.interface import get_interface_store
    from lvjiang.core.profile import schema, service
    from lvjiang.core.profile.models import KeyDef
    from lvjiang.core.profile.schema import ProfileSchema

    get_interface_store().set_active("user", "runner")
    monkeypatch.setattr(task_runner, "_local_user_names", lambda: ["viewer", "runner"])
    config = ProfileSchema(keys_by_model={"stock": [KeyDef(key="zero"), KeyDef(key="unknown")]})
    monkeypatch.setattr(schema, "get_profile_config", lambda: config)
    monkeypatch.setattr(service, "profile_read_all", lambda username: {"stock": {"zero": {"value": 0}}})
    result = json.loads(workbench.profile_view("viewer"))
    assert result["ok"]
    assert [(i["key"], i["value"], i["collected"]) for i in result["items"]] == [
        ("zero", 0, True), ("unknown", None, False),
    ]
    assert get_interface_store().get_active("user") == "runner"


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


def test_profile_all_users_groups_values_and_totals(monkeypatch):
    from lvjiang.core.profile import schema, service
    from lvjiang.core.profile.models import NoteKeyDef, StockKeyDef
    from lvjiang.core.profile.schema import ProfileSchema

    monkeypatch.setattr(task_runner, "_local_user_names", lambda: ["甲", "乙"])
    config = ProfileSchema(keys_by_model={
        "stock": [StockKeyDef(key="huarui", label="花蕊织")],
        "note": [NoteKeyDef(key="build", label="配装定位")],
    })
    monkeypatch.setattr(schema, "get_profile_config", lambda: config)

    def read_all(username):
        if username == "甲":
            return {
                "stock": {"huarui": {"value": 12}},
                "note": {"build": {"value_text": "主玩会心双刀"}},
            }
        return {"stock": {"huarui": {"value": 8}}}

    monkeypatch.setattr(service, "profile_read_all", read_all)
    result = json.loads(workbench.profile_all_users())
    assert result["ok"]
    assert result["users"] == ["甲", "乙"]

    stock = next(g for g in result["groups"] if g["title"] == "库存 · 默认")
    item = stock["items"][0]
    assert (item["key"], item["label"], item["total"]) == ("huarui", "花蕊织", "20")
    assert [(v["user"], v["value"], v["collected"]) for v in item["values"]] == [
        ("甲", "12", True), ("乙", "8", True),
    ]

    note = next(g for g in result["groups"] if g["title"] == "备注 · 默认")
    note_item = note["items"][0]
    assert "total" not in note_item
    assert [(v["user"], v["value"], v["collected"]) for v in note_item["values"]] == [
        ("甲", "主玩会心双刀", True), ("乙", None, False),
    ]


def test_profile_all_users_filters_single_key(monkeypatch):
    from lvjiang.core.profile import schema, service
    from lvjiang.core.profile.models import NoteKeyDef, StockKeyDef
    from lvjiang.core.profile.schema import ProfileSchema

    monkeypatch.setattr(task_runner, "_local_user_names", lambda: ["甲", "乙"])
    config = ProfileSchema(keys_by_model={
        "stock": [StockKeyDef(key="huarui", label="花蕊织")],
        "note": [NoteKeyDef(key="build", label="配装定位")],
    })
    monkeypatch.setattr(schema, "get_profile_config", lambda: config)

    def read_all(username):
        if username == "甲":
            return {
                "stock": {"huarui": {"value": 12}},
                "note": {"build": {"value_text": "主玩会心双刀"}},
            }
        return {"stock": {"huarui": {"value": 8}}}

    monkeypatch.setattr(service, "profile_read_all", read_all)
    result = json.loads(workbench.profile_all_users("stock", "huarui"))
    assert result["ok"]
    # 只返回该 key 所在分组，note 项不出现
    assert [g["title"] for g in result["groups"]] == ["库存 · 默认"]
    item = result["groups"][0]["items"][0]
    assert item["key"] == "huarui"
    assert item["total"] == "20"


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


def test_batch_task_detail_is_available_outside_recent_page(tmp_path, monkeypatch):
    from datetime import datetime, timedelta

    from lvjiang.core.daily_history import TaskHistoryRepository, TaskRunSession
    repository = TaskHistoryRepository(tmp_path / "history.db")
    oldest = None
    for number in range(101):
        run = TaskRunSession(username="viewer", task_id="task", task_name="测试任务", task_scope="daily", params={}, source="ondevice", repository=repository, log_root=tmp_path / "logs", started_at=datetime(2026, 1, 1) + timedelta(seconds=number))
        run.finish(status="completed")
        if number == 0:
            oldest = run.task_run_id
    monkeypatch.setattr(workbench, "TaskHistoryRepository", lambda: repository)
    monkeypatch.setattr(task_runner, "_STATE", task_runner._TaskState())
    assert len(json.loads(workbench.task_history())["records"]) == 100
    assert json.loads(workbench.task_history(oldest))["record"]["task_run_id"] == oldest
