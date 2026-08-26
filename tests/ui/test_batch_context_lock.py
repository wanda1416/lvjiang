"""Batch-time environment/layout interaction lock tests."""

from PyQt6.QtCore import Qt

from lvjiang.ui.main.window import (
    _BATCH_CONTEXT_LOCK_MESSAGE,
    _BatchContextComboBox,
)


def test_locked_context_combo_rejects_click_and_shows_message(qtbot, monkeypatch):
    messages = []
    combo = _BatchContextComboBox()
    combo.addItems(["环境一", "环境二"])
    qtbot.addWidget(combo)
    combo.show()
    combo.set_batch_locked(True)
    monkeypatch.setattr(
        "lvjiang.ui.main.window.QMessageBox.information",
        lambda _parent, title, message: messages.append((title, message)),
    )

    qtbot.mouseClick(combo, Qt.MouseButton.LeftButton)

    assert combo.currentIndex() == 0
    assert combo.isEnabled() is False
    assert messages == [("提示", _BATCH_CONTEXT_LOCK_MESSAGE)]


def test_unlocked_context_combo_can_open_normally(qtbot, monkeypatch):
    combo = _BatchContextComboBox()
    qtbot.addWidget(combo)
    combo.set_batch_locked(False)
    opened = []
    monkeypatch.setattr(combo, "showPopup", lambda: opened.append(True))

    qtbot.mouseClick(combo, Qt.MouseButton.LeftButton)

    assert opened == [True]
    assert combo.isEnabled() is True
