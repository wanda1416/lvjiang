"""系统托盘混入类 - 最小化到状态栏、托盘图标三态、右键开始/暂停/结束

依赖主类提供:
    _run_state / automation_state_changed（RunControlMixin）、
    _on_start / _on_pause_resume / _on_stop（RunControlMixin）、
    windowTitle()（QMainWindow）

平时不常驻：托盘图标只在用户点了"最小化到状态栏"之后才 show()，
回到主界面就立刻 hide()，避免占用通知区域。
"""

from PyQt6.QtCore import QPointF, Qt
from PyQt6.QtGui import QColor, QIcon, QPainter, QPixmap, QPolygonF
from PyQt6.QtWidgets import QMenu, QSystemTrayIcon

from ...i18n import tr

# 托盘图标三态颜色：运行前(绿)/运行中(红)/暂停中(黄)，与 _refresh_run_button 的
# 按钮配色保持一致，方便用户建立"同一种颜色=同一种状态"的直觉。
_TRAY_ICON_COLORS = {
    "idle": "#4CAF50",
    "running": "#f44336",
    "paused": "#FFC107",
}


def _make_tray_icon(state: str) -> QIcon:
    """绘制托盘状态图标：idle=绿色播放三角，running=红色圆角块，paused=黄色双竖条。

    纯代码绘制而非引入图片资源，避免为三种纯色状态图标增加打包体积。
    """
    size = 64
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    color = QColor(_TRAY_ICON_COLORS.get(state, _TRAY_ICON_COLORS["idle"]))
    rect = pixmap.rect().adjusted(4, 4, -4, -4)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(color)
    painter.drawRoundedRect(rect, 14, 14)
    painter.setBrush(QColor("white"))
    cx, cy = rect.center().x(), rect.center().y()
    if state == "paused":
        bar_w, bar_h, gap = 8, 26, 8
        painter.drawRoundedRect(cx - gap - bar_w, cy - bar_h // 2, bar_w, bar_h, 2, 2)
        painter.drawRoundedRect(cx + gap, cy - bar_h // 2, bar_w, bar_h, 2, 2)
    elif state != "running":  # idle / not_ready：统一显示"运行前"播放三角
        triangle = QPolygonF([
            QPointF(cx - 10, cy - 14),
            QPointF(cx - 10, cy + 14),
            QPointF(cx + 14, cy),
        ])
        painter.drawPolygon(triangle)
    painter.end()
    return QIcon(pixmap)


class TrayOpsMixin:
    """托盘图标混入类"""

    # 类级兜底：系统不支持托盘（如无 X 环境）时 _build_tray_icon 提前返回，
    # 其他方法仍可能被调用到，靠这个默认值避免 AttributeError。
    _tray_icon = None

    def _build_tray_icon(self):
        self._tray_hint_shown = False
        if not QSystemTrayIcon.isSystemTrayAvailable():
            self._tray_icon = None
            return
        self._tray_icon = QSystemTrayIcon(_make_tray_icon("idle"), self)
        menu = QMenu()
        self._tray_action_start = menu.addAction(tr("开始"), self._on_start)
        self._tray_action_pause = menu.addAction(tr("暂停"), self._on_pause_resume)
        self._tray_action_stop = menu.addAction(tr("结束"), self._on_stop)
        menu.addSeparator()
        menu.addAction(tr("打开主界面"), self._restore_from_tray)
        self._tray_icon.setContextMenu(menu)
        self._tray_icon.activated.connect(self._on_tray_activated)
        self.automation_state_changed.connect(self._refresh_tray_icon)
        self._refresh_tray_icon(getattr(self, "_run_state", "idle"))

    def _minimize_to_tray(self):
        if self._tray_icon is None:
            return
        self._tray_icon.show()
        self.hide()
        if not self._tray_hint_shown:
            self._tray_hint_shown = True
            self._tray_icon.showMessage(
                self.windowTitle(),
                tr("已最小化到状态栏，双击图标或右键「打开主界面」可恢复。"),
                QSystemTrayIcon.MessageIcon.Information,
                3000,
            )

    def _restore_from_tray(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()
        if self._tray_icon is not None:
            self._tray_icon.hide()

    def _on_tray_activated(self, reason):
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            self._restore_from_tray()

    def _refresh_tray_icon(self, state: str):
        """随 automation_state_changed 广播刷新托盘图标/菜单可用性。"""
        if self._tray_icon is None:
            return
        icon_state = state if state in ("running", "paused") else "idle"
        self._tray_icon.setIcon(_make_tray_icon(icon_state))
        from .run_control import STATE_PLAN_UNSUPPORTED
        status_text = {
            "running": tr("运行中"),
            "paused": tr("已暂停"),
            "not_ready": tr("未就绪"),
            STATE_PLAN_UNSUPPORTED: tr("方案不支持"),
        }.get(state, tr("空闲"))
        self._tray_icon.setToolTip(f"{self.windowTitle()} - {status_text}")
        running_or_paused = state in ("running", "paused")
        # 方案不支持时托盘的「开始」也得灰掉，否则等于给灰按钮开了后门。
        self._tray_action_start.setEnabled(
            not running_or_paused and state != STATE_PLAN_UNSUPPORTED)
        self._tray_action_pause.setEnabled(running_or_paused)
        self._tray_action_pause.setText(tr("恢复") if state == "paused" else tr("暂停"))
        self._tray_action_stop.setEnabled(running_or_paused)
