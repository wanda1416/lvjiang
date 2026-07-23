"""属性管理 - 主容器

包含两个 Tab 页：
- 基础属性规则
- 词条属性上限
"""

from PyQt6.QtWidgets import QWidget, QVBoxLayout, QTabWidget

from .base_attr_panel import BaseAttrPanel
from .affix_caps_panel import AffixCapsPanel


class AttrManagerTab(QWidget):
    """属性管理主面板"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        # Tab 页
        self._tabs = QTabWidget()

        # 基础属性面板
        self._base_panel = BaseAttrPanel()
        self._tabs.addTab(self._base_panel, "基础属性")

        # 词条上限面板
        self._affix_panel = AffixCapsPanel()
        self._tabs.addTab(self._affix_panel, "词条上限")

        layout.addWidget(self._tabs)
