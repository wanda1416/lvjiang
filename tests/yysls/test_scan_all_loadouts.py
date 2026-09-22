"""Game-plan scans must bind by name and use the scanned plan, not the UI active one."""
import json
from dataclasses import fields, is_dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest

from lvjiang.apps.yysls.config import get_game_config
from lvjiang.apps.yysls.core.combat.combat_attrs import CombatAttributes
from lvjiang.apps.yysls.core.loadout import LoadoutRepository
from lvjiang.apps.yysls.workflows.builtins.equipment_ingest import (
    _bind_scanned_loadout,
    _ensure_scanned_loadout,
    _loadout_scan_target,
    _write_equipped,
)
from lvjiang.apps.yysls.workflows.builtins.role_attr_ingest import (
    _save_scanned_base_attrs,
)
from lvjiang.core.scene_definition import SceneRegistry
from lvjiang.workflows.engine.signals import _ReturnSignal
from lvjiang.workflows.grammar import parse_file
from lvjiang.workflows.grammar.ast_nodes import CallProc
from lvjiang.workflows.metadata import parse_metadata_file
from tests.workflows.conftest import make_engine


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
    target = _ensure_scanned_loadout(engine, "方案甲", "无名枪法", "无名剑法")
    assert target == {
        "name": "方案甲", "main_art": "无名枪法",
        "sub_art": "无名剑法", "playstyle": "玩法甲",
        "ok": True, "created": False,
    }
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
    result = _ensure_scanned_loadout(engine, "同名", "无名剑法", "无名枪法")
    assert result == {"ok": False, "reason": "本地存在同名备战方案，无法确定写入目标"}
    with pytest.raises(ValueError, match="匹配 2 个"):
        _bind_scanned_loadout(engine, "同名", "无名剑法", "无名枪法")
    assert "_bound_loadout_plan_id" not in engine.context


def test_game_plan_creates_local_plan_and_infers_playstyle_without_activation(tmp_path):
    engine = _engine(tmp_path)
    repo = LoadoutRepository(engine.run_username, tmp_path)
    active = repo.load().active_plan_id
    for name, expected_style in (("火拳奶", "火拳"),
                                 ("纯奶PVE", "纯奶"),
                                 ("未命名方案", "火拳")):
        result = _ensure_scanned_loadout(engine, name, "千香引魂蛊", "明川药典")
        assert result["ok"] is True
        assert result["created"] is True
        assert result["playstyle"] == expected_style
        assert repo.load().active_plan_id == active
        again = _ensure_scanned_loadout(engine, name, "明川药典", "千香引魂蛊")
        assert again["created"] is False
        assert again["playstyle"] == expected_style
    assert len(repo.load().plans) == 4


