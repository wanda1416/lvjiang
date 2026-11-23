"""P0 回归门禁：覆盖 core/config 路径边界、pending 保留、字典注册表提交与 core/batch 通道中止/停止语义。"""
from __future__ import annotations

from contextlib import ExitStack
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from lvjiang.core.android.recovery import ChannelFaultError
from lvjiang.core.batch.models import (
    ST_SKIPPED,
    ST_STOPPED,
    BatchCheckResult,
    BatchContext,
    BatchRunSpec,
    BatchScript,
    BatchStageResult,
    BatchWorkflows,
)
from lvjiang.core.batch.scheduler import BatchScheduler
from lvjiang.core.config.edit_session import ConfigEditSession
from lvjiang.core.config.merge import (
    REGISTRY_LIST_IDENTITY,
    REGISTRY_LIST_PATHS,
    merge_registry_list,
)
from lvjiang.core.config.resolver import ConfigResolver
from lvjiang.core.config.update_store import UpdateStore
from lvjiang.workflows.result_protocol import (
    RESULT_SKIPPED,
    RESULT_STOPPED,
    RESULT_SUCCESS,
)


def _make_scheduler(
    tmp_path: Path,
    scripts: tuple[BatchScript, ...],
    *,
    unattended: bool = False,
    recover_wf: str = "",
    stop_check=None,
    ui_callback=None,
) -> tuple[BatchScheduler, MagicMock]:
    spec = BatchRunSpec(
        group_id="g1",
        name="测试组",
        entries=("u1",),
        candidate_usernames=("u1",),
        scripts=scripts,
        workflows=BatchWorkflows(recover_unattended=recover_wf),
        unattended=unattended,
    )
    session_mgr = MagicMock()
    session_mgr.load.return_value = {}
    session_mgr._users_dir = tmp_path
    ctx = BatchContext(
        capture=None,
        ocr=None,
        input_ctrl=None,
        layout=None,
        ui_callback=ui_callback,
    )
    scheduler = BatchScheduler(
        spec,
        ctx,
        session_mgr,
        stop_check or (lambda: False),
    )
    return scheduler, session_mgr


def _run_and_capture(scheduler: BatchScheduler) -> dict:
    summaries: list[dict] = []
    scheduler.finished_all.connect(summaries.append)
    scheduler._execution_lease = None
    scheduler._user_scope = ExitStack()
    try:
        scheduler._run_locked()
    finally:
        scheduler._user_scope.close()
        if scheduler._execution_lease is not None:
            scheduler._execution_lease.release()
            scheduler._execution_lease = None
    assert len(summaries) == 1
    return summaries[0]


def test_config_resolver_blocks_path_traversal_and_absolute_paths(tmp_path: Path):
    """用户模式下传入 ../system/x.yaml 或绝对路径必须被拒绝，不能绕过层隔离。"""
    resolver = ConfigResolver(tmp_path, dev_mode=False)
    resolver.system_dir.mkdir(parents=True, exist_ok=True)
    resolver.local_dir.mkdir(parents=True, exist_ok=True)

    for bad in ("../system/secret.yaml", "..\\system\\secret.yaml", "/etc/passwd", "a/../../system/x.yaml", "C:foo.yaml"):
        with pytest.raises(ValueError):
            resolver.resolve_read(bad)
        with pytest.raises(ValueError):
            resolver.write_entity(bad, "x: 1\n")
        with pytest.raises(ValueError):
            resolver.delete_entity(bad)


def test_edit_session_commit_with_dict_registry_list_identity(tmp_path: Path, monkeypatch):
    """编辑含字典列表（带 REGISTRY_LIST_IDENTITY）的聚合配置时，commit 不得抛出 TypeError。"""
    source = ConfigResolver(tmp_path, dev_mode=False)
    source.system_dir.mkdir(parents=True, exist_ok=True)
    source.local_dir.mkdir(parents=True, exist_ok=True)
    rel = "test_dict_registry.yaml"
    (source.system_dir / rel).write_text(
        "items:\n  - id: a\n    val: 1\n  - id: b\n    val: 2\n",
        encoding="utf-8",
        newline="\n",
    )
    monkeypatch.setitem(REGISTRY_LIST_PATHS, rel, ("items",))
    monkeypatch.setitem(REGISTRY_LIST_IDENTITY, rel, {"items": "id"})

    session = ConfigEditSession(source, entity_dirs=(), merged_paths=(rel,))
    try:
        doc = session.resolver.load_merged(rel)
        doc["items"][0]["val"] = 99
        session.resolver.save_merged(rel, doc)
        session.commit()
    finally:
        session.close()

    merged = source.load_merged(rel)
    assert merged["items"][0]["val"] == 99
    assert merged["items"][1]["val"] == 2


def test_merge_registry_list_handles_unhashable_items_without_identity():
    """无 identity_field 且元素为 dict 时，merge_registry_list 不得因建 set 崩溃。"""
    base = [{"k": 1}, {"k": 2}]
    overlay = {"__removed__": [{"k": 1}], "__added__": [{"k": 3}]}
    assert merge_registry_list(base, overlay, identity_field=None) == [{"k": 2}, {"k": 3}]


