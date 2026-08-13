"""游戏配置对话框

独立窗口，管理装备配置、词条配置与流派配置。
"""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QDialog, QVBoxLayout

from .config_tab import GameConfigTab


class GameConfigDialog(QDialog):
    """游戏配置对话框"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("游戏配置")
        self.setMinimumSize(900, 700)
        self.resize(1200, 800)
        self.setWindowFlags(
            self.windowFlags()
            | Qt.WindowType.WindowMinimizeButtonHint
            | Qt.WindowType.WindowMaximizeButtonHint
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        self._tab = GameConfigTab()
        layout.addWidget(self._tab)
