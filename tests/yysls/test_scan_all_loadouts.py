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
    _scanned_loadout_names,
    _set_scanned_loadout_gongjue,
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


def test_scanned_gongjue_writes_only_bound_plan(tmp_path):
    engine = _engine(tmp_path)
    repo = LoadoutRepository(engine.run_username, tmp_path)
    active = repo.load().active_plan_id
    target = repo.create_plan("方案甲", "无名剑法", "无名枪法", activate=False)
    with pytest.raises(ValueError, match="绑定写入目标"):
        _set_scanned_loadout_gongjue(engine, "会意")
    _bind_scanned_loadout(engine, target.name, target.main_martial_art,
                          target.sub_martial_art)
    with pytest.raises(ValueError, match="无法识别"):
        _set_scanned_loadout_gongjue(engine, "未知")
    assert _set_scanned_loadout_gongjue(engine, "精准") == "精准"
    state = repo.load()
    assert state.plans[target.id].gongjue == "精准"
    assert state.active_plan_id == active
    assert state.plans[active].gongjue == ""


def test_existing_plan_names_snapshot_does_not_change_active_plan(tmp_path):
    engine = _engine(tmp_path)
    repo = LoadoutRepository(engine.run_username, tmp_path)
    active = repo.load().active_plan_id
    repo.create_plan("方案甲", "无名剑法", "无名枪法", activate=False)
    snapshot = _scanned_loadout_names(engine)
    assert snapshot["方案甲"] is True
    repo.create_plan("方案乙", "无名剑法", "无名枪法", activate=False)
    assert "方案乙" not in snapshot
    assert repo.load().active_plan_id == active


@pytest.mark.parametrize(
    ("detail", "expected"),
    [
        ("弓玦套装 | 会意", "会意"),
        ("弓玦套装 | 会心", "会心"),
        ("弓玦套装 | 精准", "精准"),
        ("未识别到套装", None),
        ("会意 | 会心", None),
    ],
)
def test_bow_detail_dsl_updates_gongjue_only_when_unique(
        tmp_path, monkeypatch, detail, expected):
    """运行生产 DSL 的弓扫描段，确保不会凭空猜测或改动活动方案。"""
    proc = parse_file(
        Path("config/system/workflows/subcall/loadout/equipped.wf")
    ).procs["scan_equipped_slots"]
    engine = make_engine()
    engine.run_username = "test_user"
    engine.users_dir = tmp_path
    repo = LoadoutRepository(engine.run_username, tmp_path)
    active = repo.load().active_plan_id
    target = repo.create_plan("方案甲", "无名剑法", "无名枪法", activate=False)
    _bind_scanned_loadout(engine, target.name, target.main_martial_art,
                          target.sub_martial_art)
    engine.variables = {"written": 8}
    monkeypatch.setattr(engine, "_exec_click", lambda _node: None)
    monkeypatch.setattr(engine, "_exec_wait", lambda _node: None)
    monkeypatch.setattr(engine, "_exec_scan", lambda _node: engine.variables.update(
        bow_detail={"equip_detail": detail}))
    with pytest.raises(_ReturnSignal) as returned:
        engine._exec_body(proc.body[3:])
    assert returned.value.value == (8 if expected else -1)
    state = repo.load()
    assert state.plans[target.id].gongjue == (expected or "")
    assert state.active_plan_id == active


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
        "_right_attr_attack_valid": True,
        "_right_outer_pen_valid": True,
        "_right_attr_pen_valid": True,
    }
    with pytest.raises(ValueError, match="右侧详情"):
        _save_scanned_base_attrs(engine, {**parsed, "_right_outer_valid": False})
    with pytest.raises(ValueError, match="右侧详情"):
        _save_scanned_base_attrs(engine, {**parsed, "_right_attr_attack_valid": False})
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
        "subcall/loadout/game_plans.wf",
        "subcall/loadout/equipped.wf",
        "subcall/loadout/role_attrs.wf",
        "subcall/loadout/equipment_scan.wf",
    ):
        parse_file(base / path)
    assert {"scan_equipped_plan", "scan_equipped_slots"} <= set(
        parse_file(base / "subcall/loadout/equipped.wf").procs)
    assert {"scan_role_base_attr_for_plan", "capture_role_base_attrs"} <= set(
        parse_file(base / "subcall/loadout/role_attrs.wf").procs)


