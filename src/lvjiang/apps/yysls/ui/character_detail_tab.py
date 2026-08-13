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

# 统一的刷新按钮样式
_REFRESH_BTN_STYLE = (
    "QPushButton { background-color: #607D8B; color: white; font-size: 12px; "
    "padding: 4px; border-radius: 3px; }"
    "QPushButton:hover { background-color: #78909C; }"
)


class CharacterDetailTab(QWidget):
    """角色详情 Tab（暂为空面板，后续扩展）"""

    def __init__(self, host, parent=None):
        super().__init__(parent)
        self._host = host
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        # 刷新按钮（右上角）
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_refresh = QPushButton("刷新")
        btn_refresh.setFixedWidth(60)
        btn_refresh.setToolTip("刷新角色详情")
        btn_refresh.setStyleSheet(_REFRESH_BTN_STYLE)
        btn_refresh.clicked.connect(self._on_refresh)
        btn_row.addWidget(btn_refresh)
        layout.addLayout(btn_row)

        # 占位提示
        placeholder = QLabel("角色详情功能开发中…")
        placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        placeholder.setStyleSheet("color: #999; font-size: 14px; padding: 40px;")
        layout.addWidget(placeholder, stretch=1)

    def _on_refresh(self):
        """刷新角色详情（暂为空操作）"""
        pass
