"""创建基础属性对话框的名称输入契约。"""

from lvjiang.apps.yysls.ui.loadout.combat.play_style_dialog import (
    _CreatePlayStyleDialog,
)


def test_name_selector_allows_new_name_and_existing_replacement(qtbot):
    dialog = _CreatePlayStyleDialog(
        school_attr="鸣金",
        existing_names=["会心小外", "会意大外"],
    )
    qtbot.addWidget(dialog)

    assert dialog._combo_name.isEditable()
    assert dialog._combo_name.currentIndex() == -1
    assert dialog.get_play_style_name() == ""
    assert [
        dialog._combo_name.itemText(index)
        for index in range(dialog._combo_name.count())
    ] == ["会心小外", "会意大外"]

    dialog._edit_name.setText("新基础属性")
    assert dialog.get_play_style_name() == "新基础属性"

    dialog._combo_name.setCurrentIndex(1)
    assert dialog.get_play_style_name() == "会意大外"
