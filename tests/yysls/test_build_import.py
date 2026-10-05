"""搭配导入不能删除旧装备、改变其他方案或留下半套写入。"""
import copy

import pytest
from PyQt6.QtWidgets import QMessageBox

from lvjiang.apps.yysls.config import get_game_config
from lvjiang.apps.yysls.config.builds import BuildDefinition, BuildRepository
from lvjiang.apps.yysls.core.combat.combat_attrs import apply_hypothetical_caps
from lvjiang.apps.yysls.core.graduation.scoring import equipment_attrs
from lvjiang.apps.yysls.core.loadout.build_import import import_build, materialize_build
from lvjiang.apps.yysls.core.loadout.repository import LoadoutRepository
from lvjiang.apps.yysls.ui.loadout.build_import import BuildImportDialog
from lvjiang.core.config.resolver import ConfigResolver
from tests.yysls.test_build_calculator import allocate, counts


@pytest.fixture
def setup_import(tmp_path):
    gc = get_game_config()
    repo = LoadoutRepository("test-user", tmp_path / "users")
    plan = repo.create_plan("导入目标", "无名剑法", "无名枪法", playstyle="无名")
    build = BuildDefinition.create("测试搭配", "无名", 115)
    build.equipment = allocate(counts() | {"劲": 9}).equipment
    build.gongjue = "会意"
    build.gongjue_level = 115
    return gc, repo, plan, build


def test_import_preserves_pool_other_plans_positions_and_reuses_mocks(setup_import):
    gc, repo, plan, build = setup_import
    other = repo.create_plan("其他方案", "无名剑法", "无名枪法", playstyle="无名", activate=False)
    old = {"_fp": "old-real", "type": "环", "level": 110}
    repo.assign_equipment(plan.id, "ring", old)
    repo.assign_equipment(other.id, "ring", old)
    repo.set_plan_equipment_set(plan.id, "ring", "old-set")
    before = repo.load()
    build_before = copy.deepcopy(build)
    equipment = materialize_build(build, gc)
    simulated = apply_hypothetical_caps(build_before.equipment, full_dingyin=True, playstyle=build.playstyle)
    assert equipment_attrs(equipment, gc) == equipment_attrs(simulated, gc)
    state = import_build(repo, plan.id, build, expected_plan=before.plans[plan.id].to_dict(), game_config=gc)
    assert build == build_before
    assert state.equipment_items["old-real"] == before.equipment_items["old-real"]
    assert state.plans[other.id] == before.plans[other.id]
    assert state.active_plan_id == before.active_plan_id
    assert state.world_level == before.world_level
    target = state.plans[plan.id]
    assert target.base_attribute == before.plans[plan.id].base_attribute
    assert target.graduation_scheme == before.plans[plan.id].graduation_scheme
    assert (target.gongjue, target.gongjue_level, target.combat_type) == ("会意", 115, "pve")
    assert "old-set" not in target.equipment_sets.values()
    for slot, equip in state.resolved_equipment(plan.id).items():
        assert equip["_fp"].startswith("mock_")
        assert equip["_extra"]["is_mock"]
        for i in range(1, 6):
            assert equip.get(f"affix_{i}") == equipment[slot].get(f"affix_{i}")
    pool = copy.deepcopy(state.equipment_items)
    again = import_build(repo, plan.id, build, expected_plan=target.to_dict(), game_config=gc)
    assert again.equipment_items == pool


def test_conflict_and_stale_plan_never_write_partial_import(setup_import):
    gc, repo, plan, build = setup_import
    equipment = materialize_build(build, gc)
    conflict = copy.deepcopy(equipment["wrist"])
    conflict["dingyin"]["value"] = 1
    repo.upsert_item(conflict)
    before = repo.load()
    with pytest.raises(ValueError, match="同指纹"):
        import_build(repo, plan.id, build, expected_plan=before.plans[plan.id].to_dict(), game_config=gc)
    assert repo.load() == before
    expected = before.plans[plan.id].to_dict()
    repo.assign_equipment(plan.id, "ring", {"_fp": "new-real", "type": "环", "level": 110})
    before = repo.load()
    with pytest.raises(ValueError, match="已修改"):
        import_build(repo, plan.id, build, expected_plan=expected, game_config=gc)
    assert repo.load() == before


def test_import_checks_level_and_unordered_arts(setup_import):
    gc, repo, plan, build = setup_import
    build.combat_type = "pvp"
    with pytest.raises(ValueError, match="只支持 PVE"):
        materialize_build(build, gc)
    build.combat_type = "pve"
    repo.set_world_level(110)
    before = repo.load()
    with pytest.raises(ValueError, match="高于"):
        import_build(repo, plan.id, build, expected_plan=before.plans[plan.id].to_dict(), game_config=gc)
    assert repo.load() == before
    repo.set_world_level(115)
    def reverse(state):
        p = state.plans[plan.id]
        p.main_martial_art, p.sub_martial_art = p.sub_martial_art, p.main_martial_art
    state = repo.update(reverse)
    state = import_build(repo, plan.id, build, expected_plan=state.plans[plan.id].to_dict(), game_config=gc)
    equipped = state.resolved_equipment(plan.id)
    assert equipped["main_weapon"]["type"] == "枪"
    assert equipped["sub_weapon"]["type"] == "剑"


def test_no_set_import_does_not_inherit_existing_mock_set(setup_import):
    gc, repo, plan, build = setup_import
    existing = materialize_build(build, gc)["ring"]
    existing["equipment_set"] = "existing-set"
    repo.upsert_item(existing)
    before = repo.load()
    with pytest.raises(ValueError, match="同指纹"):
        import_build(repo, plan.id, build, expected_plan=before.plans[plan.id].to_dict(), game_config=gc)
    assert repo.load() == before


def test_import_dialog_cancel_changes_nothing_and_success_applies(setup_import, tmp_path, qtbot, monkeypatch):
    gc, repo, plan, build = setup_import
    builds = BuildRepository(ConfigResolver(system_dir=tmp_path / "system", local_dir=tmp_path / "local", dev_mode=False))
    builds.save(build)
    repo.assign_equipment(plan.id, "ring", {"_fp": "old", "type": "环", "level": 110})
    dialog = BuildImportDialog("test-user", repo, builds=builds)
    qtbot.addWidget(dialog)
    before = repo.load()
    assert dialog.import_button.isEnabled()
    monkeypatch.setattr(QMessageBox, "question", lambda *_args: QMessageBox.StandardButton.Cancel)
    dialog._import()
    assert repo.load() == before
    monkeypatch.setattr(QMessageBox, "question", lambda *_args: QMessageBox.StandardButton.Yes)
    dialog._import()
    assert dialog.result() == dialog.DialogCode.Accepted
    assert repo.load().plans[plan.id].equipment["ring"] != "old"
    assert "old" in repo.load().equipment_items
