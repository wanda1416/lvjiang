"""Game-plan scans must bind by name and use the scanned plan, not the UI active one."""
from dataclasses import fields, is_dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest

from lvjiang.apps.yysls.config import get_game_config
from lvjiang.apps.yysls.core.combat.combat_attrs import CombatAttributes
from lvjiang.apps.yysls.core.loadout import LoadoutRepository
from lvjiang.apps.yysls.workflows.builtins.equipment_ingest import (
    _bind_scanned_loadout,
    _loadout_scan_target,
    _loadout_scan_targets,
    _write_equipped,
)
from lvjiang.apps.yysls.workflows.builtins.role_attr_ingest import (
    _save_scanned_base_attrs,
)
from lvjiang.core.scene_definition import SceneRegistry
from lvjiang.workflows.grammar import parse_file
from lvjiang.workflows.grammar.ast_nodes import CallProc
from lvjiang.workflows.metadata import parse_metadata_file


def _engine(tmp_path):
    return SimpleNamespace(run_username="test_user", users_dir=tmp_path,
                           context={}, _ui_callback=None)


def _calls(value):
    if isinstance(value, (list, tuple)):
        for item in value:
            yield from _calls(item)
    elif is_dataclass(value):
        if isinstance(value, CallProc):
            yield value.name
        for field in fields(value):
            yield from _calls(getattr(value, field.name))


def test_plan_scan_binding_keeps_active_plan_unchanged(tmp_path):
    engine = _engine(tmp_path)
    repo = LoadoutRepository(engine.run_username, tmp_path)
    active = repo.load().active_plan_id
    plan = repo.create_plan("方案甲", "无名剑法", "无名枪法",
                            playstyle="玩法甲", activate=False)
    targets = _loadout_scan_targets(engine)
    assert targets == [{
        "name": "方案甲", "main_art": "无名剑法",
        "sub_art": "无名枪法", "playstyle": "玩法甲"}]
    assert _bind_scanned_loadout(engine, "方案甲", "无名枪法", "无名剑法") == plan.id
    assert repo.load().active_plan_id == active
    with pytest.raises(ValueError, match="武学"):
        _bind_scanned_loadout(engine, "方案甲", "无名剑法", "其他武学")
    assert _loadout_scan_target(engine, "方案甲")["main_art"] == "无名剑法"
    assert _loadout_scan_target(engine)["name"] == "默认方案"
    with pytest.raises(ValueError, match="主武学和副武学"):
        _loadout_scan_target(engine, "方案甲", "无名剑法", "")
    with pytest.raises(ValueError, match="绑定写入目标"):
        _write_equipped(engine, "head", {"type": "冠胄"})


def test_ambiguous_name_never_writes_arbitrary_plan(tmp_path):
    engine = _engine(tmp_path)
    repo = LoadoutRepository(engine.run_username, tmp_path)
    for _ in range(2):
        repo.create_plan("同名", "无名剑法", "无名枪法", activate=False)
    with pytest.raises(ValueError, match="重名"):
        _loadout_scan_targets(engine)
    with pytest.raises(ValueError, match="匹配 2 个"):
        _bind_scanned_loadout(engine, "同名", "无名剑法", "无名枪法")
    assert "_bound_loadout_plan_id" not in engine.context


def test_silent_base_write_uses_bound_plan_and_rejects_incomplete_ocr(
        tmp_path, monkeypatch):
    engine = _engine(tmp_path)
    repo = LoadoutRepository(engine.run_username, tmp_path)
    active = repo.load().active_plan_id
    plan = repo.create_plan("方案甲", "无名剑法", "无名枪法",
                            playstyle="玩法甲", activate=False)
    _bind_scanned_loadout(engine, "方案甲", plan.main_martial_art,
                          plan.sub_martial_art)
    saved = []
    monkeypatch.setattr(
        "lvjiang.apps.yysls.config.save_play_style",
        lambda school, name, values: saved.append((school, name, values)))
    parsed = {
        "min_outer": 100.0, "max_outer": 200.0,
        "min_mingjin": 10.0, "max_mingjin": 20.0,
        "mingjin_pen": 0.0,
        "precision": 70.0, "crit_rate": 80.0,
        "_right_outer_valid": True,
        "_right_outer_pen_valid": True,
        "_right_attr_pen_valid": True,
    }
    with pytest.raises(ValueError, match="右侧详情"):
        _save_scanned_base_attrs(engine, {**parsed, "_right_outer_valid": False})
    assert saved == []
    assert _save_scanned_base_attrs(engine, parsed) == "test_user_玩法甲"
    assert saved[0][0] == "鸣金·虹"
    assert saved[0][1] == "test_user_玩法甲"
    assert repo.load().plans[plan.id].base_attribute == "test_user_玩法甲"
    assert repo.load().active_plan_id == active
    assert get_game_config().get_school_attr(saved[0][0]) == "鸣金"

    second = repo.create_plan("方案乙", "无名枪法", "无名剑法",
                              playstyle="玩法甲", activate=False)
    _bind_scanned_loadout(engine, second.name, second.main_martial_art,
                          second.sub_martial_art)
    with pytest.raises(ValueError, match="不同基础属性"):
        _save_scanned_base_attrs(engine, {**parsed, "min_outer": 120.0})
    assert repo.load().plans[second.id].base_attribute == ""
    assert len(saved) == 1


