"""备战方案的对战类型，以及智能调律对 PVP 方案的过滤。

同时存在「无名PVE」和「无名PVP」两套同玩法方案时，智能调律会把两套都当成
匹配结果。PVP 方案的基线低，于是大量够不到 PVE 标准的装备被判成有提升——
这正是这个字段要解决的问题。目前没有 PVP 调律方案，所以只能默认过滤。
"""

from pathlib import Path

import pytest

from lvjiang.apps.yysls.core.loadout import LoadoutRepository
from lvjiang.apps.yysls.core.loadout.models import (
    COMBAT_TYPE_PVE,
    COMBAT_TYPE_PVP,
    LoadoutPlan,
    normalize_combat_type,
)


@pytest.mark.parametrize("raw,expected", [
    ("pvp", COMBAT_TYPE_PVP),
    ("PVP", COMBAT_TYPE_PVP),
    ("pve", COMBAT_TYPE_PVE),
    # 缺字段的历史方案、手改坏的 json 一律按 PVE——老方案都是 PVE 时代建的，
    # 猜成 PVP 会把它们从智能调律里整批剔除。
    (None, COMBAT_TYPE_PVE),
    ("", COMBAT_TYPE_PVE),
    ("pvx", COMBAT_TYPE_PVE),
    (123, COMBAT_TYPE_PVE),
])
def test_combat_type_normalizes_unknown_values_to_pve(raw, expected):
    assert normalize_combat_type(raw) == expected


def test_legacy_plan_without_the_field_reads_as_pve():
    plan = LoadoutPlan.from_dict("p1", {"name": "老方案"})
    assert plan.combat_type == COMBAT_TYPE_PVE


def test_combat_type_survives_a_round_trip(tmp_path: Path):
    repo = LoadoutRepository("alice", tmp_path)
    plan = repo.create_plan("无名PVP", "无名剑法", "无名枪法",
                            combat_type=COMBAT_TYPE_PVP, activate=False)

    reloaded = LoadoutRepository("alice", tmp_path).load().plans[plan.id]
    assert reloaded.combat_type == COMBAT_TYPE_PVP

    repo.configure_plan(plan.id, combat_type=COMBAT_TYPE_PVE)
    assert repo.load().plans[plan.id].combat_type == COMBAT_TYPE_PVE


def test_main_panel_creation_preserves_selected_combat_type(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from PyQt6.QtWidgets import QDialog

    from lvjiang.apps.yysls.ui.loadout import loadout_panel

    repo = LoadoutRepository("alice", tmp_path)
    monkeypatch.setattr(loadout_panel, "PlanCreateDialog", lambda *_: SimpleNamespace(
        exec=lambda: QDialog.DialogCode.Accepted,
        plan_name="新方案", main_art="无名剑法", sub_art="无名枪法",
        playstyle="无名", combat_type=COMBAT_TYPE_PVP,
    ))
    panel = SimpleNamespace(_repo=repo, refresh=lambda: None)
    loadout_panel.LoadoutPanel._create_plan(panel)
    assert repo.load().active_plan.combat_type == COMBAT_TYPE_PVP


def test_new_plan_defaults_to_pve(tmp_path: Path):
    repo = LoadoutRepository("alice", tmp_path)
    plan = repo.create_plan("无名", "无名剑法", "无名枪法", activate=False)

    assert repo.load().plans[plan.id].combat_type == COMBAT_TYPE_PVE


@pytest.mark.parametrize("name,expected", [
    ("无名PVP", COMBAT_TYPE_PVP),
    ("无名pvp", COMBAT_TYPE_PVP),
    ("无名PVE", COMBAT_TYPE_PVE),
    ("输出奶", COMBAT_TYPE_PVE),
])
def test_scanned_plan_infers_combat_type_from_the_game_name(
    tmp_path: Path, monkeypatch, name, expected,
):
    """游戏里 PVP 方案通常就叫「xxPVP」，照名字定下来省得扫完逐个改。"""
    from types import SimpleNamespace

    from lvjiang.apps.yysls.workflows.builtins import equipment_ingest

    engine = SimpleNamespace(run_username="alice", users_dir=tmp_path,
                            context={}, _ui_callback=None)
    monkeypatch.setattr(equipment_ingest, "_notify_equipment_changed",
                        lambda _engine: None)

    result = equipment_ingest._ensure_scanned_loadout(
        engine, name, "无名剑法", "无名枪法")

    assert result["ok"] is True
    plans = LoadoutRepository("alice", tmp_path).load().plans.values()
    plan = next(p for p in plans if p.name == name)
    assert plan.combat_type == expected
