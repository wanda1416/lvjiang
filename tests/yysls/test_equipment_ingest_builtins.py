import lvjiang.apps.yysls.workflows.builtins.equipment_ingest  # noqa: F401
from lvjiang.workflows.builtins import get_function


class Engine:
    run_username = "alice"
    context = {}


def test_wf_upsert_and_plan_binding(tmp_path, monkeypatch):
    import lvjiang.constants
    monkeypatch.setattr(lvjiang.constants, "USERS_DIR", tmp_path)
    engine = Engine()
    engine.context = {}
    equip = {"_fp": "fp1", "type": "环", "level": 110}
    assert get_function("write_bag_item")(engine, "ring", equip) == "fp1"
    assert get_function("write_equipped")(engine, "ring", equip) == "fp1"

    from lvjiang.apps.yysls.core.loadout import LoadoutRepository
    state = LoadoutRepository("alice", tmp_path).load()
    assert state.active_plan.equipment["ring"] == "fp1"
    assert engine.context["_bound_loadout_plan_id"] == state.active_plan_id


def test_custom_wf_may_write_mock(tmp_path, monkeypatch):
    import lvjiang.constants
    monkeypatch.setattr(lvjiang.constants, "USERS_DIR", tmp_path)
    engine = Engine()
    engine.context = {}
    mock = {"_fp": "mock_fp1", "type": "环", "_extra": {"is_mock": True}}
    assert get_function("write_bag_item")(engine, "ring", mock) == "mock_fp1"
