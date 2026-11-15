"""在 config/session 的隔离副本上验证三个 SQLite 库的启动、读写、建表与迁移。

用法::

    python scripts/one_off/verify_db_storage_scenarios.py            # 保留副本与报告
    python scripts/one_off/verify_db_storage_scenarios.py --clean    # 验完删掉副本

为什么要副本：``LVJIANG_ROOT`` 决定 ``config/session`` 的位置，脚本把它指向
复制出来的临时客户端根，因此产品代码按真实路径解析到的三个库都在副本里。
真实 ``config/session`` 全程只读，运行前后各取一次摘要做比对。

覆盖场景：
1. 启动：三个库各自的默认路径、schema 版本、表结构；
2. 读写：Profile 数值/账本/锁定/CAS/同步簿记、任务历史、调律历史与遥测；
3. 建表：空目录首次建库，把结构与副本迁移后的结构逐字段比对；
4. 迁移：构造 v10 / v1 / v1 旧库再升级，验证版本推进、数据保留、升级前备份；
5. 复核：真实 config/session 未被改动。

报告写到副本根下的 ``verify-report.json``（UTF-8、两空格缩进、末尾换行）。
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import sqlite3
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

#: 复制副本时跳过的目录：与数据库无关的体积项（历史备份、截图、头像）。
SKIP_DIRS = {"_backup", "screenshots", "avatars", ".locks", "__pycache__"}

#: 每个库需要核对结构的表。
TABLES = {
    "profile.db": ("profile_entries", "profile_history"),
    "daily_history.db": ("batch_runs", "task_runs"),
    "tuning_history.db": ("tuning_runs", "tuning_equipment", "telemetry_deliveries"),
}


class Report:
    """收集检查项；控制台只打 ASCII 结果，细节留在 JSON 里。"""

    def __init__(self) -> None:
        self.phase_name = ""
        self.checks: list[dict] = []
        self.facts: dict = {}

    def phase(self, name: str) -> None:
        self.phase_name = name
        print(f"\n[{name}]", flush=True)

    def check(self, ok: bool, check_id: str, detail: str = "") -> bool:
        self.checks.append({
            "phase": self.phase_name, "id": check_id,
            "ok": bool(ok), "detail": detail,
        })
        print(f"  {'PASS' if ok else 'FAIL'}  {check_id}", flush=True)
        return bool(ok)

    @property
    def failures(self) -> list[dict]:
        return [item for item in self.checks if not item["ok"]]

    def fact(self, key: str, value) -> None:
        self.facts[key] = value


# ─── 基础工具 ────────────────────────────────────────────────


def digest(path: Path) -> str:
    if not path.is_file():
        return ""
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1 << 20):
            hasher.update(chunk)
    return hasher.hexdigest()


def fingerprint(path: Path, tables: tuple[str, ...], *, with_rows: bool = False) -> dict:
    """表结构（列 + 索引）；``with_rows`` 时才带行数。

    新建库与迁移库比对时不能带行数：一个是空库，一个是装着真实数据的库。
    """
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as conn:
        result: dict = {"tables": {}}
        for table in tables:
            columns = [
                {"name": row[1], "type": row[2], "notnull": row[3],
                 "default": row[4], "pk": row[5]}
                for row in conn.execute(f'PRAGMA table_info("{table}")')
            ]
            indexes = []
            for row in conn.execute(f'PRAGMA index_list("{table}")'):
                names = [part[2] for part in conn.execute(f'PRAGMA index_info("{row[1]}")')]
                indexes.append({"name": row[1], "unique": bool(row[2]), "columns": names})
            entry: dict = {
                "columns": columns,
                "indexes": sorted(indexes, key=lambda item: item["name"]),
            }
            if with_rows:
                entry["rows"] = int(
                    conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
            result["tables"][table] = entry
        return result


def schema_version(path: Path) -> int:
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as conn:
        try:
            row = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
        except sqlite3.OperationalError:
            return 0
        return int(row[0] or 0) if row else 0


def column_names(path: Path, table: str) -> set[str]:
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as conn:
        return {row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')}


def index_names(path: Path, table: str) -> set[str]:
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as conn:
        return {row[1] for row in conn.execute(f'PRAGMA index_list("{table}")')}


def prepare_copy(source: Path, root: Path) -> Path:
    """把 config/session 复制到临时客户端根，跳过体积项。"""

    def ignore(directory: str, names: list[str]) -> set[str]:
        return {name for name in names if name in SKIP_DIRS}

    target = root / "config" / "session"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, target, ignore=ignore)
    return target


# ─── 场景 1：启动 ────────────────────────────────────────────


def phase_startup(root: Path, session: Path, report: Report) -> None:
    report.phase("startup")
    from lvjiang import constants
    from lvjiang.apps.yysls.core.tuning_history import repository as tuning_repository
    from lvjiang.core.history import daily as daily_history
    from lvjiang.core.profile import repository as profile_repository

    report.check(constants.PROJECT_ROOT == root, "startup.project_root",
                 f"PROJECT_ROOT={constants.PROJECT_ROOT}")
    report.check(constants.SESSION_CONFIG_DIR == session, "startup.session_dir",
                 f"SESSION_CONFIG_DIR={constants.SESSION_CONFIG_DIR}")

    db = profile_repository.get_profile_db()
    report.check(db._db_path == session / "profile.db", "startup.profile.default_path",
                 f"path={db._db_path}")
    report.check(db._db.version() == profile_repository.CURRENT_VERSION,
                 "startup.profile.schema_version",
                 f"version={db._db.version()} expected={profile_repository.CURRENT_VERSION}")

    tasks = daily_history.TaskHistoryRepository()
    report.check(tasks.db_path == daily_history.default_db_path(),
                 "startup.tasks.default_path", f"path={tasks.db_path}")
    report.check(tasks.schema_version() == daily_history.CURRENT_SCHEMA_VERSION,
                 "startup.tasks.schema_version",
                 f"version={tasks.schema_version()}")

    tuning = tuning_repository.TuningHistoryRepository()
    report.check(tuning.db_path == tuning_repository.default_db_path(),
                 "startup.tuning.default_path", f"path={tuning.db_path}")
    report.check(tuning.schema_version() == tuning_repository.CURRENT_VERSION,
                 "startup.tuning.schema_version", f"version={tuning.schema_version()}")
    report.fact("startup", {
        "profile_entries": len(db.list_usernames()),
        "task_runs": len(tasks.list_task_runs(limit=1)),
        "tuning_runs": len(tuning.list_runs(limit=1)),
    })


# ─── 场景 2：读写 ────────────────────────────────────────────


def phase_profile_io(db, report: Report) -> None:
    report.phase("profile_io")
    from lvjiang.core.profile.author import author_stamp
    from lvjiang.core.profile.history_query import HistoryQuery
    from lvjiang.core.profile.models import MODEL_NOTE, MODEL_STOCK
    from lvjiang.core.profile.transactions import after_commit, profile_transaction

    user = "__verify_probe__"
    db.upsert(user, MODEL_STOCK, "probe", 5, op_type="set", source="验证")
    report.check(db.get_entry(user, MODEL_STOCK, "probe").value == 5.0,
                 "profile.write.set", "value=5")
    rows = db.get_history(user, MODEL_STOCK, "probe")
    report.check(len(rows) == 1 and rows[0].op_type == "set" and rows[0].source == "验证",
                 "profile.write.history_row", str(rows[0]) if rows else "empty")

    db.upsert(user, MODEL_STOCK, "probe", 3, op_type="delta", delta_value=3)
    entry = db.get_entry(user, MODEL_STOCK, "probe")
    report.check(entry.value == 3.0, "profile.write.delta_value_is_absolute",
                 f"value={entry.value}（upsert 写绝对值，delta 只进账本）")
    report.check(db.get_history(user, MODEL_STOCK, "probe")[0].delta_value == 3.0,
                 "profile.write.delta_ledger", "delta_value=3")

    # 用户主动 set 始终记账（即使文本没变）；系统行（regen）才按文本变化跳过。
    db.upsert(user, MODEL_NOTE, "笔记", 0, op_type="set", value_text="v1")
    db.upsert(user, MODEL_NOTE, "笔记", 0, op_type="set", value_text="v1")
    report.check(len(db.get_history(user, MODEL_NOTE, "笔记")) == 2,
                 "profile.write.note_user_set_always_recorded", "2 rows")
    db.upsert(user, MODEL_NOTE, "笔记", 0, op_type="regen", value_text="v1")
    report.check(len(db.get_history(user, MODEL_NOTE, "笔记")) == 2,
                 "profile.write.note_system_skip_unchanged", "文本未变，跳过")
    db.upsert(user, MODEL_NOTE, "笔记", 0, op_type="regen", value_text="v2")
    notes = db.get_history(user, MODEL_NOTE, "笔记")
    report.check(len(notes) == 3 and notes[0].value_text == "v2"
                 and notes[0].old_value_text == "v1",
                 "profile.write.note_system_record_changed",
                 f"rows={len(notes)} newest={notes[0].value_text if notes else ''}")
    report.check(db.get_entry(user, MODEL_NOTE, "笔记").value_text == "v2",
                 "profile.write.note_value_text", "条目文本已写回")

    entry = db.get_entry(user, MODEL_STOCK, "probe")
    report.check(
        db.update_if_current(
            user, MODEL_STOCK, "probe",
            expected_value=entry.value, expected_updated_at=entry.event_at,
            new_value=9, op_type="set", source="CAS"),
        "profile.cas.success", "expected snapshot matches")
    report.check(
        not db.update_if_current(
            user, MODEL_STOCK, "probe",
            expected_value=entry.value, expected_updated_at=entry.event_at,
            new_value=10, op_type="set"),
        "profile.cas.stale_rejected", "stale snapshot must return False")

    report.check(db.set_locked(user, MODEL_STOCK, "probe"), "profile.lock.set", "changed")
    history_before = len(db.get_history(user, MODEL_STOCK, "probe"))
    db.upsert(user, MODEL_STOCK, "probe", 99, op_type="set", source="锁定期间")
    report.check(db.get_entry(user, MODEL_STOCK, "probe").value == 9.0,
                 "profile.lock.rejects_write", "value still 9")
    report.check(len(db.get_history(user, MODEL_STOCK, "probe")) == history_before,
                 "profile.lock.rejects_history", "no ledger row")
    report.check(db.unlock(user, MODEL_STOCK, "probe") and not db.is_locked(user, MODEL_STOCK, "probe"),
                 "profile.lock.unlock", "released")

    query = HistoryQuery(usernames=(user,))
    report.check(len(db.history_page(query, limit=50)) > 0, "profile.query.page", "non-empty")
    report.check(db.history_count(query) == len(db.history_page(query, limit=500)),
                 "profile.query.count", "count == page length")
    facets = db.history_facets(query, "key")
    report.check(any(name.endswith(":probe") for name, _ in facets),
                 "profile.query.facets", str(facets[:3]))
    totals = db.history_delta_totals(query)
    report.check(isinstance(totals, tuple) and totals[0] >= 3, "profile.query.delta_totals", str(totals))
    report.check(db.aggregate_history(user, MODEL_STOCK, "probe", operation="sum") > 0,
                 "profile.query.aggregate", "sum > 0")

    author = author_stamp()
    pending_before = db.count_pending_push(0, author)
    unacked = db.list_unacked(author, 50)
    report.check(bool(unacked), "sync.pending_non_empty",
                 f"rows={len(unacked)} pending_total={pending_before}")
    if unacked:
        first = unacked[0]
        db.mark_remote_ids([(first.local_id, 987654)])
        report.check(db.max_remote_id() >= 987654, "sync.mark_remote_ids", "ack written")
        report.check(db.op_id_seen(first.op_id), "sync.op_id_seen", first.op_id)
        report.check(db.count_pending_push(0, author) == pending_before - 1,
                     "sync.pending_decreased",
                     f"{pending_before} -> {db.count_pending_push(0, author)}")
    report.check(bool(db.get_history_since(0, 100, author=author)), "sync.history_since", "non-empty")
    report.check(user in db.users_with_history_since("1970-01-01T00:00:00", author),
                 "sync.users_with_history_since", user)

    from lvjiang.core.profile.repository import db_apply_meta_locked
    db_apply_meta_locked("__verify_meta__", MODEL_STOCK, "k", 1,
                         instance_id="verify-remote", event_at="2026-01-01T00:00:00",
                         op_id="verify:meta:1", remote_id=987655)
    report.check(db.is_locked("__verify_meta__", MODEL_STOCK, "k"),
                 "sync.replay_meta_locks", "remote meta row applied")

    fired: list[bool] = []
    with profile_transaction(db):
        db.upsert(user, MODEL_STOCK, "inside", 1, op_type="set")
        after_commit(lambda: fired.append(True))
        report.check(not fired, "tx.callback_deferred", "not fired before commit")
    report.check(fired == [True] and db.get_entry(user, MODEL_STOCK, "inside") is not None,
                 "tx.callback_after_commit", "fired once")

    try:
        with profile_transaction(db):
            db.upsert(user, MODEL_STOCK, "rolled_back", 7, op_type="set")
            raise RuntimeError("回滚")
    except RuntimeError:
        pass
    report.check(db.get_entry(user, MODEL_STOCK, "rolled_back") is None
                 and db.get_history(user, MODEL_STOCK, "rolled_back") == [],
                 "tx.rollback_discards", "no residue")

    def write(index: int) -> None:
        db.upsert(user, MODEL_STOCK, f"thread{index}", index, op_type="set")

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(write, range(8)))
    stored = [db.get_entry(user, MODEL_STOCK, f"thread{index}") for index in range(8)]
    report.check(all(item is not None and item.value == index
                     for index, item in enumerate(stored)),
                 "concurrency.parallel_upsert", "8 distinct keys persisted")

    with closing(sqlite3.connect(str(db._db_path))) as conn:
        seen = conn.execute(
            "SELECT COUNT(*) FROM profile_entries WHERE username=?", (user,)).fetchone()[0]
    report.check(seen >= 9, "read.external_connection", f"rows={seen}")


def phase_history_io(root: Path, report: Report) -> None:
    report.phase("history_io")
    from datetime import datetime

    from lvjiang.apps.yysls.core.tuning_history import TuningHistoryRepository
    from lvjiang.apps.yysls.core.tuning_history.models import (
        RESET_COMPLETED,
        RESULT_RECYCLED,
        TuningEquipmentResult,
        TuningRunSummary,
    )
    from lvjiang.core.history.daily import (
        BatchRunRecord,
        TaskHistoryRepository,
        TaskRunRecord,
    )

    scratch = root / "verify" / "scratch"
    scratch.mkdir(parents=True, exist_ok=True)

    tasks = TaskHistoryRepository(scratch / "daily_history.db")
    now = datetime.now().astimezone().isoformat(timespec="milliseconds")
    tasks.create_batch_run(BatchRunRecord(
        batch_run_id="verify-batch", config_name="验证组", status="running",
        started_at=now, finished_at="", duration_ms=0, input_snapshot={"a": 1},
        report_path="", error_message="", target_id="windows", target_kind="local",
        target_label="本机"))
    tasks.create_task_run(TaskRunRecord(
        task_run_id="verify-task", batch_run_id="verify-batch", username="__verify__",
        task_id="demo", task_name="验证任务", task_scope="daily", source="single",
        status="running", started_at=now, finished_at="", duration_ms=0,
        params={"k": 1}, result_path="", log_path="", error_message="",
        target_id="windows", target_kind="local", target_label="本机",
        environment="windows", layout="default", input_kind="input"))
    tasks.update_task_params("verify-task", {"k": 2})
    tasks.finish_task_run("verify-task", status="completed", finished_at=now, duration_ms=12)
    tasks.finish_batch_run("verify-batch", status="completed", finished_at=now, duration_ms=20)
    record = tasks.get_task_run("verify-task")
    report.check(record is not None and record.status == "completed" and record.params == {"k": 2},
                 "tasks.write_and_finish", "record completed with final params")
    report.check(len(tasks.list_task_runs(status="completed")) == 1, "tasks.list_filter", "1 row")
    report.check(len(tasks.list_batch_runs(status="completed")) == 1, "tasks.list_batches", "1 row")
    report.check(tasks.filter_options()[0] == ["__verify__"], "tasks.filter_options", "user listed")
    report.check(tasks.date_bounds()[0] <= tasks.date_bounds()[1], "tasks.date_bounds", "ordered")

    tuning = TuningHistoryRepository(scratch / "tuning_history.db")
    summary = TuningRunSummary(
        run_id="verify-run", started_at=now, finished_at="", username="__verify__",
        status="running", stop_reason="", task_run_id="verify-task",
        target_id="windows", target_kind="local", target_label="本机",
        selected_slots=("weapon",), rule_snapshot=({"rule": 1},))
    tuning.create_run(summary)
    equipment = TuningEquipmentResult(
        equipment_id=1, slot_key="weapon", name="验证武器", type="长兵", level=80,
        quality="金", final_affixes=({"name": "攻击", "value": 1},), final_rating="极品",
        rounds=3, result=RESULT_RECYCLED, reason="", reset_outcome=RESET_COMPLETED,
        initial_affixes=(), tuning_started_at=now, finished_at=now,
        round_details=(), tuning_mode="manual", resets=1)
    equipment_pk = tuning.save_equipment("verify-run", equipment, event_id="verify-event")
    report.check(isinstance(equipment_pk, int) and equipment_pk > 0,
                 "tuning.save_equipment", f"pk={equipment_pk}")
    tuning.finish_run(TuningRunSummary(
        run_id="verify-run", started_at=now, finished_at=now, username="__verify__",
        status="completed", stop_reason="", total_equipment=1, recycled_count=1,
        total_rounds=3, markdown_path="logs/tuning/verify.md"))
    report.check(tuning.get_run("verify-run").status == "completed", "tuning.finish_run", "completed")
    report.check(tuning.get_run_for_task("verify-task", "__verify__") is not None,
                 "tuning.get_run_for_task", "linked by task_run_id")
    results = tuning.get_results("verify-run")
    report.check(len(results) == 1 and results[0].name == "验证武器",
                 "tuning.get_results", f"rows={len(results)}")
    counts = tuning.aggregate_counts()
    report.check(counts["runs"] == 1 and counts["recycled"] == 1, "tuning.aggregate_counts", str(counts))
    pending = tuning.pending_telemetry()
    report.check(len(pending) == 1, "tuning.telemetry_pending", f"rows={len(pending)}")
    if pending:
        tuning.mark_attempted([equipment_pk], at=now, schema_version=1)
        tuning.mark_reported([equipment_pk], at=now)
        pending = tuning.pending_telemetry()
        report.check(pending == [], "tuning.telemetry_reported", "queue drained")
    report.check(tuning.delete_run("verify-run"), "tuning.delete_run", "cascade delete")
    report.check(tuning.get_results("verify-run") == (), "tuning.cascade_cleanup", "equipment removed")


# ─── 场景 3：模拟建表 ────────────────────────────────────────


def phase_fresh_schema(root: Path, session: Path, report: Report) -> None:
    report.phase("fresh_schema")
    from lvjiang.apps.yysls.core.tuning_history import (
        TuningHistoryRepository,
    )
    from lvjiang.apps.yysls.core.tuning_history import (
        repository as tuning_repository,
    )
    from lvjiang.core.history.daily import CURRENT_SCHEMA_VERSION as TASK_VERSION
    from lvjiang.core.history.daily import TaskHistoryRepository
    from lvjiang.core.profile.repository import CURRENT_VERSION as PROFILE_VERSION
    from lvjiang.core.profile.repository import ProfileDB

    fresh = root / "verify" / "fresh"
    fresh.mkdir(parents=True, exist_ok=True)
    created = {
        "profile.db": fresh / "profile.db",
        "daily_history.db": fresh / "daily_history.db",
        "tuning_history.db": fresh / "tuning_history.db",
    }
    report.check(not created["profile.db"].exists(), "fresh.starts_empty", "no file before")
    ProfileDB(created["profile.db"])
    TaskHistoryRepository(created["daily_history.db"])
    TuningHistoryRepository(created["tuning_history.db"])

    versions = {
        "profile.db": PROFILE_VERSION,
        "daily_history.db": TASK_VERSION,
        "tuning_history.db": tuning_repository.CURRENT_VERSION,
    }
    for name, expected in versions.items():
        path = created[name]
        report.check(path.is_file(), f"fresh.{name}.created", f"{path.stat().st_size} bytes")
        report.check(schema_version(path) == expected, f"fresh.{name}.version",
                     f"version={schema_version(path)}")
        backup = list(path.parent.glob(f"{path.stem}.before-v*.db"))
        report.check(backup == [], f"fresh.{name}.no_backup", str(backup))
        # 新建库的结构必须与副本（真实数据、已迁移）完全一致。
        report.check(fingerprint(path, TABLES[name]) == fingerprint(session / name, TABLES[name]),
                     f"fresh.{name}.structure_matches_copy",
                     "columns/indexes identical to the migrated database")

    # 重复构造 = 不得再次迁移，也不得产生备份。
    before = {name: fingerprint(created[name], TABLES[name]) for name in created}
    ProfileDB(created["profile.db"])
    TaskHistoryRepository(created["daily_history.db"])
    TuningHistoryRepository(created["tuning_history.db"])
    after = {name: fingerprint(created[name], TABLES[name]) for name in created}
    report.check(before == after, "fresh.reopen_idempotent", "structure unchanged")

    # 并发首次建库：8 个线程同时构造，只允许一轮迁移。
    concurrent = fresh / "concurrent.db"
    errors: list[str] = []

    def open_once(_index: int) -> None:
        try:
            ProfileDB(concurrent)
        except Exception as exc:  # noqa: BLE001
            errors.append(repr(exc))

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(open_once, range(8)))
    report.check(not errors, "fresh.concurrent_init_no_error", str(errors[:2]))
    report.check(schema_version(concurrent) == PROFILE_VERSION,
                 "fresh.concurrent_init_version", f"version={schema_version(concurrent)}")
    report.check(len(list(concurrent.parent.glob("concurrent.before-v*.db"))) == 0,
                 "fresh.concurrent_init_no_backup", "no upgrade happened")


# ─── 场景 4：模拟迁移 ────────────────────────────────────────


def phase_profile_migration(root: Path, report: Report) -> None:
    report.phase("migrate_profile")
    from lvjiang.core.db import Migration, Schema, connect_database
    from lvjiang.core.profile.migrations import MIGRATIONS
    from lvjiang.core.profile.repository import CURRENT_VERSION, ProfileDB

    directory = root / "verify" / "migrate"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "profile-old.db"
    (directory / "account.json").write_text('{"name": "verify"}', encoding="utf-8")

    # 构造 v10 库：只应用前 10 个迁移。
    old_schema = Schema(
        [Migration(*row) for row in MIGRATIONS[:10]], label="ProfileDB-v10")
    old_schema.ensure(path, lambda: connect_database(path))
    report.check(schema_version(path) == 10, "migrate.setup_v10", f"version={schema_version(path)}")

    with closing(sqlite3.connect(str(path))) as conn:
        conn.execute(
            "INSERT INTO profile_entries (username, type, key, value, updated_at, updated_time) "
            "VALUES ('u', 'stock', 'k', 3, '2026-01-01T00:00:00', '2026-01-01T00:00:00')")
        # v10 库里的真实行是「本机写的」，origin_local_id 已回填成本地 id。
        conn.execute(
            "INSERT INTO profile_history (ts, username, type, key, old_value, new_value, "
            "change_type, source, device_id, origin_local_id) VALUES "
            "('2026-01-01T00:00:00', 'u', 'stock', 'k', NULL, 1, 'set', '旧来源', 'device-a', 1)")
        conn.execute(
            "INSERT INTO profile_history (ts, username, type, key, old_value, new_value, "
            "change_type, source, device_id, origin_local_id) VALUES "
            "('2026-01-02T00:00:00', 'u', 'stock', 'k', 1, 3, 'tick', '', 'device-a', 2)")
        conn.commit()

    db = ProfileDB(path)
    report.check(db._db.version() == CURRENT_VERSION, "migrate.profile.version",
                 f"version={db._db.version()}")
    columns = column_names(path, "profile_history")
    report.check({"event_at", "op_type", "instance_id", "local_id", "op_id",
                  "remote_id", "created_at", "value_text"} <= columns,
                 "migrate.profile.history_columns", str(sorted(columns)))
    report.check("origin_local_id" not in columns, "migrate.profile.dropped_origin_local_id", "")
    report.check({"event_at", "created_at", "op_id"} <= column_names(path, "profile_entries"),
                 "migrate.profile.entry_columns", "")
    report.check({"idx_history_op_id", "idx_history_pending"} <= index_names(path, "profile_history"),
                 "migrate.profile.indexes", str(sorted(index_names(path, "profile_history"))))

    rows = db.get_history("u", "stock", "k")
    op_types = sorted(row.op_type for row in rows)
    report.check(op_types == ["regen", "set"], "migrate.profile.tick_to_regen", str(op_types))
    report.check(all(row.op_id for row in rows), "migrate.profile.op_id_backfilled",
                 str([row.op_id for row in rows]))
    report.check(all(row.created_at for row in rows), "migrate.profile.created_at_filled", "")
    entry = db.get_entry("u", "stock", "k")
    # v11 只按「set/delta」重写条目锚点；tick（时间派生）不参与裁决。
    # 因此条目被回退到那条 set 的值，并带上该行的 op_id。
    report.check(entry is not None and entry.value == 1.0
                 and entry.event_at == "2026-01-01T00:00:00"
                 and entry.op_id == "legacy:device-a:1",
                 "migrate.profile.entry_anchor_from_set",
                 f"value={entry.value} at={entry.event_at} op_id={entry.op_id}")

    backup = path.with_suffix(".before-v13.db")
    report.check(backup.is_file(), "migrate.profile.backup_file", str(backup))
    report.check(schema_version(backup) == 10, "migrate.profile.backup_version",
                 f"version={schema_version(backup)}")
    folders = list(directory.glob("_migration-backup-*"))
    report.check(len(folders) == 1, "migrate.profile.backup_folder", str(folders))
    if folders:
        report.check((folders[0] / "profile-old.before-v13.db").is_file(),
                     "migrate.profile.backup_folder_db", "")
        report.check((folders[0] / "account.json").is_file(),
                     "migrate.profile.backup_folder_account", "")

    # 幂等：再次打开不产生第二个备份目录，数据不变。
    ProfileDB(path)
    report.check(len(list(directory.glob("_migration-backup-*"))) == 1,
                 "migrate.profile.idempotent_backup", "still one backup folder")
    report.check(schema_version(path) == CURRENT_VERSION, "migrate.profile.idempotent_version", "")
    report.fact("migrate_profile", {
        "columns": sorted(columns), "op_types": op_types,
        "backup": str(backup.name),
    })


def phase_task_migration(root: Path, report: Report) -> None:
    report.phase("migrate_tasks")
    from lvjiang.core.db import Migration, Schema, connect_database
    from lvjiang.core.history.daily import (
        _MIGRATIONS,
        CURRENT_SCHEMA_VERSION,
        TaskHistoryRepository,
    )

    directory = root / "verify" / "migrate"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "daily-old.db"
    Schema([Migration(*_MIGRATIONS[0])], label="TaskHistoryDB-v1").ensure(
        path, lambda: connect_database(path, foreign_keys=True))

    with closing(sqlite3.connect(str(path))) as conn:
        conn.execute(
            "INSERT INTO task_runs (task_run_id, batch_run_id, username, task_id, task_name, "
            "task_scope, source, status, started_at, finished_at, duration_ms, params_json, "
            "result_path, log_path, error_message) VALUES "
            "('old-task', NULL, 'u', 'demo', '旧任务', 'daily', 'single', 'completed', "
            "'2026-01-01T00:00:00', '2026-01-01T00:01:00', 60000, '{}', '', '', '')")
        conn.commit()

    repo = TaskHistoryRepository(path)
    report.check(repo.schema_version() == CURRENT_SCHEMA_VERSION, "migrate.tasks.version",
                 f"version={repo.schema_version()}")
    report.check({"target_id", "target_kind", "target_label", "environment", "layout",
                  "input_kind"} <= column_names(path, "task_runs"),
                 "migrate.tasks.new_columns", "")
    record = repo.get_task_run("old-task")
    report.check(record is not None and record.task_name == "旧任务"
                 and record.target_kind == "" and record.environment == "",
                 "migrate.tasks.data_preserved",
                 "" if record is None else f"{record.task_name}/{record.target_kind!r}")
    report.check(len(repo.list_task_runs()) == 1, "migrate.tasks.list_after_upgrade", "")


def phase_tuning_migration(root: Path, report: Report) -> None:
    report.phase("migrate_tuning")
    from lvjiang.apps.yysls.core.tuning_history import (
        TuningHistoryRepository,
    )
    from lvjiang.apps.yysls.core.tuning_history import (
        repository as tuning_repository,
    )
    from lvjiang.core.db import Migration, Schema, connect_database

    directory = root / "verify" / "migrate"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "tuning-old.db"
    Schema([Migration(*tuning_repository.MIGRATIONS[0])], label="TuningHistoryDB-v1").ensure(
        path, lambda: connect_database(path, foreign_keys=True))

    with closing(sqlite3.connect(str(path))) as conn:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute(
            "INSERT INTO tuning_runs (run_id, started_at, finished_at, username, status, "
            "stop_reason, created_at, updated_at) VALUES "
            "('old-run', '2026-01-01T00:00:00', '2026-01-01T00:10:00', 'u', 'completed', "
            "'', '2026-01-01T00:00:00', '2026-01-01T00:10:00')")
        conn.execute(
            "INSERT INTO tuning_equipment (run_id, sequence_id, name, equip_type, quality, "
            "result, reset_outcome) VALUES ('old-run', 1, '旧武器', '长兵', '金', 'tuned', '')")
        conn.commit()

    repo = TuningHistoryRepository(path)
    report.check(repo.schema_version() == tuning_repository.CURRENT_VERSION,
                 "migrate.tuning.version", f"version={repo.schema_version()}")
    report.check({"lock_status", "cooldown_kind", "cooldown_state", "cooldown_expires_at"}
                 <= column_names(path, "tuning_equipment"),
                 "migrate.tuning.equipment_columns", "")
    report.check({"task_run_id", "target_id", "target_kind", "target_label"}
                 <= column_names(path, "tuning_runs"), "migrate.tuning.run_columns", "")
    run = repo.get_run("old-run")
    report.check(run is not None and run.status == "completed" and run.target_kind == "",
                 "migrate.tuning.data_preserved", "" if run is None else run.status)
    results = repo.get_results("old-run")
    report.check(len(results) == 1 and results[0].name == "旧武器"
                 and results[0].lock_status == "",
                 "migrate.tuning.results_preserved", f"rows={len(results)}")
    report.check(repo.aggregate_counts()["tuned"] == 1, "migrate.tuning.aggregate", "")


# ─── 主流程 ──────────────────────────────────────────────────


def snapshot_real(session: Path) -> dict:
    names = ("profile.db", "daily_history.db", "tuning_history.db",
             "profile.db-wal", "tuning_history.db-wal", "daily_history.db-wal")
    return {name: digest(session / name) for name in names}


def main() -> int:
    parser = argparse.ArgumentParser(description="在 config/session 副本上验证三个库")
    parser.add_argument("--clean", action="store_true", help="验证完成后删除副本")
    parser.add_argument("--source", default=str(ROOT / "config" / "session"),
                        help="要复制的真实 config/session")
    args = parser.parse_args()

    source = Path(args.source).resolve()
    if not source.is_dir():
        print(f"source not found: {source}")
        return 2

    before_real = snapshot_real(source)
    root = Path(tempfile.mkdtemp(prefix="lvjiang-verify-"))
    session = prepare_copy(source, root)
    # LVJIANG_ROOT 必须在导入 lvjiang 之前设好：constants 在导入时确定根目录。
    os.environ["LVJIANG_ROOT"] = str(root)

    report = Report()
    report.fact("copy", {"root": str(root), "session": str(session)})
    print(f"copy root: {root}", flush=True)

    from lvjiang.core.profile.repository import get_profile_db

    phase_startup(root, session, report)
    phase_profile_io(get_profile_db(), report)
    phase_history_io(root, report)
    phase_fresh_schema(root, session, report)
    phase_profile_migration(root, report)
    phase_task_migration(root, report)
    phase_tuning_migration(root, report)

    report.phase("real_tree_untouched")
    after_real = snapshot_real(source)
    changed = sorted(name for name in before_real
                     if before_real[name] != after_real[name])
    report.fact("real_digests", {"before": before_real, "after": after_real})
    report.check(before_real == after_real, "real.digests_unchanged",
                 f"changed={changed}" if changed else "real config/session untouched")
    report.check(not list(source.glob("*.before-v*.db")), "real.no_new_backup", "")
    report.check(not list(source.glob("_migration-backup-*")), "real.no_new_backup_folder", "")

    from lvjiang.core.json_files import formatted_json

    payload = {
        "generated_at": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
        "checks_total": len(report.checks),
        "checks_failed": len(report.failures),
        "failures": report.failures,
        "facts": report.facts,
        "checks": report.checks,
    }
    output = root / "verify-report.json"
    output.write_text(formatted_json(payload), encoding="utf-8")

    print(f"\ntotal checks: {len(report.checks)}, failed: {len(report.failures)}", flush=True)
    for item in report.failures:
        print(f"  FAILED {item['phase']}.{item['id']}", flush=True)
    print(f"report: {output}", flush=True)

    if args.clean:
        shutil.rmtree(root, ignore_errors=True)
        print("copy removed", flush=True)
    return 0 if not report.failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
