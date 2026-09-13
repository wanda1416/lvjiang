"""游戏配置对话框的显式保存与未保存退出保护。"""

import copy

from PyQt6.QtWidgets import QDialogButtonBox, QMessageBox

from lvjiang.apps.yysls.config import get_game_config
from lvjiang.apps.yysls.ui.game_settings.config_dialog import GameConfigDialog


class _ManagerSpy:
    def __init__(self, data: dict):
        self.data = data
        self.saved: list[dict] = []

    def reload(self) -> None:
        pass

    def get_raw(self) -> dict:
        return copy.deepcopy(self.data)

    def save(self, data: dict) -> None:
        self.saved.append(copy.deepcopy(data))


def _dialog(monkeypatch, qtbot):
    manager = _ManagerSpy(get_game_config().get_raw())
    monkeypatch.setattr(
        "lvjiang.apps.yysls.ui.game_settings.config_dialog.get_game_config",
        lambda: manager,
    )
    dialog = GameConfigDialog()
    monkeypatch.setattr(dialog._tab, "save_auxiliary_config", lambda: None)
    qtbot.addWidget(dialog)
    return dialog, manager


def test_changes_stay_in_memory_until_bottom_save(monkeypatch, qtbot):
    dialog, manager = _dialog(monkeypatch, qtbot)
    save = dialog._buttons.button(QDialogButtonBox.StandardButton.Save)
    assert not save.isEnabled()

    panel = dialog._tab._basic_config_panel
    panel._cooldown_days.setValue(panel._cooldown_days.value() + 1)

    assert save.isEnabled()
    assert manager.saved == []
    save.click()
    assert len(manager.saved) == 1
    assert not save.isEnabled()


def test_exit_button_confirms_before_discarding_changes(monkeypatch, qtbot):
    dialog, _manager = _dialog(monkeypatch, qtbot)
    dialog.show()
    dialog._mark_dirty()
    answers = iter((QMessageBox.StandardButton.No, QMessageBox.StandardButton.Yes))
    monkeypatch.setattr(QMessageBox, "question", lambda *_a, **_kw: next(answers))

    dialog.reject()
    assert dialog.isVisible()
    assert dialog._dirty

    dialog.reject()
    assert not dialog.isVisible()


def test_title_bar_close_uses_same_unsaved_confirmation(monkeypatch, qtbot):
    dialog, _manager = _dialog(monkeypatch, qtbot)
    dialog.show()
    dialog._mark_dirty()
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *_a, **_kw: QMessageBox.StandardButton.No,
    )

    assert not dialog.close()
    assert dialog.isVisible()
