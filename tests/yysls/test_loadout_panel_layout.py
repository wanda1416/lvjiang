from __future__ import annotations

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWidgets import QComboBox

from lvjiang.apps.yysls.ui.loadout.loadout_panel import LoadoutPanel


class _Users:
    @staticmethod
    def list_users() -> list[str]:
        return []


class _Host(QObject):
    user_changed = pyqtSignal(str)
    app_event = pyqtSignal(object)

    def __init__(self) -> None:
        super().__init__()
        self.user_manager = _Users()
        self.user_combo = QComboBox()

    @staticmethod
    def active_user_name() -> str:
        return ""

    @staticmethod
    def navigate_user(_delta: int) -> None:
        return None


def test_assumptions_share_public_metric_row(qtbot):
    panel = LoadoutPanel(_Host())
    qtbot.addWidget(panel)

    controls = [
        panel._assumption_layout.itemAt(index).widget()
        for index in range(3)
    ]
    assert [control.text() for control in controls] == [
        "满等级", "满承音", "满定音",
    ]
    assert all(
        control.parentWidget() is panel._assumption_card
        for control in controls
    )
    assert [panel._metrics_layout.stretch(index) for index in range(3)] == [
        2, 1, 1,
    ]