def test_batch_skips_all_existing_plans_before_switching(monkeypatch):
    workflow_path = Path("config/system/workflows/scan_all_loadouts.wf")
    workflow = parse_file(workflow_path)
    params = {item["name"]: item for item in
              parse_metadata_file(workflow_path)["parameters"]}
    assert params["skip_existing"]["default"] is False
    engine = make_engine()
    engine.variables = {"skip_existing": True}
    calls = []

    def fake_call(node):
        calls.append(node.name)
        assert node.name in {
            "nav_main_to_game_plans", "collect_game_plan_names",
            "nav_game_plans_to_main",
        }
        result = {
            "nav_main_to_game_plans": 0,
            "collect_game_plan_names": ["方案甲", "方案乙"],
            "nav_game_plans_to_main": 0,
        }[node.name]
        engine.variables[node.result_var] = result

    original_eval = engine._exec_eval

    def fake_eval(node):
        if node.func_name == "scanned_loadout_names":
            engine.variables[node.target] = {"方案甲": True, "方案乙": True}
        else:
            original_eval(node)

    monkeypatch.setattr(engine, "_exec_call_proc", fake_call)
    monkeypatch.setattr(engine, "_exec_eval", fake_eval)
    with pytest.raises(_ReturnSignal) as returned:
        engine._exec_body(workflow.body)
    assert returned.value.value == 0
    assert calls == ["nav_main_to_game_plans", "collect_game_plan_names",
                     "nav_game_plans_to_main"]
    assert engine.variables["skipped_existing"] == 2.0


def test_batch_continues_after_one_plan_base_attr_failure(monkeypatch):
    workflow = parse_file(Path("config/system/workflows/scan_all_loadouts.wf"))
    engine = make_engine()
    calls = []

    def fake_call(node):
        name = engine._resolve(node.args[0]) if node.args else None
        calls.append((node.name, name))
        result = {
            "nav_main_to_game_plans": 0,
            "collect_game_plan_names": ["方案甲", "方案乙"],
            "nav_game_plans_to_main": 0,
            "scan_equipped_plan": 8,
            "scan_role_base_attr_for_plan": -2 if name == "方案甲" else "已保存",
        }.get(node.name)
        if node.name == "select_game_plan":
            result = {"name": name, "main_art": "武学甲", "sub_art": "武学乙"}
        if node.result_var is not None:
            engine.variables[node.result_var] = result

    original_eval = engine._exec_eval

    def fake_eval(node):
        if node.func_name == "ensure_scanned_loadout":
            name = engine.variables["selected"]["name"]
            engine.variables[node.target] = {
                "ok": True, "name": name, "main_art": "武学甲",
                "sub_art": "武学乙", "playstyle": "玩法甲",
            }
        else:
            original_eval(node)

    monkeypatch.setattr(engine, "_exec_call_proc", fake_call)
    monkeypatch.setattr(engine, "_exec_eval", fake_eval)
    with pytest.raises(_ReturnSignal) as returned:
        engine._exec_body(workflow.body)
    assert returned.value.value == 0
    assert [name for proc, name in calls if proc == "scan_role_base_attr_for_plan"] == [
        "方案甲", "方案乙"]


@pytest.mark.parametrize("scan_kind", ["equipment", "base_attrs"])
@pytest.mark.parametrize("safe_home", [True, False])
def test_plan_scan_recovers_from_data_error_and_returns_to_main(
        monkeypatch, scan_kind, safe_home):
    path = Path("config/system/workflows/subcall/loadout") / (
        "equipped.wf" if scan_kind == "equipment"
        else "role_attrs.wf")
    proc_name = ("scan_equipped_plan" if scan_kind == "equipment"
                 else "scan_role_base_attr_for_plan")
    proc = parse_file(path).procs[proc_name]
    engine = make_engine()
    engine.variables = {
        "name": "方案甲", "main_art": "武学甲", "sub_art": "武学乙",
        "silent_write": True, "scroll_count": 8,
    }
    calls = []
    clicked = []

    def fake_call(node):
        calls.append(node.name)
        if node.name == "scan_equipped_slots":
            raise ValueError("装备数据不完整")
        result = {
            "nav_main_to_equip": 0, "nav_back_to_main": 0 if safe_home else -1,
            "nav_main_to_role": 0,
            "capture_role_base_attrs": {"min_outer": 100.0},
            "is_in_main_page": 1,
        }[node.name]
        if node.result_var is not None:
            engine.variables[node.result_var] = result

    original_eval = engine._exec_eval

    def fake_eval(node):
        if node.func_name == "bind_scanned_loadout":
            return
        if node.func_name == "save_scanned_base_attrs":
            raise ValueError("角色属性右侧详情识别不完整")
        original_eval(node)

    monkeypatch.setattr(engine, "_exec_call_proc", fake_call)
    monkeypatch.setattr(engine, "_exec_eval", fake_eval)
    monkeypatch.setattr(engine, "_exec_scan", lambda node: engine.variables.update({
        node.target.name: "" if safe_home else "back",
    }))
    monkeypatch.setattr(engine, "_exec_click", lambda node: clicked.append(node))
    monkeypatch.setattr(engine, "_exec_wait", lambda _node: None)
    with pytest.raises(_ReturnSignal) as returned:
        engine._exec_body(proc.body)
    assert returned.value.value == (-2 if safe_home else -1)
    if scan_kind == "equipment":
        assert calls[-1] == "nav_back_to_main"
    else:
        assert "nav_main_to_menu" not in calls
        assert calls[-1] == ("is_in_main_page" if safe_home else "capture_role_base_attrs")
        assert len(clicked) == 2


