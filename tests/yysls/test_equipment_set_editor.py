from lvjiang.apps.yysls.ui.game_settings.config_tab import GameConfigTab
from lvjiang.apps.yysls.ui.game_settings.equipment_set_panel import (
    EquipmentSetEditor,
)


def _headers(editor: EquipmentSetEditor) -> list[str]:
    return [
        editor._table.horizontalHeaderItem(column).text()
        for column in range(editor._table.columnCount())
    ]


def test_equipment_sets_are_embedded_in_output_and_armor_pages(qtbot):
    tab = GameConfigTab()
    qtbot.addWidget(tab)

    assert "套装配置" not in [
        tab._tabs.tabText(index) for index in range(tab._tabs.count())
    ]

    panel = tab._base_panel
    panel._part_list.setCurrentRow(0)
    assert panel._equipment_set_editor._title.text() == "输出套装定义"
    assert _headers(panel._equipment_set_editor) == [
        "套装名称", "推荐流派", "基础属性",
    ]

    panel._part_list.setCurrentRow(4)
    assert panel._equipment_set_editor._title.text() == "防具套装定义"
    assert _headers(panel._equipment_set_editor) == [
        "套装名称", "推荐流派", "关联套装",
    ]


def test_armor_view_edits_the_existing_output_relationship(qtbot):
    data = {
        "equipment_sets": {
            "left": {
                "output_a": {
                    "name": "输出甲",
                    "recommended_right": "armor_a",
                    "recommended_school": "流派甲",
                    "two_piece_affix": "会心率",
                },
            },
            "right": {"armor_a": {"name": "防具甲"}},
        },
    }
    changes: list[bool] = []
    editor = EquipmentSetEditor(
        data=data, on_changed=lambda: changes.append(True))
    qtbot.addWidget(editor)

    editor.set_series("armor")
    assert editor._table.item(0, 1).text() == "流派甲"
    assert editor._table.item(0, 2).text() == "输出甲"

    editor._table.item(0, 1).setText("流派乙")

    assert changes
    assert data["equipment_sets"]["left"]["output_a"] == {
        "name": "输出甲",
        "recommended_right": "armor_a",
        "recommended_school": "流派乙",
        "two_piece_affix": "会心率",
    }
