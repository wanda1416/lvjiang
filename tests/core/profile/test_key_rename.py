"""保护旧历史升级的持续运行契约。"""

import sqlite3

from lvjiang.core.profile.repository import MIGRATIONS, ProfileDB


def test_v7_preserves_history_and_separates_reset_sync_and_unknown(tmp_path):
    path = tmp_path / "profile.db"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE schema_version (version INTEGER PRIMARY KEY)")
        for version, _, migrate in MIGRATIONS[:6]:
            migrate(conn)
            conn.execute("INSERT INTO schema_version VALUES (?)", (version,))
        rows = [
            ("quota", "progress", 8, 0, "tick", "reset:0"),
            ("regen", "energy", 5, 6, "tick", "regen:+1.2500"),
            ("stock", "reward", 1, 3, "action", "sync_from:progress"),
            ("regen", "energy", 6, 9, "action", "sync_from:progress"),
            ("stock", "reward", 3, 4, "action", "unrecognized"),
        ]
        conn.executemany(
            "INSERT INTO profile_history (ts,username,type,key,old_value,new_value,change_type,detail) "
            "VALUES ('2026-01-01','tester',?,?,?,?,?,?)", rows)
    db = ProfileDB(path)
    history = {row["id"]: row for row in db.get_history("tester")}
    assert history[1]["change_type"] == "reset" and history[1]["delta_value"] == -8
    assert history[2]["delta_value"] == 1.25
    assert history[3]["sync_from"] == "quota:progress" and history[3]["delta_value"] == 2
    assert history[4]["delta_value"] is None
    with db._connect() as conn:
        assert conn.execute("SELECT detail FROM profile_history_legacy WHERE history_id=5").fetchone()[0] == "unrecognized"
        assert "detail" not in {row[1] for row in conn.execute("PRAGMA table_info(profile_history)")}
    assert path.with_suffix(".before-v7.db").exists()
    assert ProfileDB(path).get_history("tester") == db.get_history("tester")