def test_game_plan_same_name_with_other_arts_does_not_mutate_local_plan(tmp_path):
    engine = _engine(tmp_path)
    repo = LoadoutRepository(engine.run_username, tmp_path)
    plan = repo.create_plan("方案甲", "无名剑法", "无名枪法",
                            playstyle="玩法甲", activate=False)
    result = _ensure_scanned_loadout(engine, "方案甲", "明川药典", "千香引魂蛊")
    assert result == {"ok": False, "reason": "本地同名方案的武学与游戏不一致"}
    assert repo.load().plans[plan.id].playstyle == "玩法甲"


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
        "intent_rate": 22.8, "direct_crit": 9.2,
        "direct_intent": 1.5, "crit_dmg": 54.0,
        "intent_dmg": 35.0, "outer_bonus": 2.5,
        "attr_bonus_current": 15.0,
        "outer_pen": 58.4,
        "_right_outer_valid": True,
        "_right_outer_pen_valid": True,
        "_right_attr_pen_valid": True,
    }
    with pytest.raises(ValueError, match="右侧详情"):
        _save_scanned_base_attrs(engine, {**parsed, "_right_outer_valid": False})
    for missing in ("crit_dmg", "attr_bonus_current"):
        with pytest.raises(ValueError, match="增减伤属性识别不完整"):
            _save_scanned_base_attrs(engine, {
                key: value for key, value in parsed.items() if key != missing
            })
    assert saved == []
    assert _save_scanned_base_attrs(engine, parsed) == "test_user_方案甲"
    assert saved[0][0] == "鸣金·虹"
    assert saved[0][1] == "test_user_方案甲"
    values = saved[0][2]
    for field, expected in (
        ("precision", 0.7), ("crit_rate", 0.8),
        ("intent_rate", 0.228), ("direct_crit", 0.092),
        ("direct_intent", 0.015),
        ("crit_dmg", 0.54), ("intent_dmg", 0.35),
        ("outer_bonus", 0.025), ("mingjin_bonus", 0.15),
    ):
        assert values[field] == pytest.approx(expected)
    assert values["outer_pen"] == pytest.approx(58.4)
    assert parsed["precision"] == 70.0  # 不改写 OCR 原始数据
    assert repo.load().plans[plan.id].base_attribute == "test_user_方案甲"
    assert repo.load().active_plan_id == active
    assert get_game_config().get_school_attr(saved[0][0]) == "鸣金"

    second = repo.create_plan("方案乙", "无名枪法", "无名剑法",
                              playstyle="玩法甲", activate=False)
    _bind_scanned_loadout(engine, second.name, second.main_martial_art,
                          second.sub_martial_art)
    assert _save_scanned_base_attrs(
        engine, {**parsed, "min_outer": 120.0}) == "test_user_方案乙"
    assert repo.load().plans[second.id].base_attribute == "test_user_方案乙"
    assert [item[1] for item in saved] == ["test_user_方案甲", "test_user_方案乙"]
    assert saved[0][2]["min_outer"] == 100.0
    assert saved[1][2]["min_outer"] == 120.0


def test_workflow_and_shared_subcalls_parse():
    base = Path("config/system/workflows")
    for path in (
        "scan_all_loadouts.wf", "scan_equipped.wf",
        "standalone/scan_role_base_attr.wf",
        "subcall/loadout/loadout_plan_navigation.wf",
        "subcall/loadout/equipped_slots_scan.wf",
        "subcall/loadout/role_base_attr_scan.wf",
        "subcall/loadout/equipped_plan_scan.wf",
        "subcall/loadout/role_base_attr_plan_scan.wf",
    ):
        parse_file(base / path)


def test_game_plan_scene_loads_with_distinct_popup_views():
    registry = SceneRegistry()
    main = registry.get_scene("training_main")
    scene = registry.get_scene("training_plan")
    assert main is not None and scene is not None
    assert {view.key for view in main.views}.isdisjoint({"fangan", "fangan_fill", "fangan_confirm"})
    assert {view.key for view in scene.views} == {"base", "fill", "confirm"}
    entry = next(region for region in main.regions if region.key == "fangan")
    assert entry.to == "training_plan/base"
    back = next(region for region in scene.regions if region.key == "back")
    assert back.to == "training_main/base"
    fill = next(region for region in scene.regions
                if region.key == "smart_fill")
    assert fill.views == ["fill"]
    assert fill.is_text and fill.is_clickable

    for platform in ("android", "desktop"):
        base = Path(f"config/system/layouts/{platform}")
        main_layout = json.loads((base / "training_main.json").read_text(encoding="utf-8"))
        layout = json.loads((base / "training_plan.json").read_text(encoding="utf-8"))
        assert {region["key"] for region in main_layout["regions"]}.isdisjoint(
            {"plan_title", "main_art", "sub_art", "use_area", "modal_message", "smart_fill"})
        assert not main_layout["panels"]
        assert {panel["key"] for panel in layout["panels"]} == {"plan_list"}
        button = next(region for region in layout["regions"]
                      if region["key"] == "smart_fill")
        assert all(button[key] > 0 for key in ("w_ratio", "h_ratio"))
        assert button.get("activation_key") == (
            "SPACE" if platform == "desktop" else None)

    navigation = Path(
        "config/system/workflows/subcall/loadout/loadout_plan_navigation.wf"
    ).read_text(encoding="utf-8")
    assert 'scan [training_plan].[smart_fill] as $action by contains "智能填充"' in navigation
    assert "click [training_plan].[smart_fill]" in navigation
    assert "click [training_plan].[back]" in navigation


