"""属性管理对话框

独立窗口，管理装备基础属性规则和词条属性上限。
"""

from PyQt6.QtWidgets import QDialog, QVBoxLayout
from PyQt6.QtCore import Qt

from .attr_tab import AttrManagerTab


class AttrManagerDialog(QDialog):
    """属性管理对话框"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("属性管理")
        self.setMinimumSize(900, 700)
        self.resize(1200, 800)
        self.setWindowFlags(
            self.windowFlags()
            | Qt.WindowType.WindowMinimizeButtonHint
            | Qt.WindowType.WindowMaximizeButtonHint
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        self._tab = AttrManagerTab()
        layout.addWidget(self._tab)
