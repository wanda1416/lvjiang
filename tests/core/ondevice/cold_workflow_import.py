"""由 pytest 在全新解释器执行；真实插件、bootstrap 和 apply_sync，无重载 mock。"""

import json
import shutil
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

from tests.config_write_guard import install_project_config_write_guard


def main():
    repo = Path(__file__).resolve().parents[3]
    install_project_config_write_guard(repo / "config")
    from lvjiang import constants
    from lvjiang.core.ondevice.plugins import configure_apps
    from lvjiang.core.plugins import builtin_app_ids

    root = constants.PROJECT_ROOT
    assert root.resolve() != repo
    configure_apps(",".join(builtin_app_ids()))
    session = root / "config/session"
    (session / "users").mkdir(parents=True, exist_ok=True)
    (session / "users/tester.json").write_text("{}", encoding="utf-8")
    (session / "session.json").write_text(
        json.dumps(
            {
                "users": ["tester"],
                "wf_configs": {"daily": {"count": 0, "enabled": False}},
            }
        ),
        encoding="utf-8",
    )
    from lvjiang.core.profile.repository import ProfileDB

    db = ProfileDB(session / "profile.db")
    db.upsert(
        "tester",
        "stock",
        "fixture",
        9,
        op_type="set",
        source="原始来源",
        sync_from="旧设备说明",
    )
    db.upsert("tester", "stock", "fixture", 0, op_type="set", source="", sync_from="")
    maximum = db.get_max_history_id()
    assert maximum > 0
    account = {
        "username": "fixture-account",
        "sync_established": False,
        "profile_sync": {"local_history_id": maximum},
    }
    (session / "account.json").write_text(json.dumps(account), encoding="utf-8")
    account_raw = (session / "account.json").read_bytes()

    def business_rows():
        with closing(sqlite3.connect(session / "profile.db")) as conn:
            return {
                name: conn.execute(f"SELECT * FROM {name} ORDER BY rowid").fetchall()
                for name in ("profile_entries", "profile_history")
            }

    before = business_rows()
    from lvjiang.core.ondevice.bootstrap import initialize

    result = json.loads(initialize(sys.argv[1]))
    assert result["ok"], result
    assert json.loads((session / "workflow.json").read_bytes())["workflow_params"][
        "daily"
    ] == {"count": 0, "enabled": False}
    assert "wf_configs" not in json.loads((session / "session.json").read_bytes())
    assert business_rows() == before
    pc = root.parent / "pc"
    shutil.copytree(root / "config/system", pc / "config/system")
    pcs = pc / "config/session"
    (pcs / "users").mkdir(parents=True)
    (pcs / "users/tester.json").write_text("{}", encoding="utf-8")
    (pcs / "session.json").write_text('{"users":["tester"]}', encoding="utf-8")
    (pcs / "interface.json").write_text(
        '{"actives":{"user":"tester","layout":"android","env":"android"}}',
        encoding="utf-8",
    )
    (pcs / "workflow.json").write_text(
        '{"workflow_params":{"daily":{"count":1},"pc_only":{"count":2}}}',
        encoding="utf-8",
    )
    from lvjiang.core.offline_bundle import build_offline_bundle

    archive = pc / "bundle.zip"
    build_offline_bundle(pc, archive, username="tester", layout="android")
    from lvjiang.core.ondevice.offline import apply_sync

    preserve = sys.argv[2] == "true"
    result = json.loads(apply_sync(str(archive), preserve, True))
    assert result["ok"], result
    params = json.loads((session / "workflow.json").read_bytes())["workflow_params"]
    assert params["daily"] == (
        {"count": 0, "enabled": False} if preserve else {"count": 1}
    )
    assert params["pc_only"] == {"count": 2}
    assert (session / "account.json").read_bytes() == account_raw
    assert business_rows() == before
    # 使用真实 ProfileDB/账户基线，只有远端传输替换为只读固定响应；任何 push 即失败。
    from lvjiang.core.profile import snapshot_sync
    from lvjiang.core.sync.round_state import invalidate

    def read_only_cloud(route, _data, _token):
        assert route == "/api/sync/state", route
        return {"protocol_version": 2, "history_head": 0}

    snapshot_sync.post = read_only_cloud
    invalidate()
    report = snapshot_sync.push_local("fixture-token-not-real")
    assert report.pushed == 0 and not report.errors, report
    assert (
        json.loads((session / "account.json").read_bytes())["profile_sync"][
            "local_history_id"
        ]
        == maximum
    )
    assert business_rows() == before
    print(
        "真实冷启动→立即导入通过；任务参数保留=",
        preserve,
        "；Profile 原始行列一致，既有基线=max，历史 push=0",
    )


if __name__ == "__main__":
    main()