def test_direct_and_batch_workflows_call_the_same_parameterized_procedures():
    base = Path("config/system/workflows")
    equipment = parse_file(base / "scan_equipped.wf")
    role = parse_file(base / "standalone/scan_role_base_attr.wf")
    batch = parse_file(base / "scan_all_loadouts.wf")
    assert "scan_equipped_plan" in set(_calls(equipment.body))
    assert "scan_role_base_attr_for_plan" in set(_calls(role.body))
    assert {"scan_equipped_plan", "scan_role_base_attr_for_plan"} <= set(
        _calls(batch.body))
    assert {"collect_game_plan_names", "select_game_plan"} <= set(_calls(batch.body))
    batch_text = (base / "scan_all_loadouts.wf").read_text(encoding="utf-8")
    navigation_text = (base / "subcall/loadout/loadout_plan_navigation.wf").read_text(
        encoding="utf-8")
    assert "loadout_scan_targets" not in batch_text
    assert "for name in $names\n    if not $in_game_plans" in batch_text
    assert "if $selected == -1" in batch_text
    assert "继续在方案列表尝试下一套" in batch_text
    assert "if $selected == -2" in batch_text
    assert "len($name) > 0" in navigation_text
    assert "scroll [training_plan].[plan_list]" not in navigation_text
    assert "drag [training_plan].[plan_list]" not in navigation_text
    for path in (base / "scan_equipped.wf", base / "standalone/scan_role_base_attr.wf"):
        names = {item["name"] for item in parse_metadata_file(path)["parameters"]}
        assert {"plan_name", "main_art", "sub_art"} <= names


@pytest.mark.parametrize("unsafe_failure,second_succeeds,expected_names,expected_result", [
    (False, True, ["方案甲", "方案乙"], 0),
    (False, False, ["方案甲", "方案乙"], -1),
    (True, False, ["方案甲"], -1),
])
def test_batch_retries_next_plan_without_reentering_after_safe_failure(
    monkeypatch, unsafe_failure, second_succeeds, expected_names, expected_result,
):
    engine = make_engine()
    calls = []

    def fake_call(node):
        name = engine._resolve(node.args[0]) if node.args else None
        calls.append((node.name, name))
        result = {
            "nav_main_to_game_plans": 0,
            "collect_game_plan_names": ["方案甲", "方案乙"],
            "select_game_plan": (
                {"name": "方案乙", "main_art": "武学甲", "sub_art": "武学乙"}
                if name == "方案乙" and second_succeeds else
                -2 if name == "方案甲" and unsafe_failure else -1
            ),
            "nav_game_plans_to_main": 0,
            "scan_equipped_plan": 8,
        }[node.name]
        if node.result_var is not None:
            engine.variables[node.result_var] = result

    original_eval = engine._exec_eval

    def fake_eval(node):
        if node.func_name == "ensure_scanned_loadout":
            engine.variables[node.target] = {
                "ok": True, "name": "方案乙", "main_art": "武学甲",
                "sub_art": "武学乙", "playstyle": "",
            }
        else:
            original_eval(node)

    monkeypatch.setattr(engine, "_exec_call_proc", fake_call)
    monkeypatch.setattr(engine, "_exec_eval", fake_eval)
    workflow = parse_file(Path("config/system/workflows/scan_all_loadouts.wf"))
    with pytest.raises(_ReturnSignal) as returned:
        engine._exec_body(workflow.body)
    assert returned.value.value == expected_result
    assert [name for proc, name in calls if proc == "select_game_plan"] == expected_names
    assert sum(proc == "nav_main_to_game_plans" for proc, _ in calls) == 1
    assert sum(proc == "nav_game_plans_to_main" for proc, _ in calls) == int(
        not unsafe_failure)
    assert sum(proc == "scan_equipped_plan" for proc, _ in calls) == int(second_succeeds)


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
        "crit_dmg": 50.0, "intent_dmg": 40.0,
        "outer_bonus": 0.0, "attr_bonus_current": 15.0,
        "_right_outer_valid": True,
        "_right_outer_pen_valid": True,
        "_right_attr_pen_valid": True,
    })
    assert seen == [{"head": {"type": "冠胄", "_fp": "fp",
                              "created_at": "", "updated_at": ""}}]
    assert saved[0]["min_outer"] == 95.0
