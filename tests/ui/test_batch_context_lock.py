"""Batch-time environment/layout interaction lock tests."""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QWidget

from lvjiang.ui.main.window import (
    _BATCH_CONTEXT_LOCK_MESSAGE,
    _BatchContextComboBox,
)


def test_locked_context_combo_rejects_click_without_modal_main_window(qtbot):
    host = QWidget()
    qtbot.addWidget(host)
    host.show()
    combo = _BatchContextComboBox(host)
    combo.addItems(["环境一", "环境二"])
    combo.show()
    combo.set_batch_locked(True)

    qtbot.mouseClick(combo, Qt.MouseButton.LeftButton)

    assert combo.currentIndex() == 0
    assert combo.isEnabled() is False
    notice = combo._batch_lock_notice
    assert notice.text() == _BATCH_CONTEXT_LOCK_MESSAGE
    assert notice.isVisible()
    assert not notice.isModal()
    assert notice.windowModality() == Qt.WindowModality.NonModal
    assert host.isEnabled()
    notice.close()


def test_unlocked_context_combo_can_open_normally(qtbot, monkeypatch):
    combo = _BatchContextComboBox()
    qtbot.addWidget(combo)
    combo.set_batch_locked(False)
    opened = []
    monkeypatch.setattr(combo, "showPopup", lambda: opened.append(True))

    qtbot.mouseClick(combo, Qt.MouseButton.LeftButton)

    assert opened == [True]
    assert combo.isEnabled() is True
