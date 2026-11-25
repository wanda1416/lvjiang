"""历史回传保护数据归属、去重、附件完整性与独立选择。"""
import json
import sqlite3
import zipfile
from contextlib import closing
from dataclasses import replace
from datetime import datetime

import pytest

from lvjiang.core.history.daily import (
    BatchRunSession,
    TaskHistoryRepository,
    TaskRunSession,
)
from lvjiang.core.history.transfer import (
    HistoryChannel,
    HistoryTable,
    export_history,
    import_history,
)
from tests.fake_plugin import fake_hooks, use_registered_hooks

#: 假插件声明的历史类别：两张表、一个报告附件列、自己的附件目录。
DEMO = "demo_app.records"


def _initialize_demo(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.execute("CREATE TABLE IF NOT EXISTS demo_runs (run_id TEXT PRIMARY KEY, "
                     "status TEXT, finished_at TEXT, report_path TEXT, note TEXT)")
        conn.execute("CREATE TABLE IF NOT EXISTS demo_items (id INTEGER PRIMARY KEY AUTOINCREMENT, "
                     "run_id TEXT, sequence_id INTEGER, rounds INTEGER)")


_FINISHED = "status != 'running' AND finished_at != ''"
DEMO_CHANNEL = HistoryChannel(DEMO, "示例记录", "demo_history.db", _initialize_demo, (
    HistoryTable("demo_runs", ("run_id",), _FINISHED, ("report_path",)),
    HistoryTable("demo_items", ("run_id", "sequence_id"),
                 f"run_id IN (SELECT run_id FROM demo_runs WHERE {_FINISHED})", omitted=("id",)),
), attachment_prefixes=("logs/demo/",))


def _use_demo_plugin(monkeypatch):
    use_registered_hooks(monkeypatch, fake_hooks(history_channels=lambda: (DEMO_CHANNEL,)))


@pytest.fixture
def histories(tmp_path, monkeypatch):
    _use_demo_plugin(monkeypatch)
    phone = tmp_path / "phone"
    pc = tmp_path / "pc"
    monkeypatch.setattr("lvjiang.constants.PROJECT_ROOT", phone)
    repo = TaskHistoryRepository(phone / "config/session/daily_history.db")
    batch = BatchRunSession(config_name="手机批次", input_snapshot={}, repository=repo,
                            target_id="ondevice", target_kind="android")
    task = TaskRunSession(username="手机用户", task_id="daily", task_name="任务", task_scope="daily",
                          params={}, source="ondevice", repository=repo, batch_run_id=batch.batch_run_id,
                          started_at=datetime(2026, 1, 1), target_id="ondevice", target_kind="android",
                          log_root=phone / "logs/daily")
    task.log_path.write_text("手机日志\n", encoding="utf-8")
    result = phone / "logs/output/手机用户/2026-01/01/result.json"
    result.parent.mkdir(parents=True)
    result.write_text('{"结果": 1}\n', encoding="utf-8")
    task.finish(status="completed", result_path=result)
    report = phone / "logs/batch/report.md"
    report.parent.mkdir(parents=True)
    report.write_text("批次报告", encoding="utf-8")
    batch.finish(status="completed", report_path=report)
    demo_db = phone / "config/session/demo_history.db"
    _initialize_demo(demo_db)
    doc = phone / "logs/demo/report.md"
    doc.parent.mkdir(parents=True)
    doc.write_text("插件报告", encoding="utf-8")
    with closing(sqlite3.connect(demo_db)) as conn, conn:
        # 旧记录存绝对路径：导出时必须规范化成相对路径。
        conn.execute("INSERT INTO demo_runs VALUES ('demo-phone', 'completed', '2026-01-01', ?, '完成')",
                     (str(doc),))
        conn.execute("INSERT INTO demo_items (run_id, sequence_id, rounds) VALUES ('demo-phone', 1, 2)")
    # 旧下发带来的 PC 记录不能作为手机独立执行历史回传。
    record = repo.list_task_runs()[0]
    repo.create_task_run(replace(record, task_run_id="pc-only", source="single", batch_run_id=""))
    yield phone, pc, task, batch


def test_roundtrip_keeps_details_and_paths_of_plugin_channel(histories, tmp_path):
    phone, pc, task, batch = histories
    archive = tmp_path / "history.zip"
    selected = ["tasks", DEMO, "logs"]
    export_history(phone, archive, selected)
    first = import_history(pc, archive, selected)
    assert not first["errors"] and not first["missing"]
    assert first["imported"] == {"tasks": 2, DEMO: 2}
    assert first["files"] == 4
    again = import_history(pc, archive, selected)
    assert again["imported"] == {"tasks": 0, DEMO: 0}
    assert again["files"] == 0
    records = TaskHistoryRepository(pc / "config/session/daily_history.db").list_task_runs()
    assert [r.task_run_id for r in records] == [task.task_run_id]
    assert records[0].batch_run_id == batch.batch_run_id
    assert (pc / records[0].log_path).read_text(encoding="utf-8") == "手机日志\n"
    assert json.loads((pc / records[0].result_path).read_text(encoding="utf-8")) == {"结果": 1}
    with closing(sqlite3.connect(pc / "config/session/demo_history.db")) as conn:
        assert conn.execute("SELECT sequence_id, rounds FROM demo_items").fetchall() == [(1, 2)]
        assert conn.execute("SELECT report_path FROM demo_runs").fetchone()[0] == "logs/demo/report.md"
    assert (pc / "logs/demo/report.md").read_text(encoding="utf-8") == "插件报告"
    assert not (pc / "config/session/users").exists()
    assert not (pc / "config/session/profile.db").exists()


def test_legacy_session_output_attachment_is_still_accepted(tmp_path, monkeypatch):
    """旧版产出在 config/session/output；两端混用时回传仍须接受该前缀。"""
    _use_demo_plugin(monkeypatch)
    phone = tmp_path / "phone"
    monkeypatch.setattr("lvjiang.constants.PROJECT_ROOT", phone)
    repository = TaskHistoryRepository(phone / "config/session/daily_history.db")
    task = TaskRunSession(username="手机用户", task_id="daily", task_name="旧任务",
                          task_scope="daily", params={}, source="ondevice",
                          repository=repository, started_at=datetime(2026, 1, 1),
                          target_id="ondevice", target_kind="android",
                          log_root=phone / "logs/daily")
    task.log_path.write_text("手机日志\n", encoding="utf-8")
    legacy = phone / "config/session/output/手机用户/2026-01/01/result.json"
    legacy.parent.mkdir(parents=True)
    legacy.write_text('{"旧": 1}\n', encoding="utf-8")
    task.finish(status="completed", result_path=legacy)

    archive = tmp_path / "legacy.zip"
    export_history(phone, archive, ["tasks", "logs"])
    with zipfile.ZipFile(archive) as package:
        assert "config/session/output/手机用户/2026-01/01/result.json" in package.namelist()
    outcome = import_history(tmp_path / "pc", archive, ["tasks", "logs"])
    assert not outcome["errors"] and not outcome["missing"]
    assert outcome["files"] == 2


def test_logs_are_independent_and_missing_files_are_reported(histories, tmp_path):
    phone, pc, task, _ = histories
    archive = tmp_path / "logs.zip"
    # 单独执行在库中以 NULL 表示没有所属批次，不能被终态查询漏掉。
    with closing(sqlite3.connect(phone / "config/session/daily_history.db")) as conn, conn:
        conn.execute("UPDATE task_runs SET batch_run_id=NULL WHERE task_run_id=?", (task.task_run_id,))
    export_history(phone, archive, ["logs"])
    assert import_history(pc, archive, ["logs"])["files"] == 1
    assert not (pc / "config/session/daily_history.db").exists()
    task.log_path.unlink()
    export_history(phone, archive, ["tasks", "logs"])
    outcome = import_history(pc, archive, ["tasks", "logs"])
    assert len(outcome["missing"]) == 1
    assert outcome["imported"]["tasks"] == 2


def test_same_path_different_content_never_overwrites_pc(histories, tmp_path):
    phone, pc, task, _ = histories
    target = pc / task.log_path.relative_to(phone)
    target.parent.mkdir(parents=True)
    target.write_text("PC 原内容", encoding="utf-8")
    archive = tmp_path / "history.zip"
    export_history(phone, archive, ["tasks", "logs"])
    with pytest.raises(ValueError, match="同名附件"):
        import_history(pc, archive, ["tasks", "logs"])
    assert target.read_text(encoding="utf-8") == "PC 原内容"
    assert not (pc / "config/session/daily_history.db").exists()


def test_unfinished_batch_waits_and_conflicting_category_does_not_block_others(histories, tmp_path):
    phone, pc, _, batch = histories
    archive = tmp_path / "history.zip"
    with closing(sqlite3.connect(phone / "config/session/daily_history.db")) as conn, conn:
        conn.execute("UPDATE batch_runs SET status='running', finished_at='' WHERE batch_run_id=?", (batch.batch_run_id,))
    export_history(phone, archive, ["tasks", DEMO])
    outcome = import_history(pc, archive, ["tasks", DEMO])
    assert outcome["imported"]["tasks"] == 0
    assert outcome["imported"][DEMO] == 2
    batch.finish(status="completed")
    export_history(phone, archive, ["tasks", DEMO])
    with closing(sqlite3.connect(pc / "config/session/demo_history.db")) as conn, conn:
        conn.execute("UPDATE demo_runs SET note='PC 保留'")
    outcome = import_history(pc, archive, ["tasks", DEMO])
    assert outcome["imported"]["tasks"] == 2
    assert DEMO in outcome["errors"]


def test_archive_rejects_unrequested_category_and_path_escape(histories, tmp_path):
    phone, pc, _, _ = histories
    archive = tmp_path / "history.zip"
    export_history(phone, archive, ["logs"])
    with pytest.raises(ValueError, match="类别"):
        import_history(pc, archive, ["tasks"])
    with zipfile.ZipFile(archive) as package:
        payload = json.loads(package.read("manifest.json"))
    payload["files"] = {"../outside.txt": "invalid"}
    with zipfile.ZipFile(archive, "w") as package:
        package.writestr("manifest.json", json.dumps(payload))
        package.writestr("../outside.txt", "bad")
    with pytest.raises(ValueError, match="路径"):
        import_history(pc, archive, ["logs"])
    assert not (pc.parent / "outside.txt").exists()


def test_rpc_retry_and_cancel_do_not_import_partial_history(histories, monkeypatch):
    from lvjiang.core.android.history_transfer import pull_history
    from lvjiang.core.ondevice import history_transfer as device

    phone, pc, _, _ = histories
    monkeypatch.setattr("lvjiang.core.ondevice.plugins.ensure_loaded", lambda: None)

    class Agent:
        status = {"history_protocol": 1}
        fail_chunk = True

        def call(self, operation, timeout=None, **params):
            if operation == "history_chunk" and self.fail_chunk:
                raise ConnectionError("模拟断线")
            response = json.loads(device.request(operation.removeprefix("history_"), json.dumps(params)))
            if not response["ok"]:
                raise RuntimeError(response["error"])
            return response, b""

    agent = Agent()
    with pytest.raises(ConnectionError):
        pull_history(agent, pc, ["tasks"])
    assert not (pc / "config/session/daily_history.db").exists()
    agent.fail_chunk = False
    with pytest.raises(RuntimeError, match="取消"):
        pull_history(agent, pc, ["tasks"], cancelled=lambda: True)
    assert not (pc / "config/session/daily_history.db").exists()
    assert pull_history(agent, pc, ["tasks"])["imported"]["tasks"] == 2
    assert pull_history(agent, pc, ["tasks"])["imported"]["tasks"] == 0
    # 收发包不进 config/session，两端暂存目录用完即删
    assert not (pc / "config/session/history-transfer").exists()
    assert not list(phone.glob(".history-stage-*"))


def test_running_task_is_skipped_while_transfer_continues(histories, monkeypatch):
    """任务运行中仍可回传：运行中的记录由导出查询跳过，不影响其他记录。"""
    from lvjiang.core.android.history_transfer import pull_history
    from lvjiang.core.ondevice import history_transfer as device

    phone, pc, task, _ = histories
    running = TaskRunSession(
        username="手机用户", task_id="daily", task_name="运行中任务", task_scope="daily",
        params={}, source="ondevice",
        repository=TaskHistoryRepository(phone / "config/session/daily_history.db"),
        started_at=datetime(2026, 1, 2), target_id="ondevice", target_kind="android",
        log_root=phone / "logs/daily")
    monkeypatch.setattr("lvjiang.core.ondevice.plugins.ensure_loaded", lambda: None)

    class Agent:
        status = {"history_protocol": 1}

        def call(self, operation, timeout=None, **params):
            response = json.loads(device.request(operation.removeprefix("history_"), json.dumps(params)))
            if not response["ok"]:
                raise RuntimeError(response["error"])
            return response, b""

    outcome = pull_history(Agent(), pc, ["tasks"])
    assert outcome["imported"]["tasks"] == 2
    records = TaskHistoryRepository(pc / "config/session/daily_history.db").list_task_runs()
    assert [r.task_run_id for r in records] == [task.task_run_id]
    assert running.task_run_id not in {r.task_run_id for r in records}


def test_plugin_attachment_prefixes_are_declared_by_channels(tmp_path):
    """宿主只认识自己的附件落点；插件目录由插件 HistoryChannel 自己声明。"""
    from lvjiang.core.history.transfer import _attachment, attachment_prefixes

    host_only = attachment_prefixes({})
    assert "logs/demo/" not in host_only
    with pytest.raises(ValueError):
        _attachment(tmp_path, "logs/demo/report.md", host_only)

    prefixes = attachment_prefixes({DEMO: DEMO_CHANNEL})
    assert "logs/demo/" in prefixes
    assert _attachment(tmp_path, "logs/demo/report.md", prefixes)[0] == "logs/demo/report.md"

    fake = HistoryChannel("x", "x", "x.db", lambda _p: None, (), attachment_prefixes=("logs/x/",))
    assert attachment_prefixes({"x": fake})[-1] == "logs/x/"


def test_plugin_channels_come_from_registered_hooks(monkeypatch):
    """历史类别 = 宿主 tasks + 已装配插件声明的类别；重复 key 直接拒绝。"""
    from lvjiang.core.history.transfer import history_channels

    use_registered_hooks(monkeypatch)
    assert list(history_channels()) == ["tasks"]
    _use_demo_plugin(monkeypatch)
    assert list(history_channels()) == ["tasks", DEMO]
    use_registered_hooks(monkeypatch, fake_hooks(history_channels=lambda: (DEMO_CHANNEL,)),
                         fake_hooks(id="other_app", history_channels=lambda: (DEMO_CHANNEL,)))
    with pytest.raises(ValueError, match="重复"):
        history_channels()
