"""真实打包/安装路径的双开关与新旧/空/缺失来源矩阵。"""

import json
import sqlite3
from contextlib import closing

import pytest

from lvjiang.core.config.workflow_document import read_workflow_source
from lvjiang.core.offline_bundle import build_offline_bundle, install_offline_bundle
from tests.core.test_offline_bundle import bundle as bundle

PARAMS = {
    "pc": {"daily": {"n": 1, "pc_field": 7}, "pc_only": {"n": 2}},
    "phone": {"daily": {"n": 0, "b": False}, "phone_only": {"n": 9}},
}


def state(root, kind, who):
    root.mkdir(parents=True, exist_ok=True)
    session = {"users": ["tester"]}
    if kind in {"legacy", "both"}:
        session["wf_configs"] = (
            PARAMS[who] if kind == "legacy" else {"stale": {"n": 99}}
        )
    (root / "session.json").write_text(json.dumps(session), encoding="utf-8")
    workflow = root / "workflow.json"
    if kind in {"new", "both", "empty"}:
        workflow.write_text(
            json.dumps({"workflow_params": {} if kind == "empty" else PARAMS[who]}),
            encoding="utf-8",
        )
    else:
        workflow.unlink(missing_ok=True)
    return {} if kind == "empty" else None if kind == "missing" else PARAMS[who]


# 每种来源语义各保留代表配对；四个独立开关组合只在常规新文档配对验证，避免笛卡尔膨胀。
@pytest.mark.parametrize(
    "pc_kind,phone_kind,preserve_tasks,preserve_batch",
    [
        (pc, phone, preserve, True)
        for pc, phone in [
            ("legacy", "new"),
            ("legacy", "legacy"),
            ("both", "both"),
            ("empty", "new"),
            ("new", "empty"),
            ("missing", "legacy"),
            ("new", "missing"),
            ("missing", "missing"),
        ]
        for preserve in (True, False)
    ]
    + [
        ("new", "new", task, batch)
        for task, batch in [(True, True), (True, False), (False, True), (False, False)]
    ],
)
def test_all_sources_and_independent_switches(
    bundle, tmp_path, pc_kind, phone_kind, preserve_tasks, preserve_batch
):
    pc, archive = bundle
    pc_session = pc / "config/session"
    pc_params = state(pc_session, pc_kind, "pc")
    (pc_session / "batch.json").write_text('{"pc":true}', encoding="utf-8")
    pc_before = {
        p.relative_to(pc): p.read_bytes() for p in pc.rglob("*") if p.is_file()
    }
    build_offline_bundle(pc, archive, username="tester", layout="android")
    assert pc_before == {
        p.relative_to(pc): p.read_bytes() for p in pc.rglob("*") if p.is_file()
    }
    phone = tmp_path / "phone"
    phone_session = phone / "config/session"
    phone_params = state(phone_session, phone_kind, "phone")
    (phone_session / "batch.json").write_text('{"phone":true}', encoding="utf-8")
    (phone_session / "account.json").write_bytes(
        b'{"sync_established":false,"profile_sync":{"local_history_id":17}}'
    )
    (phone_session / "account_token").write_bytes(b"fixture-only-not-a-credential")
    account = (phone_session / "account.json").read_bytes()
    # Profile 原始值/列（包括空和非空 source/sync_from）不属于离线同步域。
    with closing(sqlite3.connect(phone_session / "profile.db")) as db:
        db.execute(
            "CREATE TABLE business (id INTEGER, value, source TEXT, sync_from TEXT)"
        )
        db.executemany(
            "INSERT INTO business VALUES (?,?,?,?)",
            [(1, 0, "原始来源", ""), (2, "原字串", "", "历史设备")],
        )
        db.commit()
    profile = (phone_session / "profile.db").read_bytes()
    old_archive = phone / "config/local/.migrations/content-split-v2/original.json"
    old_archive.parent.mkdir(parents=True)
    old_archive.write_bytes(b'{"original":"fixture"}')
    install_offline_bundle(
        archive,
        phone,
        preserve_task_params=preserve_tasks,
        preserve_batch_config=preserve_batch,
    )
    result = read_workflow_source(
        phone_session / "session.json", phone_session / "workflow.json"
    ).params
    expected = (
        phone_params
        if pc_params is None
        else ({**pc_params, **(phone_params or {})} if preserve_tasks else pc_params)
    )
    assert result == expected
    assert json.loads((phone_session / "batch.json").read_bytes()) == (
        {"phone": True} if preserve_batch else {"pc": True}
    )
    assert (phone_session / "account.json").read_bytes() == account
    assert (phone_session / "profile.db").read_bytes() == profile
    assert old_archive.read_bytes() == b'{"original":"fixture"}'


def test_phone_corrupt_workflow_stops_before_mirroring(bundle, tmp_path):
    _, archive = bundle
    phone = tmp_path / "phone"
    session = phone / "config/session"
    state(session, "legacy", "phone")
    (session / "workflow.json").write_bytes(b"{")
    old = phone / "config/system/keep.yaml"
    old.parent.mkdir(parents=True)
    old.write_bytes(b"untouched")
    before = {
        p.relative_to(phone): p.read_bytes() for p in phone.rglob("*") if p.is_file()
    }
    with pytest.raises(ValueError):
        install_offline_bundle(archive, phone)
    assert before == {
        p.relative_to(phone): p.read_bytes() for p in phone.rglob("*") if p.is_file()
    }


def test_edit_during_import_keeps_edit_and_original_snapshot(
    bundle, tmp_path, monkeypatch
):
    from lvjiang.core import offline_bundle

    pc, archive = bundle
    state(pc / "config/session", "new", "pc")
    build_offline_bundle(pc, archive, username="tester", layout="android")
    phone = tmp_path / "phone"
    session = phone / "config/session"
    state(session, "legacy", "phone")
    original = (session / "session.json").read_bytes()
    write_layers = offline_bundle._write_mirrored_layers
    edited = b'{"users":["tester"],"wf_configs":{"daily":{"n":77}}}'

    def edit(*args):
        write_layers(*args)
        (session / "session.json").write_bytes(edited)

    monkeypatch.setattr(offline_bundle, "_write_mirrored_layers", edit)
    with pytest.raises(RuntimeError, match="读取后修改"):
        install_offline_bundle(archive, phone)
    assert (session / "session.json").read_bytes() == edited
    assert not (session / "workflow.json").exists()
    assert original in [
        p.read_bytes() for p in (session / ".migrations/workflow").iterdir()
    ]