@pytest.mark.parametrize("needs_switch", [True, False])
def test_select_game_plan_does_not_use_button_text_as_success_check(
        monkeypatch, needs_switch):
    """切换后无弹窗即可继续；当前方案没有“另存为”也不误报失败。"""
    proc = parse_file(Path(
        "config/system/workflows/subcall/loadout/game_plans.wf"
    )).procs["select_game_plan"]
    engine = make_engine()
    engine.variables = {"name": "测试方案"}
    clicked = []
    found_targets = []

    def fake_find(node):
        found_targets.append(node.var_name)
        engine.variables[node.var_name] = "测试方案"

    def fake_scan(node):
        values = {
            "detail": {"plan_title": "测试方案", "main_art": "武学甲",
                       "sub_art": "武学乙"},
            "use": 1 if needs_switch else 0,
            "message": {"modal_message": ""},
        }
        engine.variables[node.target.name] = values[node.target.name]

    monkeypatch.setattr(engine, "_exec_find", fake_find)
    monkeypatch.setattr(engine, "_exec_scan", fake_scan)
    monkeypatch.setattr(engine, "_exec_click", lambda node: clicked.append(node))
    monkeypatch.setattr(engine, "_exec_wait", lambda _node: None)
    with pytest.raises(_ReturnSignal) as returned:
        engine._exec_body(proc.body)
    assert returned.value.value == {
        "name": "测试方案", "main_art": "武学甲", "sub_art": "武学乙",
    }
    assert found_targets == ["found"]
    assert len(clicked) == (2 if needs_switch else 1)


@pytest.mark.parametrize(
    ("main_checks", "retry_answers", "expected", "prompt_count"),
    [
        ([1], [], 0, 0),
        ([0, 1], [True], 0, 1),
        ([0, 0, 1], [True, True], 0, 2),
        ([0], [False], -1, 1),
    ],
)
def test_return_from_game_plans_checks_only_home_and_prompts_on_mismatch(
        monkeypatch, main_checks, retry_answers, expected, prompt_count):
    """返回路径不依赖中间页 OCR；终点失配由用户决定重试或停止。"""
    proc = parse_file(Path(
        "config/system/workflows/subcall/loadout/game_plans.wf"
    )).procs["nav_game_plans_to_main"]
    engine = make_engine()
    clicked = []
    checks = iter(main_checks)
    answers = iter(retry_answers)
    prompts = []

    monkeypatch.setattr(engine, "_exec_click", lambda node: clicked.append(node))
    monkeypatch.setattr(engine, "_exec_wait", lambda _node: None)

    def fake_call(node):
        assert node.name == "is_in_main_page"
        engine.variables[node.result_var] = next(checks)

    original_eval = engine._exec_eval

    def fake_eval(node):
        if node.func_name == "confirm":
            prompts.append(node)
            engine.variables[node.target] = next(answers)
        else:
            original_eval(node)

    monkeypatch.setattr(engine, "_exec_call_proc", fake_call)
    monkeypatch.setattr(engine, "_exec_eval", fake_eval)
    with pytest.raises(_ReturnSignal) as returned:
        engine._exec_body(proc.body)
    assert returned.value.value == expected
    assert len(clicked) == 3
    assert len(prompts) == prompt_count


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
    assert back.to == "training_main"
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
        assert not main_layout.get("panels", [])
        assert {panel["key"] for panel in layout["panels"]} == {"plan_list"}
        button = next(region for region in layout["regions"]
                      if region["key"] == "smart_fill")
        assert all(button[key] > 0 for key in ("w_ratio", "h_ratio"))
        assert button.get("activation_key") == (
            "SPACE" if platform == "desktop" else None)

    navigation = Path(
        "config/system/workflows/subcall/loadout/game_plans.wf"
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
    navigation_text = (base / "subcall/loadout/game_plans.wf").read_text(
        encoding="utf-8")
    assert "loadout_scan_targets" not in batch_text
    assert "for name in $names\n    if $skip_existing" in batch_text
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
        "_right_attr_attack_valid": True,
        "_right_outer_pen_valid": True,
        "_right_attr_pen_valid": True,
    })
    assert seen == [{"head": {"type": "冠胄", "_fp": "fp",
                              "created_at": "", "updated_at": ""}}]
    assert saved[0]["min_outer"] == 95.0
