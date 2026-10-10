"""Agent generation must preserve existing definitions and user defaults."""
from __future__ import annotations

import copy
from pathlib import Path

import pytest
import yaml

from lvjiang.apps.yysls.core.agent.service import AgentService


@pytest.fixture
def setup_service(tmp_path):
    system = tmp_path / "config/system/yysls"
    source = Path(__file__).parents[2] / "config/system/yysls"
    for rel in ("tune_config.yaml", "base_groups/default.yaml", "tuning_rules/huiyi_general.yaml"):
        dest = system / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text((source / rel).read_text(encoding="utf-8"), encoding="utf-8")
    def dispatch(operation, args):
        if operation == "context":
            return {"current_user": "test_user", "users": ["test_user", "another_user"]}
        if operation == "resolve_target":
            return {"target_id": args["target_id"] or "test_target"}
        return {}
    service = AgentService(tmp_path, dispatch=dispatch)
    service.set_enabled(True)
    base = yaml.safe_load((system / "base_groups/default.yaml").read_text(encoding="utf-8"))
    base["materials"]["food_rules"] = []
    base["scan"]["rules"] = []
    base["tune"]["rules"] = []
    base["tune"]["reset_exhausted_action"] = "skip"
    base["tune"]["lock_qualified"] = False
    rule = yaml.safe_load((system / "tuning_rules/huiyi_general.yaml").read_text(encoding="utf-8"))
    payload = {"name": "会意培养计划", "goal": "优先补齐首饰", "base_group": base,
               "rules": [rule], "run_config": {"selected_slots": ["ring"]}}
    return service, payload


def test_generation_is_new_local_only_idempotent_and_does_not_save_defaults(setup_service):
    service, payload = setup_service
    before = {p: p.read_bytes() for p in service.resolver.system_dir.rglob("*") if p.is_file()}
    original = copy.deepcopy(payload)
    preview = service.preview_generated_tuning("test_user", payload)
    assert preview["base_group"]["key"] != "default"
    assert not service.resolver.local_dir.exists()
    first = service.create_generated_tuning("test_user", "request-1", payload)
    assert first == service.create_generated_tuning("test_user", "request-1", payload)
    second = service.create_generated_tuning("test_user", "request-2", payload)
    assert first["id"] != second["id"]
    assert service.validate_auto_tuning("test_user", first["id"])["ready"]
    assert payload == original
    assert all(p.read_bytes() == content for p, content in before.items())
    assert len(list(service.resolver.local_dir.rglob("*.yaml"))) == 4
    assert not service.users_dir.exists()  # Creating a result must not save defaults.
    changed = {**payload, "goal": "另一个目标"}
    with pytest.raises(ValueError, match="不同内容"):
        service.create_generated_tuning("test_user", "request-1", changed)


def test_generation_rolls_back_owned_files_if_publish_fails(setup_service, monkeypatch):
    service, payload = setup_service

    def fail(*_args):
        raise OSError("fake publication failure")
    monkeypatch.setattr(service.store, "mutate", fail)
    with pytest.raises(OSError):
        service.create_generated_tuning("test_user", "request", payload)
    assert not list(service.resolver.local_dir.rglob("*.yaml"))
    assert service.store.load("results") == {}


def test_generation_does_not_automatically_activate_disabled_rule(setup_service):
    service, payload = setup_service
    payload["rules"][0]["disabled"] = True
    with pytest.raises(ValueError, match="禁用"):
        service.preview_generated_tuning("test_user", payload)
    assert not service.resolver.local_dir.exists()


def test_all_users_are_available_and_execution_checks_configuration_at_call_time(setup_service):
    service, payload = setup_service
    assert service.list_plans("another_user")["plans"]
    with pytest.raises(ValueError, match="用户不存在"):
        service.list_plans("missing_user")
    payload["base_group"]["smart_tuning"]["failure_action"]["action"] = "tune_full_recycle"
    record = service.create_generated_tuning("test_user", "request", payload)
    assert service.validate_auto_tuning("test_user", record["id"])["ready"]
    path = next((service.resolver.local_dir / "yysls/tuning_rules").glob("*.yaml"))
    path.write_text(path.read_text(encoding="utf-8") + "\ndescription: human change\n", encoding="utf-8")
    with pytest.raises(ValueError, match="已被修改"):
        service.validate_auto_tuning("test_user", record["id"])
    service.set_enabled(False)
    with pytest.raises(PermissionError, match="服务已关闭"):
        service.list_plans("another_user")


def test_generated_goal_uses_derived_plan_snapshot_not_persistent_defaults(setup_service):
    from types import SimpleNamespace

    from lvjiang.apps.yysls.core.loadout import LoadoutRepository

    service, payload = setup_service
    service._game_config = SimpleNamespace(get_playstyles_for_arts=lambda _arts: ["无名", "九剑"])
    repo = LoadoutRepository("test_user", service.users_dir)
    plan = repo.create_plan("方案示例", "无名剑法", "无名枪法", playstyle="无名", activate=False)
    before = repo.load().to_dict()
    payload["plan_ids"] = [plan.id]
    payload["plan_targets"] = {plan.id: {"playstyle": "九剑"}}
    record = service.create_generated_tuning("test_user", "goal-request", payload)
    prepared = service.validate_auto_tuning("test_user", record["id"])
    assert prepared["smart_state"]["plans"][plan.id]["playstyle"] == "九剑"
    assert list(prepared["smart_state"]["plans"]) == [plan.id]
    assert repo.load().to_dict() == before


def test_user_selection_patch_preserves_other_workflows_and_rejects_stale_revision(setup_service):
    from lvjiang.apps.yysls.config.auto_tuning_config import default_auto_tuning_config
    from lvjiang.apps.yysls.core.agent.service import revision
    from lvjiang.core.user_config import User, load_user_metadata, save_user_metadata

    service, _ = setup_service
    config = default_auto_tuning_config()
    config["rules"] = {"huiyi_general": {"enabled": True, "playstyles": ["无名"]}}
    user = User("test_user", workflow_params={"auto_tuning": config, "other": {"enabled": True}})
    save_user_metadata(user, service.users_dir)
    result = service.update_tuning_config("test_user", revision(config), {"selected_slots": ["ring"]})
    saved = load_user_metadata("test_user", service.users_dir)
    assert saved.workflow_params["other"] == {"enabled": True}
    assert result["config"]["selected_slots"] == ["ring"]
    with pytest.raises(ValueError, match="已改变"):
        service.update_tuning_config("test_user", revision(config), {"min_level": 105})
    assert load_user_metadata("test_user", service.users_dir).workflow_params == saved.workflow_params
