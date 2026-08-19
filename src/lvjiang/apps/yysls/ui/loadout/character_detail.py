"""燕云「角色详情」Tab —— 战斗属性展示与反推。

包含战斗属性 Tab，支持：
- 选择玩法（流派下的不同 build）
- 选择弓玦套装
- 手动输入当前面板属性
- 反推基础属性（面板 - 装备 - 弓玦 → 保存为玩法）
"""
from __future__ import annotations

from PyQt6.QtWidgets import (
    QVBoxLayout,
    QWidget,
)

from .combat.attrs_tab import CombatAttrsTab


class CharacterDetailTab(QWidget):
    """角色详情 Tab（战斗属性）"""

    def __init__(self, host, parent=None):
        super().__init__(parent)
        self._host = host
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # 嵌入战斗属性 Tab
        self._combat_attrs_tab = CombatAttrsTab(self._host, self)
        layout.addWidget(self._combat_attrs_tab)
