"""图库空间展示名编辑框：多语言预填、清空回落 key、key 不可编辑。"""

from __future__ import annotations

from PyQt6.QtWidgets import QDialog

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


def test_language_rows_put_chinese_and_english_first(qtbot):
    """简体中文、英文排在最前两行——新建时这两行必填，不该埋在一堆语言里。"""
    dialog = SpaceNameDialog(None, creating=True)
    qtbot.addWidget(dialog)

    codes = list(dialog._edits)
    assert codes[:2] == ["zh_CN", "en_US"]


def test_create_mode_requires_key_and_both_languages(qtbot):
    """新建：key 必填且不能重名，简中与英文都必须填。"""
    from lvjiang.i18n import tr

    dialog = SpaceNameDialog(None, creating=True, existing_keys={"android"})
    qtbot.addWidget(dialog)
    dialog._edits["zh_CN"].setText("我的空间")
    dialog._edits["en_US"].setText("My Space")

    dialog._on_accept()                      # key 还是空的
    assert dialog.result() != int(QDialog.DialogCode.Accepted)
    assert dialog._error_label.text() == tr("空间 key 不能为空")

    dialog._key_edit.setText("android")      # 与已有空间重名
    dialog._on_accept()
    assert dialog._error_label.text() == tr("同名空间已存在")

    dialog._key_edit.setText("my space")     # 含空格之外先测非法字符
    dialog._key_edit.setText("my/space")
    dialog._on_accept()
    assert dialog._error_label.text() == tr("空间 key 不能包含路径分隔符或特殊字符")

    dialog._key_edit.setText("my_space")
    dialog._edits["en_US"].clear()
    dialog._on_accept()
    assert dialog._error_label.text() == tr("简体中文与英文都必须填写")
    assert dialog.result() != int(QDialog.DialogCode.Accepted)

    dialog._edits["en_US"].setText("My Space")
    dialog._on_accept()
    assert dialog.result() == int(QDialog.DialogCode.Accepted)
    assert dialog.space_key() == "my_space"
    assert dialog.names() == {"zh_CN": "我的空间", "en_US": "My Space"}


def test_edit_mode_shows_key_disabled(qtbot):
    """编辑：key 展示但置灰不可编辑（存在但不可改，比藏起来清楚）。"""
    dialog = SpaceNameDialog(None, space_key="android",
                             current_name={"zh_CN": "手游"})
    qtbot.addWidget(dialog)

    assert dialog._key_edit.text() == "android"
    assert dialog._key_edit.isEnabled() is False
    assert dialog.space_key() == "android"
