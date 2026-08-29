"""游戏配置对话框

独立窗口，管理装备配置、词条配置与流派配置。
"""

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import QDialog, QVBoxLayout

from .....i18n import tr
from ...config import get_game_config
from .config_tab import GameConfigTab


class GameConfigDialog(QDialog):
    """游戏配置对话框"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("游戏配置"))
        self.setMinimumSize(900, 700)
        self.resize(1200, 800)
        self.setWindowFlags(
            self.windowFlags()
            | Qt.WindowType.WindowMinimizeButtonHint
            | Qt.WindowType.WindowMaximizeButtonHint
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        # manager 是进程级单例；每次打开都重读一次，以便发现
        # 两次对话框之间的外部改动。YAML 未变时只做 stat + 缓存拷贝。
        get_game_config().reload()
        self._tab = GameConfigTab()
        layout.addWidget(self._tab)

    def select_school_base_attr(self, school: str, base_attr: str) -> None:
        """显示后定位到流派配置及指定基础属性。"""
        QTimer.singleShot(
            0, lambda: self._tab.select_school_base_attr(school, base_attr),
        )
