"""手机装备查看：只读评分与桌面一致，缺失装备不能伪装为零毕业率。"""
import json

import pytest

from lvjiang.apps.yysls.config import get_game_config
from lvjiang.apps.yysls.config.equipment_slots import SLOT_SPECS
from lvjiang.apps.yysls.core.graduation.context import PlanScoringContext
from lvjiang.apps.yysls.core.loadout.models import LoadoutPlan, LoadoutState
from lvjiang.apps.yysls.ondevice import loadout_view


@pytest.fixture
def saved_loadout(tmp_path, monkeypatch):
    from lvjiang import constants
    from lvjiang.apps.yysls.config import play_styles
    from lvjiang.apps.yysls.core.graduation import context
    from lvjiang.core.ondevice import task_runner

    monkeypatch.setattr(constants, "USERS_DIR", tmp_path)
    monkeypatch.setattr(task_runner, "_local_user_names", lambda: ["viewer"])
    base = {"基准属性": {"min_outer": 1200, "max_outer": 3000, "intent_rate": .2}}
    monkeypatch.setattr(play_styles, "get_play_styles", lambda school: base)
    monkeypatch.setattr(context, "get_play_styles", lambda school: base)
    gc = get_game_config()
    arts = gc.get_schools()["鸣金·虹"]
    plan = LoadoutPlan(id="plan", name="扫描方案", main_martial_art=arts["sub"]["martial_art"],
                       sub_martial_art=arts["main"]["martial_art"], base_attribute="基准属性", graduation_scheme="基础方案")
    items = {}
    for slot in SLOT_SPECS:
        fingerprint = slot.key
        plan.equipment[slot.key] = fingerprint
        items[fingerprint] = {"name": slot.label, "type": "剑" if slot.is_weapon else slot.part,
                              "level": 110, "original_level": 110,
                              "affix_1": {"name": "最大外功攻击", "value": 100},
                              "dingyin": {"name": "会心率", "value": 5, "unit": "%"}}
    state = LoadoutState(world_level=110, active_plan_id=plan.id, plans={plan.id: plan}, equipment_items=items)
    path = tmp_path / "viewer.loadouts.json"
    path.write_text(json.dumps(state.to_dict(), ensure_ascii=False), encoding="utf-8")
    return path, state, gc


def test_phone_snapshot_score_matches_shared_scorer_and_does_not_write(saved_loadout):
    path, state, gc = saved_loadout
    before = path.read_bytes()
    context = PlanScoringContext.from_plan(state.active_plan, game_config=gc, world_level=110)
    expected = context.scorer(game_config=gc).rate(state.resolved_equipment())
    result = json.loads(loadout_view.plan_view("viewer", "plan", "基础方案", "基准属性"))
    assert result["ok"] and not result["reasons"]
    assert result["result"]["graduation_rate"] == pytest.approx(expected)
    assert result["model"]["level"] == 110
    assert len(result["slots"]) == len(SLOT_SPECS)
    assert path.read_bytes() == before


def test_incomplete_plan_keeps_equipment_visible_without_a_false_rate(saved_loadout):
    path, state, _ = saved_loadout
    state.active_plan.equipment["head"] = None
    state.equipment_items["main_weapon"]["is_mock"] = True
    path.write_text(json.dumps(state.to_dict()), encoding="utf-8")
    result = json.loads(loadout_view.plan_view("viewer", "plan"))
    assert result["ok"] and result["result"] is None
    assert any("冠胄" in reason for reason in result["reasons"])
    assert result["contains_mock"]
    assert result["slots"][0]["equipment"]["name"]


def test_pool_shows_scanned_affixes_and_model_override_does_not_save(saved_loadout):
    path, _, _ = saved_loadout
    before = path.read_bytes()
    pool = json.loads(loadout_view.equipment_pool("viewer"))
    assert pool["total"] == len(SLOT_SPECS)
    assert any(row["label"] == "最大外功攻击" and row["value"] == "100"
               for section in pool["items"][0]["sections"] for row in section["items"])
    assert any(row["label"] == "会心率" and row["value"] == "5%"
               for section in pool["items"][0]["sections"] for row in section["items"])
    invalid = json.loads(loadout_view.plan_view("viewer", "plan", "不存在的计算方案"))
    assert not invalid["ok"]
    assert path.read_bytes() == before


def test_four_assumptions_share_projected_score_and_combat_values_without_writing(saved_loadout):
    from lvjiang.apps.yysls.core.graduation.assumptions import Assumptions

    path, state, gc = saved_loadout
    before = path.read_bytes()
    flags = dict.fromkeys(("full_level", "full_chengyin", "full_dingyin", "simulate_transmute"), True)
    result = json.loads(loadout_view.plan_view("viewer", "plan", assumptions=json.dumps(flags)))
    context = PlanScoringContext.from_plan(state.active_plan, game_config=gc, world_level=110)
    projected = Assumptions(full_level=110, full_chengyin=True, full_dingyin=True, simulate_transmute=True).project(state.resolved_equipment(), gc)
    expected = context.scorer(game_config=gc).rate(projected)
    assert result["result"]["graduation_rate"] == pytest.approx(expected)
    assert len(result["assumption_labels"]) == 4
    assert all(option["checked"] for option in result["assumptions"])
    assert any(item["label"] == "精准率" and item["effective"].endswith("%") for item in result["combat_attributes"])
    assert path.read_bytes() == before
