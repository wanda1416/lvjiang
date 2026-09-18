"""主窗口用户页面共用的工具栏按钮与样式。"""

from __future__ import annotations

from PyQt6.QtCore import QObject, QTimer, pyqtSlot
from PyQt6.QtWidgets import QHBoxLayout, QPushButton

from ..i18n import tr
from .button_styles import ACTION_BUTTON_STYLE

REFRESH_BTN_STYLE = (
    "QPushButton { background-color: #607D8B; color: white; font-size: 12px; "
    "padding: 4px; border-radius: 3px; }"
    "QPushButton:hover { background-color: #78909C; }"
)
NAV_BTN_STYLE = (
    "QPushButton { background-color: #546E7A; color: white; font-size: 12px; "
    "padding: 2px 6px; border-radius: 3px; }"
    "QPushButton:hover { background-color: #78909C; }"
    "QPushButton:disabled { background-color: #B0BEC5; }"
)
USER_TOOLBAR_BTN_STYLE = (
    "QPushButton { background-color: palette(button); color: palette(button-text); "
    "font-size: 12px; border: 1px solid palette(mid); padding: 2px 6px; "
    "border-radius: 3px; }"
    "QPushButton:hover { background-color: palette(midlight); border-color: palette(mid); }"
    "QPushButton:pressed { background-color: palette(mid); }"
    "QPushButton:disabled { color: palette(mid); border-color: palette(midlight); }"
)
USER_ACTION_BTN_STYLE = ACTION_BUTTON_STYLE
_USER_TOOLBAR_BTN_HEIGHT = 36


class _NavButtonState(QObject):
    """按当前用户位置启停「上一个/下一个用户」按钮；生命周期跟随按钮。"""

    def __init__(self, btn_prev: QPushButton, btn_next: QPushButton, host,
                 parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._btn_prev = btn_prev
        self._btn_next = btn_next
        self._host = host

    @pyqtSlot()
    @pyqtSlot(int)
    @pyqtSlot(str)
    def update_enabled(self, *_args) -> None:
        idx = self._host.user_combo.currentIndex()
        count = self._host.user_combo.count()
        self._btn_prev.setEnabled(idx > 0)
        self._btn_next.setEnabled(idx < count - 1)


def add_user_nav_buttons(
    btn_row: QHBoxLayout,
    host,
    *,
    button_style: str = NAV_BTN_STYLE,
    fixed_height: int | None = None,
) -> None:
    btn_row.addSpacing(6)
    btn_prev = QPushButton("◀")
    btn_prev.setFixedWidth(28)
    if fixed_height is not None:
        btn_prev.setFixedHeight(fixed_height)
    btn_prev.setToolTip(tr("上一个用户"))
    btn_prev.setStyleSheet(button_style)
    btn_prev.clicked.connect(lambda: host.navigate_user(-1))
    btn_row.addWidget(btn_prev)

    btn_next = QPushButton("▶")
    btn_next.setFixedWidth(28)
    if fixed_height is not None:
        btn_next.setFixedHeight(fixed_height)
    btn_next.setToolTip(tr("下一个用户"))
    btn_next.setStyleSheet(button_style)
    btn_next.clicked.connect(lambda: host.navigate_user(1))
    btn_row.addWidget(btn_next)

    # 更新器必须随按钮一起销毁：host 是长寿命的主窗口，这里若连的是裸闭包，
    # 拥有按钮的页面（如分析对话框里每次新建的预览属性页）关闭后闭包仍挂在
    # host.user_changed 上，下一次切换用户就会对已删除的 QPushButton 调用
    # setEnabled 而崩溃。把槽放在以按钮为 parent 的 QObject 上，Qt 会在按钮
    # 销毁时自动断开连接。
    updater = _NavButtonState(btn_prev, btn_next, host, parent=btn_prev)
    host.user_combo.currentIndexChanged.connect(updater.update_enabled)
    host.user_changed.connect(updater.update_enabled)
    QTimer.singleShot(0, updater.update_enabled)


def add_user_toolbar_refresh_button(
    btn_row: QHBoxLayout, refresh_callback, *, refresh_tooltip: str
) -> None:
    btn_refresh = QPushButton(tr("刷新"))
    btn_refresh.setFixedSize(60, _USER_TOOLBAR_BTN_HEIGHT)
    btn_refresh.setToolTip(refresh_tooltip)
    btn_refresh.setStyleSheet(USER_TOOLBAR_BTN_STYLE)
    btn_refresh.clicked.connect(refresh_callback)
    btn_row.addWidget(btn_refresh)


def add_user_toolbar_buttons(
    btn_row: QHBoxLayout, host, refresh_callback, *, refresh_tooltip: str
) -> None:
    add_user_toolbar_refresh_button(
        btn_row,
        refresh_callback,
        refresh_tooltip=refresh_tooltip,
    )
    add_user_nav_buttons(
        btn_row,
        host,
        button_style=USER_TOOLBAR_BTN_STYLE,
        fixed_height=_USER_TOOLBAR_BTN_HEIGHT,
    )
