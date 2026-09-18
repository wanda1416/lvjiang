"""用户导航按钮的更新器必须随按钮销毁：否则长寿命 host 的信号会打到已删除控件。"""
from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWidgets import QComboBox, QHBoxLayout, QWidget

from lvjiang.ui.user_toolbar import add_user_nav_buttons


class _Host(QObject):
    user_changed = pyqtSignal(str)

    def __init__(self) -> None:
        super().__init__()
        self.user_combo = QComboBox()
        self.user_combo.addItems(["a", "b", "c"])

    def navigate_user(self, _delta: int) -> None:
        pass


def test_nav_buttons_updater_dies_with_the_buttons(qtbot):
    """分析对话框每次都新建带导航按钮的预览页，关闭后再切换用户不能崩。"""
    from PyQt6 import sip
    host = _Host()
    owner = QWidget()
    row = QHBoxLayout(owner)
    add_user_nav_buttons(row, host)
    buttons = [row.itemAt(i).widget() for i in range(row.count())
               if row.itemAt(i).widget() is not None]
    assert len(buttons) == 2

    # 正常工作：位于首项时「上一个」禁用
    host.user_combo.setCurrentIndex(0)
    host.user_changed.emit("a")
    assert not buttons[0].isEnabled() and buttons[1].isEnabled()

    # 拥有按钮的页面被销毁（对话框关闭）
    sip.delete(owner)          # 连同子按钮一起销毁
    qtbot.wait(10)

    # host 仍然活着并继续发信号：必须静默，不能 RuntimeError
    host.user_combo.setCurrentIndex(2)
    host.user_changed.emit("c")
