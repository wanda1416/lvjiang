"""燕云「角色详情」Tab —— 暂时留空，后续展示角色持有装备等信息。"""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ....i18n import tr
from .profile.tab import REFRESH_BTN_STYLE as _REFRESH_BTN_STYLE
from .profile.tab import add_user_nav_buttons


class CharacterDetailTab(QWidget):
    """角色详情 Tab（暂为空面板，后续扩展）"""

    def __init__(self, host, parent=None):
        super().__init__(parent)
        self._host = host
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        # 刷新按钮
        btn_row = QHBoxLayout()
        btn_refresh = QPushButton(tr("刷新"))
        btn_refresh.setFixedWidth(60)
        btn_refresh.setToolTip(tr("刷新角色详情"))
        btn_refresh.setStyleSheet(_REFRESH_BTN_STYLE)
        btn_refresh.clicked.connect(self._on_refresh)
        btn_row.addWidget(btn_refresh)
        add_user_nav_buttons(btn_row, self._host)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        # 占位提示
        placeholder = QLabel(tr("角色详情功能开发中…"))
        placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        placeholder.setStyleSheet("color: #999; font-size: 14px; padding: 40px;")
        layout.addWidget(placeholder, stretch=1)

    def _on_refresh(self):
        """刷新角色详情（暂为空操作）"""
        pass