def test_update_store_install_preserves_pending_node(tmp_path: Path):
    """安装更新或撤回会员重写 state.json 时，不得静默抹除既有的 pending 节点。"""
    (tmp_path / "system").mkdir()
    (tmp_path / "system" / "app.yaml").write_text("k: 1\n", encoding="utf-8", newline="\n")
    (tmp_path / "local").mkdir()
    store = UpdateStore(tmp_path, "0.15.19", "0" * 64)
    store.mark_pending(public=(2, "a" * 64))
    before = store._state()
    assert before.get("pending") == {"revision": 2, "public_sha256": "a" * 64}

    store.install(member_files={}, activate=lambda _path, _changed: None)
    after = store._state()
    assert after.get("pending") == before["pending"]


def test_recover_unattended_propagates_stopped_and_returns_stage_result(tmp_path: Path, monkeypatch):
    """无人值守恢复阶段返回 RESULT_STOPPED 时，必须置位 _stopped 并返回结果供上层中止循环。"""
    scheduler, _ = _make_scheduler(
        tmp_path,
        (),
        unattended=True,
        recover_wf="recover.wf",
    )
    scheduler._unattended_hit = "pause popup"
    monkeypatch.setattr(
        scheduler,
        "_run_stage",
        lambda *a, **kw: BatchStageResult(
            status=RESULT_STOPPED,
            message="user stopped",
            state={"recovered": False},
        ),
    )
    res = scheduler._recover_unattended(0, "u1", {}, pending=True)
    assert res is not None
    assert res.status == RESULT_STOPPED
    assert res.state == {"recovered": False}
    assert scheduler._stopped is True


def test_batch_user_mode_channel_fault_aborts_and_skips_remaining(tmp_path: Path, monkeypatch):
    """用户模式发生 ChannelFaultError 时必须命中通道故障分支、中止剩余脚本并跳过收尾阶段。"""
    s1 = BatchScript(id="s1", name="脚本1", wf_file="s1.wf")
    s2 = BatchScript(id="s2", name="脚本2", wf_file="s2.wf")
    scheduler, _ = _make_scheduler(tmp_path, (s1, s2))

    ran: list[str] = []

    def fake_run(script, session, username, *, params=None):
        ran.append(script.id)
        raise ChannelFaultError("device_gone", "ADB disconnected")

    monkeypatch.setattr(scheduler, "_run_script", fake_run)
    monkeypatch.setattr("lvjiang.core.batch.scheduler.BatchReport.write", lambda self, *a, **kw: None)
    monkeypatch.setattr(scheduler, "_save_result", lambda *a, **kw: None)

    summary = _run_and_capture(scheduler)
    assert ran == ["s1"]
    assert scheduler._aborted is True
    assert summary["aborted"] is True


def test_batch_user_mode_batch_check_blocks_single_failed_script(tmp_path: Path, monkeypatch):
    """用户模式下即使同用户有其它脚本通过，batch_check 未通过的单脚本也不得带病执行。"""
    s1 = BatchScript(id="s1", name="脚本1", wf_file="s1.wf", batch_check="can_run")
    s2 = BatchScript(id="s2", name="脚本2", wf_file="s2.wf", batch_check="can_run")
    scheduler, _ = _make_scheduler(tmp_path, (s1, s2))

    ran: list[str] = []
    monkeypatch.setattr(
        scheduler,
        "_check_script",
        lambda script, username, *, params: BatchCheckResult(
            status=RESULT_SKIPPED if script.id == "s1" else RESULT_SUCCESS,
            message="not ready" if script.id == "s1" else "",
        ),
    )
    monkeypatch.setattr(
        scheduler,
        "_run_script",
        lambda script, session, username, *, params=None: (ran.append(script.id), {})[1],
    )
    monkeypatch.setattr("lvjiang.core.batch.scheduler.BatchReport.write", lambda self, *a, **kw: None)
    monkeypatch.setattr(scheduler, "_save_result", lambda *a, **kw: None)

    summary = _run_and_capture(scheduler)
    assert ran == ["s2"]
    assert summary["entries"]["u1"]["scripts"]["s1"] == ST_SKIPPED
    assert summary["entries"]["u1"]["scripts"]["s2"] != ST_SKIPPED


def test_batch_stop_marks_stopped_and_skips_session_save(tmp_path: Path, monkeypatch):
    """脚本执行期间触发批次级停止时，应记为 ST_STOPPED、跳过未完成 session 落盘并中止剩余脚本。"""
    s1 = BatchScript(id="s1", name="脚本1", wf_file="s1.wf")
    s2 = BatchScript(id="s2", name="脚本2", wf_file="s2.wf")
    stop_flag = {"stopped": False}
    scheduler, session_mgr = _make_scheduler(
        tmp_path, (s1, s2), stop_check=lambda: stop_flag["stopped"]
    )
    session_mgr.load.return_value = {"dirty": False}

    ran: list[str] = []

    def fake_run(script, session, username, *, params=None):
        ran.append(script.id)
        session["dirty"] = True
        stop_flag["stopped"] = True
        return {}

    monkeypatch.setattr(scheduler, "_run_script", fake_run)
    monkeypatch.setattr("lvjiang.core.batch.scheduler.BatchReport.write", lambda self, *a, **kw: None)
    monkeypatch.setattr(scheduler, "_save_result", lambda *a, **kw: None)

    summary = _run_and_capture(scheduler)
    assert ran == ["s1"]
    assert summary["entries"]["u1"]["scripts"]["s1"] == ST_STOPPED
    assert session_mgr.save.call_count == 0
    assert scheduler._stopped is True
    assert summary["stopped"] is True
