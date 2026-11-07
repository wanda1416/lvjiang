from lvjiang.apps.yysls.ui.game_settings import equipment_set_panel
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
    assert panel._equipment_set_editor._title.text() == "攻具套装定义"
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


def _stub_add_dialog(monkeypatch, name: str) -> list[str]:
    """把新增对话框与警告弹窗换成可控实现，返回收到的警告文案。"""
    warnings: list[str] = []

    class _Input:
        @staticmethod
        def getText(*_args, **_kwargs):
            return name, True

    class _Message:
        @staticmethod
        def warning(_parent, _title, message):
            warnings.append(message)

    monkeypatch.setattr(equipment_set_panel, "QInputDialog", _Input)
    monkeypatch.setattr(equipment_set_panel, "QMessageBox", _Message)
    return warnings


def test_add_set_generates_unused_key_on_the_active_side(qtbot, monkeypatch):
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

    _stub_add_dialog(monkeypatch, "寒芒")
    editor._btn_add.click()

    left = data["equipment_sets"]["left"]
    assert "output_a" in left
    assert [entry for key, entry in left.items()
            if key != "output_a"] == [{"name": "寒芒"}]
    assert changes == [True]

    editor.set_series("armor")
    _stub_add_dialog(monkeypatch, "隐峦")
    editor._btn_add.click()

    right = data["equipment_sets"]["right"]
    added_key = next(key for key in right if key != "armor_a")
    assert right[added_key] == {"name": "隐峦"}
    # key 必须全局唯一：左右两侧同 key 会让推荐关系与配装引用指向同一身份。
    assert added_key not in left


def test_add_set_rejects_duplicate_name(qtbot, monkeypatch):
    data = {"equipment_sets": {"left": {"output_a": {"name": "输出甲"}},
                               "right": {}}}
    editor = EquipmentSetEditor(data=data)
    qtbot.addWidget(editor)

    warnings = _stub_add_dialog(monkeypatch, "输出甲")
    editor._btn_add.click()

    assert list(data["equipment_sets"]["left"]) == ["output_a"]
    assert warnings == ["「输出甲」套装已存在。"]


def _stub_delete_dialog(monkeypatch, *, answer: bool) -> list[str]:
    """把删除确认换成可控实现，返回收到的确认文案。"""
    from PyQt6.QtWidgets import QMessageBox as RealMessageBox

    questions: list[str] = []

    class _Message:
        StandardButton = RealMessageBox.StandardButton

        @staticmethod
        def question(_parent, _title, message, *_args, **_kwargs):
            questions.append(message)
            return (RealMessageBox.StandardButton.Yes if answer
                    else RealMessageBox.StandardButton.No)

        @staticmethod
        def information(_parent, _title, message):
            questions.append(message)

    monkeypatch.setattr(equipment_set_panel, "QMessageBox", _Message)
    return questions


def test_delete_armor_set_clears_dangling_relationship(qtbot, monkeypatch):
    data = {"equipment_sets": {
        "left": {"output_a": {"name": "输出甲", "recommended_right": "custom_2"}},
        "right": {"custom_2": {"name": "防具乙"}},
    }}
    changes: list[bool] = []
    editor = EquipmentSetEditor(
        data=data, on_changed=lambda: changes.append(True))
    qtbot.addWidget(editor)
    editor.set_series("armor")
    editor._table.setCurrentCell(0, 0)

    questions = _stub_delete_dialog(monkeypatch, answer=True)
    editor._btn_delete.click()

    assert data["equipment_sets"]["right"] == {}
    # 关联关系存在攻具一侧；删掉防具套装必须解开，不能留悬空引用。
    assert data["equipment_sets"]["left"]["output_a"]["recommended_right"] == ""
    assert changes == [True]
    assert "输出甲" in questions[0]


def test_delete_set_cancelled_keeps_definition(qtbot, monkeypatch):
    data = {"equipment_sets": {"left": {}, "right": {"custom_1": {"name": "防具甲"}}}}
    changes: list[bool] = []
    editor = EquipmentSetEditor(
        data=data, on_changed=lambda: changes.append(True))
    qtbot.addWidget(editor)
    editor.set_series("armor")
    editor._table.setCurrentCell(0, 0)

    _stub_delete_dialog(monkeypatch, answer=False)
    editor._btn_delete.click()

    assert data["equipment_sets"]["right"] == {"custom_1": {"name": "防具甲"}}
    assert changes == []


def test_new_set_after_delete_does_not_backfill_number(qtbot, monkeypatch):
    """编号不补空号：删掉的 key 可能仍留在任务历史与装备记录里。"""
    data = {"equipment_sets": {
        "left": {},
        "right": {"custom_1": {"name": "防具甲"}, "custom_3": {"name": "防具丙"}},
    }}
    editor = EquipmentSetEditor(data=data)
    qtbot.addWidget(editor)
    editor.set_series("armor")
    editor._table.setCurrentCell(0, 0)
    _stub_delete_dialog(monkeypatch, answer=True)
    editor._btn_delete.click()
    assert list(data["equipment_sets"]["right"]) == ["custom_3"]

    _stub_add_dialog(monkeypatch, "防具丁")
    editor._btn_add.click()

    # 空出来的 custom_1 不会被新套装占用，新编号接在最大值后面。
    assert sorted(data["equipment_sets"]["right"]) == ["custom_3", "custom_4"]
