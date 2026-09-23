"""模态弹窗防线本身的回归。

这条防线守的是测试的反馈回路：离屏模式下没人去点确定，一次意外的
``QMessageBox.warning`` 会让整个套件无限挂起，不报错也不超时，只能靠人
逐个文件去猜是哪条卡住。真实事故就是 UI 槽函数里一个 AttributeError 被
``except`` 接住后弹了警告框，本该是一条清晰的失败，结果全量停在 84% 二十
多分钟。

防线坏掉不会有任何用例变红——正因如此它必须有自己的用例。
"""

import pytest
from PyQt6.QtWidgets import (
    QDialog,
    QFileDialog,
    QInputDialog,
    QMessageBox,
)


@pytest.mark.parametrize("call", [
    lambda: QMessageBox.warning(None, "标题", "保存失败"),
    lambda: QMessageBox.critical(None, "标题", "保存失败"),
    lambda: QMessageBox.information(None, "标题", "提示"),
    lambda: QMessageBox.question(None, "标题", "确定吗"),
    lambda: QInputDialog.getText(None, "标题", "名称:"),
    lambda: QFileDialog.getOpenFileName(None, "选择文件"),
])
def test_blocking_dialog_entries_raise_instead_of_hanging(call):
    with pytest.raises(AssertionError, match="Qt 模态对话框"):
        call()


def test_dialog_exec_raises_instead_of_hanging(qtbot):
    dialog = QDialog()
    qtbot.addWidget(dialog)

    with pytest.raises(AssertionError, match="QDialog.exec"):
        dialog.exec()


def test_message_text_is_carried_into_the_failure(qtbot):
    """把弹窗正文带进报错，才能一眼看出被 except 兜住的原始错误是什么。"""
    with pytest.raises(AssertionError, match="没有 combat_type"):
        QMessageBox.warning(None, "保存失败", "没有 combat_type")


def test_a_test_can_still_stub_a_dialog_it_drives(monkeypatch, qtbot):
    """需要驱动对话框的用例照常打桩：测试体里的补丁晚于 fixture，自然覆盖。"""
    monkeypatch.setattr(
        QMessageBox, "question",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes)

    assert QMessageBox.question(
        None, "标题", "确定吗") is QMessageBox.StandardButton.Yes
