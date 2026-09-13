"""游戏配置对话框的显式保存与未保存退出保护。"""

import copy

from PyQt6.QtWidgets import QDialogButtonBox, QLabel, QMessageBox

from lvjiang.apps.yysls.config import get_game_config
from lvjiang.apps.yysls.ui.game_settings.config_dialog import GameConfigDialog
from lvjiang.core.config.resolver import EntityOrigin


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
    discard = dialog._buttons.button(QDialogButtonBox.StandardButton.Discard)
    assert not save.isEnabled()
    assert not discard.isEnabled()

    version = dialog.findChild(QLabel, "game_config_version")
    assert version is not None
    assert "当前生效" in version.toolTip()

    panel = dialog._tab._basic_config_panel
    panel._cooldown_days.setValue(panel._cooldown_days.value() + 1)

    assert save.isEnabled()
    assert discard.isEnabled()
    assert manager.saved == []
    save.click()
    assert len(manager.saved) == 1
    assert not save.isEnabled()
    assert not discard.isEnabled()


def test_discard_restores_values_without_exiting(monkeypatch, qtbot):
    dialog, _manager = _dialog(monkeypatch, qtbot)
    dialog.show()
    panel = dialog._tab._basic_config_panel
    original = panel._cooldown_days.value()
    panel._cooldown_days.setValue(original + 1)
    discard = dialog._buttons.button(QDialogButtonBox.StandardButton.Discard)
    answers = iter((QMessageBox.StandardButton.No, QMessageBox.StandardButton.Yes))
    monkeypatch.setattr(QMessageBox, "question", lambda *_a, **_kw: next(answers))

    discard.click()
    assert dialog.isVisible()
    assert dialog._dirty

    discard.click()
    assert dialog.isVisible()
    assert not dialog._dirty
    assert not discard.isEnabled()
    assert dialog._tab._basic_config_panel._cooldown_days.value() == original


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


def test_version_label_reports_remote_and_layer_distribution(monkeypatch, qtbot):
    class Resolver:
        @staticmethod
        def describe_entity(_path):
            return EntityOrigin("remote", 3)

        @staticmethod
        def list_entity_origins(_path):
            return (
                EntityOrigin("local", None),
                EntityOrigin("remote", 3),
                EntityOrigin("system", 1),
            )

    monkeypatch.setattr(
        "lvjiang.apps.yysls.ui.game_settings.config_dialog.get_resolver",
        lambda: Resolver(),
    )
    dialog, _manager = _dialog(monkeypatch, qtbot)
    version = dialog.findChild(QLabel, "game_config_version")

    assert version.text() == "v3"
    assert "当前生效：远程 · v3" in version.toolTip()
    assert "本地 · -" in version.toolTip()
    assert "系统 · v1" in version.toolTip()
