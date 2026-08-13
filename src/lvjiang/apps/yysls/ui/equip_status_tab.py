"""燕云「装备数据」Tab —— 拆分为「当前装备」和「其他装备」两个子面板。"""
from __future__ import annotations

from loguru import logger
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .equip_status_panel import EquipStatusPanel
from .profile.tab import REFRESH_BTN_STYLE as _REFRESH_BTN_STYLE
from .profile.tab import add_user_nav_buttons
from ....i18n import tr


class _OtherEquipPage(QWidget):
    """其他装备页 —— 暂时留空，后续展示角色持有的其他装备。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        placeholder = QLabel(tr("其他装备展示开发中…"))
        placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        placeholder.setStyleSheet("color: #999; font-size: 14px; padding: 40px;")
        layout.addWidget(placeholder, stretch=1)


class EquipStatusTab(QWidget):
    """装备数据 Tab —— 包含「当前装备」和「其他装备」两个子面板。"""

    def __init__(self, host, parent=None):
        super().__init__(parent)
        self._host = host
        self._setup_ui()
        self._refresh_current()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        # 刷新按钮
        btn_row = QHBoxLayout()
        btn_refresh = QPushButton(tr("刷新"))
        btn_refresh.setFixedWidth(60)
        btn_refresh.setToolTip(tr("刷新装备数据"))
        btn_refresh.setStyleSheet(_REFRESH_BTN_STYLE)
        btn_refresh.clicked.connect(self._on_refresh)
        btn_row.addWidget(btn_refresh)
        add_user_nav_buttons(btn_row, self._host)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        # 子 Tab
        self._sub_tabs = QTabWidget()
        layout.addWidget(self._sub_tabs, stretch=1)

        # 当前装备 —— 复用 EquipStatusPanel
        self._current_equip_panel = EquipStatusPanel()
        self._sub_tabs.addTab(self._current_equip_panel, tr("当前装备"))

        # 其他装备 —— 暂时留空
        self._other_equip_page = _OtherEquipPage()
        self._sub_tabs.addTab(self._other_equip_page, tr("其他装备"))

        # 订阅宿主用户切换
        self._host.user_changed.connect(lambda _name: self._refresh_current())

    def _on_refresh(self):
        """刷新当前激活的子面板"""
        self._refresh_current()

    def _refresh_current(self):
        """从当前用户加载已装备数据并刷新当前装备面板"""
        from lvjiang.core.config import SessionManager

        user_name = self._host.active_user_name()
        if not user_name:
            self._current_equip_panel.refresh({})
            return
        try:
            data = SessionManager().load(user_name)
            equipped = data.get("equipped", {})
            self._current_equip_panel.refresh(equipped)
        except Exception as e:
            logger.error(f"加载用户装备数据失败: {e}")
            self._current_equip_panel.refresh({})
