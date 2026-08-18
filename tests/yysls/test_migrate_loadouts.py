import importlib.util
import json
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[2] / "scripts/migrate_loadouts.py"
SPEC = importlib.util.spec_from_file_location("migrate_loadouts", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def test_build_loadout_merges_stores_and_infers_slot_type(tmp_path):
    config = tmp_path / "config/system/yysls"
    config.mkdir(parents=True)
    (config / "game_config.yaml").write_text("schools: {}\n", encoding="utf-8")
    session = tmp_path / "config/session"
    session.mkdir(parents=True)
    (session / "session.json").write_text("{}", encoding="utf-8")
    legacy = {
        "equipped": {"leg": {"name": "旧胫甲", "level": 110}},
        "bag_items": {"ring": {"bagfp": {"_fp": "bagfp", "type": "环"}}},
        "mock_items": {"ring": {"mock_x": {"_fp": "mock_x", "type": "环"}}},
    }
    result = MODULE.build_loadout(legacy, "alice", tmp_path)
    plan = result["plans"][result["active_plan_id"]]
    assert result["equipment_items"][plan["equipment"]["leg"]]["type"] == "胫甲"
    assert {"bagfp", "mock_x"}.issubset(result["equipment_items"])


def test_migrate_is_idempotently_skipped_for_nonempty_target(tmp_path):
    config = tmp_path / "config/system/yysls"
    config.mkdir(parents=True)
    (config / "game_config.yaml").write_text("schools: {}\n", encoding="utf-8")
    session = tmp_path / "config/session"
    users = session / "users"
    users.mkdir(parents=True)
    (session / "session.json").write_text("{}", encoding="utf-8")
    source = users / "alice.json"
    source.write_text(json.dumps({
        "bag_items": {"ring": {"fp": {"_fp": "fp", "type": "环"}}}
    }), encoding="utf-8")
    assert MODULE.migrate_user(source, tmp_path, dry_run=False, force=False).startswith("migrated:")
    assert MODULE.migrate_user(source, tmp_path, dry_run=False, force=False) == "skip:target-not-empty"