def test_workflow_and_shared_subcalls_parse():
    base = Path("config/system/workflows")
    for path in (
        "scan_all_loadouts.wf", "scan_equipped.wf",
        "standalone/scan_role_base_attr.wf",
        "subcall/loadout_plan_navigation.wf",
        "subcall/equipped_slots_scan.wf", "subcall/role_base_attr_scan.wf",
        "subcall/equipped_plan_scan.wf", "subcall/role_base_attr_plan_scan.wf",
    ):
        parse_file(base / path)


def test_game_plan_scene_loads_with_distinct_popup_views():
    scene = SceneRegistry().get_scene("training_main")
    assert scene is not None
    assert {view.key for view in scene.views} >= {
        "fangan_fill", "fangan_confirm",
    }


def test_direct_and_batch_workflows_call_the_same_parameterized_procedures():
    base = Path("config/system/workflows")
    equipment = parse_file(base / "scan_equipped.wf")
    role = parse_file(base / "standalone/scan_role_base_attr.wf")
    batch = parse_file(base / "scan_all_loadouts.wf")
    assert "scan_equipped_plan" in set(_calls(equipment.body))
    assert "scan_role_base_attr_for_plan" in set(_calls(role.body))
    assert {"scan_equipped_plan", "scan_role_base_attr_for_plan"} <= set(
        _calls(batch.body))
    for path in (base / "scan_equipped.wf", base / "standalone/scan_role_base_attr.wf"):
        names = {item["name"] for item in parse_metadata_file(path)["parameters"]}
        assert {"plan_name", "main_art", "sub_art"} <= names


def test_silent_base_subtracts_only_bound_plan_equipment(tmp_path, monkeypatch):
    engine = _engine(tmp_path)
    repo = LoadoutRepository(engine.run_username, tmp_path)
    target = repo.create_plan("目标", "无名剑法", "无名枪法",
                              playstyle="玩法甲", activate=False)
    def attach(state):
        state.equipment_items["fp"] = {"type": "冠胄", "_fp": "fp"}
        state.plans[target.id].equipment["head"] = "fp"
    repo.update(attach)
    seen = []
    def fake_equipment_attrs(equipped, _config):
        seen.append(equipped)
        return CombatAttributes(min_outer=5.0)
    monkeypatch.setattr(
        "lvjiang.apps.yysls.core.graduation.scoring.equipment_attrs",
        fake_equipment_attrs)
    saved = []
    monkeypatch.setattr(
        "lvjiang.apps.yysls.config.save_play_style",
        lambda _school, _name, attrs: saved.append(attrs))
    _bind_scanned_loadout(engine, target.name, target.main_martial_art,
                          target.sub_martial_art)
    _save_scanned_base_attrs(engine, {
        "min_outer": 100.0, "max_outer": 200.0,
        "min_mingjin": 10.0, "max_mingjin": 20.0,
        "mingjin_pen": 0.0, "precision": 70.0, "crit_rate": 80.0,
        "_right_outer_valid": True,
        "_right_outer_pen_valid": True,
        "_right_attr_pen_valid": True,
    })
    assert seen == [{"head": {"type": "冠胄", "_fp": "fp",
                              "created_at": "", "updated_at": ""}}]
    assert saved[0]["min_outer"] == 95.0
