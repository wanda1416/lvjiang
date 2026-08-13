"""游戏配置 - 主容器

包含六个 Tab 页：
- 词组配置（最基础的配置，不依赖任何 tab）
- 装备配置（基础属性规则 + 武器类型注册）
- 流派配置
- 等级配置（按等级区分重置支持与材料要求）
- 赛季配置（管理游戏赛季时间与装备等级）
- 装备展示（卡片字号、高度、网格列数等外观参数）
"""

from PyQt6.QtWidgets import QTabWidget, QVBoxLayout, QWidget

from .....i18n import tr
from .affix_caps_panel import AffixCapsPanel
from .base_attr_panel import BaseAttrPanel
from .equip_display_panel import EquipDisplayPanel
from .level_config_panel import LevelConfigPanel
from .school_panel import SchoolPanel
from .season_config_panel import SeasonConfigPanel


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
        self._tabs.addTab(self._affix_panel, tr("词组配置"))

        # 装备配置面板（基础属性 + 武器类型）
        self._base_panel = BaseAttrPanel()
        self._tabs.addTab(self._base_panel, tr("装备配置"))

        # 流派配置面板
        self._school_panel = SchoolPanel()
        self._tabs.addTab(self._school_panel, tr("流派配置"))

        # 等级配置面板（按等级区分重置支持与材料要求）
        self._level_panel = LevelConfigPanel()
        self._tabs.addTab(self._level_panel, tr("等级配置"))

        # 赛季配置面板（管理游戏赛季时间与装备等级）
        self._season_panel = SeasonConfigPanel()
        self._tabs.addTab(self._season_panel, tr("赛季配置"))

        # 装备展示面板（卡片字号、高度、网格列数）
        self._equip_display_panel = EquipDisplayPanel()
        self._tabs.addTab(self._equip_display_panel, tr("装备展示"))

        # 等级配置保存后，刷新其他面板中的 LevelCombo
        self._level_panel.level_configs_saved.connect(self._refresh_level_combos)

        layout.addWidget(self._tabs)

    def _refresh_level_combos(self):
        """刷新所有面板中的 LevelCombo（等级配置变更后调用）"""
        self._affix_panel.refresh_level_combos()
        self._base_panel.refresh_level_combos()
        self._season_panel.refresh_level_combos()
