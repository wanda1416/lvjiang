"""游戏配置 - 主容器

包含三个 Tab 页：
- 词组配置（最基础的配置，不依赖任何 tab）
- 装备配置（基础属性规则 + 武器类型注册）
- 流派配置
"""

from PyQt6.QtWidgets import QTabWidget, QVBoxLayout, QWidget

from .affix_caps_panel import AffixCapsPanel
from .base_attr_panel import BaseAttrPanel
from .school_panel import SchoolPanel


class GameConfigTab(QWidget):
    """游戏配置主面板"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        # Tab 页
        self._tabs = QTabWidget()

        # 词组配置面板（最基础，不依赖任何 tab）
        self._affix_panel = AffixCapsPanel()
        self._tabs.addTab(self._affix_panel, "词组配置")

        # 装备配置面板（基础属性 + 武器类型）
        self._base_panel = BaseAttrPanel()
        self._tabs.addTab(self._base_panel, "装备配置")

        # 流派配置面板
        self._school_panel = SchoolPanel()
        self._tabs.addTab(self._school_panel, "流派配置")

        layout.addWidget(self._tabs)
