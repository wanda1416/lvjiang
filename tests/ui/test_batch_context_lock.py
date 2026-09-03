"""顶部上下文选择器的锁定行为（批量运行 / 选中方案，可叠加）。"""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QWidget

from lvjiang.ui.main.run_control import LOCK_REASON_BATCH, LOCK_REASON_PLAN
from lvjiang.ui.main.window import (
    _BATCH_CONTEXT_LOCK_MESSAGE,
    _PLAN_CONTEXT_LOCK_MESSAGE,
    _ContextComboBox,
)


def test_locked_context_combo_rejects_click_without_modal_main_window(qtbot):
    host = QWidget()
    qtbot.addWidget(host)
    host.show()
    combo = _ContextComboBox(host)
    combo.addItems(["环境一", "环境二"])
    combo.show()
    combo.set_locked(LOCK_REASON_BATCH, True)

    qtbot.mouseClick(combo, Qt.MouseButton.LeftButton)

    assert combo.currentIndex() == 0
    assert combo.isEnabled() is False
    notice = combo._lock_notice
    assert notice.text() == _BATCH_CONTEXT_LOCK_MESSAGE
    assert notice.isVisible()
    assert not notice.isModal()
    assert notice.windowModality() == Qt.WindowModality.NonModal
    assert host.isEnabled()
    notice.close()


def test_unlocked_context_combo_can_open_normally(qtbot, monkeypatch):
    combo = _ContextComboBox()
    qtbot.addWidget(combo)
    combo.set_locked(LOCK_REASON_BATCH, False)
    opened = []
    monkeypatch.setattr(combo, "showPopup", lambda: opened.append(True))

    qtbot.mouseClick(combo, Qt.MouseButton.LeftButton)

    assert opened == [True]
    assert combo.isEnabled() is True


def test_plan_lock_survives_batch_unlock(qtbot):
    """批量结束时的解锁不能把方案锁一起解掉。"""
    combo = _ContextComboBox()
    qtbot.addWidget(combo)
    combo.set_locked(LOCK_REASON_PLAN, True)
    combo.set_locked(LOCK_REASON_BATCH, True)

    combo.set_locked(LOCK_REASON_BATCH, False)

    assert combo.is_locked()
    assert combo.isEnabled() is False
    assert combo.lock_message() == _PLAN_CONTEXT_LOCK_MESSAGE


def test_combo_unlocks_only_after_every_reason_cleared(qtbot):
    combo = _ContextComboBox()
    qtbot.addWidget(combo)
    combo.set_locked(LOCK_REASON_PLAN, True)
    combo.set_locked(LOCK_REASON_BATCH, True)

    combo.set_locked(LOCK_REASON_PLAN, False)
    assert combo.isEnabled() is False

    combo.set_locked(LOCK_REASON_BATCH, False)
    assert combo.isEnabled() is True
    assert combo.lock_message() == ""
    assert combo.toolTip() == ""


def test_batch_message_wins_while_both_locks_held(qtbot):
    combo = _ContextComboBox()
    qtbot.addWidget(combo)
    combo.set_locked(LOCK_REASON_PLAN, True)
    combo.set_locked(LOCK_REASON_BATCH, True)

    assert combo.lock_message() == _BATCH_CONTEXT_LOCK_MESSAGE


def test_releasing_an_unheld_reason_is_harmless(qtbot):
    combo = _ContextComboBox()
    qtbot.addWidget(combo)
    combo.set_locked(LOCK_REASON_PLAN, True)

    combo.set_locked(LOCK_REASON_BATCH, False)

    assert combo.isEnabled() is False
