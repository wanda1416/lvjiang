"""新建方案的流派快捷填充、无序玩法筛选与创建保存。"""

from lvjiang.apps.yysls.core.loadout.models import LoadoutPlan
from lvjiang.apps.yysls.core.loadout.repository import LoadoutRepository
from lvjiang.apps.yysls.ui.loadout.plan_create_dialog import PlanCreateDialog


class _GameConfig:
    def __init__(self):
        self.schools = {
            "流派甲": {
                "main": {"martial_art": "武学甲"},
                "sub": {"martial_art": "武学乙"},
            },
            "流派乙": {
                "main": {"martial_art": "武学丙"},
                "sub": {"martial_art": "武学丁"},
            },
        }
        self.playstyles = {
            "甲玩法": {"school": "流派甲", "arts": ["武学乙", "武学甲"]},
            "别派玩法": {"school": "流派乙", "arts": ["武学甲", "武学乙"]},
            "混搭玩法": {"school": "", "arts": ["武学丙", "武学甲"]},
        }

    def get_schools(self):
        return self.schools

    def get_martial_arts(self):
        return {name: {} for name in ("武学甲", "武学乙", "武学丙", "武学丁")}

    def get_playstyles_for_arts(self, arts):
        selected = {art for art in arts if art}
        return [name for name, item in self.playstyles.items()
                if selected and set(item["arts"]) == selected]

    def get_playstyle(self, name):
        return self.playstyles.get(name)


def _options(combo):
    return [combo.itemData(i) for i in range(combo.count())]


def test_school_fills_arts_and_swapping_does_not_change_playstyle(qtbot):
    dialog = PlanCreateDialog(_GameConfig())
    qtbot.addWidget(dialog)
    dialog._combo_school.setCurrentIndex(
        dialog._combo_school.findData("流派甲"))
    assert (dialog.main_art, dialog.sub_art) == ("武学甲", "武学乙")
    assert _options(dialog._combo_playstyle) == ["", "甲玩法"]
    dialog._combo_playstyle.setCurrentIndex(1)

    dialog._combo_main.setCurrentText("武学乙")
    dialog._combo_sub.setCurrentText("武学甲")
    assert dialog._combo_school.currentData() == ""
    assert _options(dialog._combo_playstyle) == [
        "", "甲玩法", "别派玩法"]
    assert dialog.playstyle == "甲玩法"


def test_manual_registered_pair_keeps_school_unselected(qtbot):
    dialog = PlanCreateDialog(_GameConfig())
    qtbot.addWidget(dialog)
    dialog._combo_main.setCurrentText("武学乙")
    dialog._combo_sub.setCurrentText("武学甲")
    assert dialog._combo_school.currentData() == ""
    assert _options(dialog._combo_playstyle) == [
        "", "甲玩法", "别派玩法"]


def test_manual_mixed_arts_can_choose_playstyle_and_save(qtbot, tmp_path):
    dialog = PlanCreateDialog(_GameConfig())
    qtbot.addWidget(dialog)
    assert dialog._combo_school.currentData() == ""
    dialog._edit_name.setText("混搭方案")
    dialog._combo_main.setCurrentText("武学甲")
    dialog._combo_sub.setCurrentText("武学丙")
    assert dialog._combo_school.currentData() == ""
    assert _options(dialog._combo_playstyle) == ["", "混搭玩法"]
    dialog._combo_playstyle.setCurrentIndex(1)
    dialog._validate_and_accept()
    assert dialog.result() == dialog.DialogCode.Accepted

    repo = LoadoutRepository("test_user", users_dir=tmp_path)
    plan = repo.create_plan(dialog.plan_name, dialog.main_art,
                            dialog.sub_art, playstyle=dialog.playstyle)
    assert repo.load().plans[plan.id].playstyle == "混搭玩法"


def test_edit_named_school_locks_arts_until_custom_selected(qtbot):
    plan = LoadoutPlan(
        id="plan-id", name="原方案", main_martial_art="武学乙",
        sub_martial_art="武学甲", playstyle="甲玩法")
    dialog = PlanCreateDialog(_GameConfig(), plan=plan)
    qtbot.addWidget(dialog)
    assert dialog._combo_school.currentData() == "流派甲"
    assert (dialog.main_art, dialog.sub_art) == ("武学乙", "武学甲")
    assert not dialog._combo_main.isEnabled()
    assert not dialog._combo_sub.isEnabled()
    assert dialog.playstyle == "甲玩法"

    dialog._combo_school.setCurrentIndex(0)
    assert dialog._combo_main.isEnabled()
    assert dialog._combo_sub.isEnabled()
    dialog._combo_main.setCurrentText("武学甲")
    dialog._combo_sub.setCurrentText("武学丙")
    assert _options(dialog._combo_playstyle) == ["", "混搭玩法"]


def test_edit_keeps_unknown_existing_arts_and_playstyle(qtbot):
    plan = LoadoutPlan(
        id="plan-id", name="旧方案", main_martial_art="旧武学甲",
        sub_martial_art="旧武学乙", playstyle="旧玩法")
    dialog = PlanCreateDialog(_GameConfig(), plan=plan)
    qtbot.addWidget(dialog)
    dialog._edit_name.setText("新名称")
    assert (dialog.main_art, dialog.sub_art, dialog.playstyle) == (
        "旧武学甲", "旧武学乙", "旧玩法")
    dialog._validate_and_accept()
    assert dialog.result() == dialog.DialogCode.Accepted
