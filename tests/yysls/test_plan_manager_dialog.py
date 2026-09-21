"""跨用户方案管理只修改选中用户及选中方案。"""

from PyQt6.QtWidgets import QDialog, QMessageBox

from lvjiang.apps.yysls.config import get_game_config
from lvjiang.apps.yysls.core.loadout import LoadoutRepository
from lvjiang.apps.yysls.ui.loadout import plan_manager_dialog as manager_module
from lvjiang.apps.yysls.ui.loadout.plan_manager_dialog import PlanManagerDialog


def test_switch_user_and_reorder_keep_both_active_plans(qtbot, tmp_path):
    alice = LoadoutRepository("alice", tmp_path)
    bob = LoadoutRepository("bob", tmp_path)
    alice_active = alice.load().active_plan_id
    bob_active = bob.load().active_plan_id
    first = bob.create_plan("第一套", "武学甲", "武学乙",
                            activate=False).id
    second = bob.create_plan("第二套", "武学丙", "武学丁",
                             activate=False).id

    dialog = PlanManagerDialog(
        ["alice", "bob"], "alice", tmp_path,
        game_config=get_game_config())
    qtbot.addWidget(dialog)
    assert dialog._users.currentItem().text() == "alice"
    assert not alice.path.exists()
    dialog._users.setCurrentRow(1)
    assert bob.load().active_plan_id == bob_active
    dialog._table.selectRow(2)
    dialog._move_plan(-1)
    assert bob.load().ordered_plan_ids() == [bob_active, second, first]
    assert bob.load().active_plan_id == bob_active
    assert alice.load().active_plan_id == alice_active
    assert dialog.changed_users == {"bob"}


def test_delete_plan_keeps_shared_equipment_and_only_target_user(
        qtbot, tmp_path, monkeypatch):
    alice = LoadoutRepository("alice", tmp_path)
    bob = LoadoutRepository("bob", tmp_path)
    target = bob.create_plan("待删方案", "武学甲", "武学乙",
                             activate=False).id
    def attach_item(state):
        state.equipment_items["item"] = {"_fp": "item"}
        state.plans[target].equipment["ring"] = "item"

    bob.update(attach_item)
    dialog = PlanManagerDialog(
        ["alice", "bob"], "bob", tmp_path,
        game_config=get_game_config())
    qtbot.addWidget(dialog)
    dialog._table.selectRow(1)
    monkeypatch.setattr(QMessageBox, "question", lambda *_args: QMessageBox.StandardButton.Yes)
    dialog._delete_plan()

    assert target not in bob.load().plans
    assert "item" in bob.load().equipment_items
    assert not alice.path.exists()
    assert dialog.changed_users == {"bob"}


def test_create_and_edit_in_manager_do_not_switch_active_plan(
        qtbot, tmp_path, monkeypatch):
    repo = LoadoutRepository("alice", tmp_path)
    active_id = repo.load().active_plan_id
    dialog = PlanManagerDialog(
        ["alice"], "alice", tmp_path, game_config=get_game_config())
    qtbot.addWidget(dialog)

    class FakePlanDialog:
        def __init__(self, _config, _parent, *, plan=None):
            self.plan_name = "已编辑" if plan else "新方案"
            self.main_art = "武学甲"
            self.sub_art = "武学乙"
            self.playstyle = "玩法甲"

        def exec(self):
            return QDialog.DialogCode.Accepted

    monkeypatch.setattr(manager_module, "PlanCreateDialog", FakePlanDialog)
    dialog._create_plan()
    state = repo.load()
    assert len(state.plans) == 2
    assert state.active_plan_id == active_id
    new_id = state.ordered_plan_ids()[1]
    assert state.plans[new_id].playstyle == "玩法甲"

    dialog._edit_plan(1, 0)
    state = repo.load()
    assert state.plans[new_id].name == "已编辑"
    assert state.active_plan_id == active_id
