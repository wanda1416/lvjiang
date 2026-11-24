"""备战方案管理的未保存草稿。

编辑只改内存草稿，点「保存」才写回；写回按字段合并，不能把对话框打开期间
外部写入的字段一起回滚。关闭时先问「保存并关闭／放弃修改／取消」。
"""

from pathlib import Path

import pytest
from PyQt6.QtWidgets import QApplication

from lvjiang.apps.yysls.core.loadout import LoadoutRepository
from lvjiang.apps.yysls.ui.loadout.plan_manager_dialog import PlanManagerDialog
from lvjiang.apps.yysls.ui.loadout.plan_table_delegate import COL_NAME

USER = "tester"


@pytest.fixture
def repo(tmp_path: Path) -> LoadoutRepository:
    repository = LoadoutRepository(USER, tmp_path)
    repository.create_plan("甲方案", "无名剑法", "斩雪刀法", playstyle="走地玉")
    repository.create_plan("乙方案", "无名枪法", "斩雪刀法", playstyle="威威")
    # 首次写入前仓储会给一份「默认方案」占位，这里清掉，让断言只面对两套。
    state = repository.load()
    for plan_id, plan in state.plans.items():
        if plan.name == "默认方案":
            repository.delete_plan(plan_id)
    return repository


@pytest.fixture
def dialog(qtbot, tmp_path: Path, repo) -> PlanManagerDialog:
    widget = PlanManagerDialog([USER], USER, tmp_path)
    qtbot.addWidget(widget)
    widget.resize(1000, 560)
    widget.show()
    QApplication.processEvents()
    return widget


def _disk_names(repo: LoadoutRepository) -> list[str]:
    state = repo.load()
    return [state.plans[pid].name for pid in state.ordered_plan_ids()]


def _edit_name(dialog: PlanManagerDialog, row: int, name: str) -> None:
    dialog._commit_field(dialog._row_plans[row], COL_NAME, name)


# ─── 草稿 ─────────────────────────────────────────────────

def test_edit_waits_in_memory_until_saved(dialog, repo):
    """编辑后磁盘原样不动，点保存才落盘。"""
    _edit_name(dialog, 0, "改过的甲方案")

    assert _disk_names(repo) == ["甲方案", "乙方案"]
    assert dialog._dirty_users() == [USER]
    assert dialog._save_button.isEnabled()

    assert dialog._save() is True

    assert _disk_names(repo) == ["改过的甲方案", "乙方案"]
    assert not dialog._save_button.isEnabled()
    assert dialog.changed_users == {USER}


def test_dirty_marks_clear_once_saved(dialog, repo):
    """保存过一轮后按钮回到灰，不再被算作未保存修改。"""
    _edit_name(dialog, 0, "第一次")
    assert dialog._save() is True

    _edit_name(dialog, 0, "第二次")
    assert dialog._save_button.isEnabled()

    assert dialog._save() is True
    assert _disk_names(repo) == ["第二次", "乙方案"]
    assert dialog._dirty_users() == []


def test_world_level_change_waits_for_save(dialog, repo):
    """个人世界等级同样属于草稿，保存后落盘。"""
    dialog._draft().set_world_level(100)
    dialog._update_actions()

    assert repo.load().world_level is None
    assert dialog._save() is True
    assert repo.load().world_level == 100


def test_save_merges_fields_written_outside(dialog, repo):
    """保存只覆盖本次改过的字段，对话框外写进来的字段必须保留。"""
    _edit_name(dialog, 0, "改过的甲方案")
    plan_id = repo.load().ordered_plan_ids()[0]
    repo.configure_plan(plan_id, combat_type="pvp")

    assert dialog._save() is True

    saved = repo.load().plans[plan_id]
    assert saved.name == "改过的甲方案"
    assert saved.combat_type == "pvp"


def test_new_delete_and_order_apply_on_save(dialog, repo):
    """新建、删除、排序都在草稿里做完，保存时才一次性生效。"""
    draft = dialog._draft()
    created = draft.add_plan(name="丙方案", main_art="无名剑法",
                             sub_art="斩雪刀法")
    draft.remove_plan(draft.state.ordered_plan_ids()[0])
    dialog._load_user(selected_id=created.id)

    assert _disk_names(repo) == ["甲方案", "乙方案"]
    assert dialog._save() is True

    state = repo.load()
    names = [state.plans[pid].name for pid in state.ordered_plan_ids()]
    assert names == ["乙方案", "丙方案"]


# ─── 关闭前的确认 ──────────────────────────────────────────

def test_close_without_changes_does_not_ask(dialog, monkeypatch):
    called: list[str] = []
    monkeypatch.setattr(PlanManagerDialog, "_ask_unsaved",
                        lambda self: called.append("ask") or "cancel")

    dialog.reject()

    assert called == []
    assert not dialog.isVisible()


def test_discard_keeps_disk_unchanged(dialog, repo, monkeypatch):
    monkeypatch.setattr(PlanManagerDialog, "_ask_unsaved",
                        lambda self: "discard")
    _edit_name(dialog, 0, "改过的甲方案")

    dialog.reject()

    assert not dialog.isVisible()
    assert _disk_names(repo) == ["甲方案", "乙方案"]
    assert dialog.changed_users == set()


def test_cancel_keeps_dialog_and_draft(dialog, monkeypatch):
    monkeypatch.setattr(PlanManagerDialog, "_ask_unsaved",
                        lambda self: "cancel")
    _edit_name(dialog, 0, "改过的甲方案")

    dialog.reject()

    assert dialog.isVisible()
    assert dialog._dirty_users() == [USER]


def test_save_on_close_writes_and_closes(dialog, repo, monkeypatch):
    monkeypatch.setattr(PlanManagerDialog, "_ask_unsaved", lambda self: "save")
    _edit_name(dialog, 0, "改过的甲方案")

    dialog.reject()

    assert not dialog.isVisible()
    assert _disk_names(repo) == ["改过的甲方案", "乙方案"]
    assert dialog.changed_users == {USER}
