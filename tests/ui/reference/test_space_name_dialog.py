"""图库空间展示名编辑框：多语言预填、清空回落 key、key 不可编辑。"""

from __future__ import annotations

from lvjiang.i18n import current_language
from lvjiang.ui.reference.space_name_dialog import SpaceNameDialog


def test_prefills_localized_name_per_language(qtbot):
    dialog = SpaceNameDialog(
        None, space_key="android",
        current_name={"zh_CN": "手游", "en_US": "Mobile"})
    qtbot.addWidget(dialog)

    assert dialog._edits["zh_CN"].text() == "手游"
    assert dialog._edits["en_US"].text() == "Mobile"
    assert dialog.names() == {"zh_CN": "手游", "en_US": "Mobile"}


def test_literal_name_lands_on_current_language_row(qtbot):
    """旧的字面值名字不是「每种语言都叫这个」，只落在当前界面语言那行。"""
    dialog = SpaceNameDialog(None, space_key="legacy", current_name="旧名字")
    qtbot.addWidget(dialog)

    filled = {code: edit.text() for code, edit in dialog._edits.items()
              if edit.text()}
    assert filled == {current_language(): "旧名字"}


def test_empty_every_field_means_fall_back_to_key(qtbot):
    dialog = SpaceNameDialog(
        None, space_key="android", current_name={"zh_CN": "手游"})
    qtbot.addWidget(dialog)

    for edit in dialog._edits.values():
        edit.clear()
    assert dialog.names() is None


def test_unknown_language_tags_are_preserved(qtbot):
    """磁盘上有、但不在随包语言表里的标签原样带回，不能丢。"""
    dialog = SpaceNameDialog(
        None, space_key="android",
        current_name={"zh_CN": "手游", "xx_XX": "沿用"})
    qtbot.addWidget(dialog)

    dialog._edits["zh_CN"].setText("手机端")
    assert dialog.names() == {"zh_CN": "手机端", "xx_XX": "沿用"}
