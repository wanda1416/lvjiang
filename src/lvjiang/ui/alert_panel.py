"""公共告警通知面板

在主页面右侧 Tab 下方展示告警信息，支持：
- 栈式展示（最新优先）
- 持久化存储（session.json 的 alert_info 节点）
- 可关闭的告警标签
- 无告警时自动隐藏
"""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from ..core.config.session import dismiss_alert, get_alerts


class AlertPanel(QWidget):
    """告警展示面板

    显示最新的告警（栈顶），带关闭按钮。
    关闭当前告警后自动显示下一条。
    无告警时隐藏整个面板。
    """

    # 告警样式：浅黄背景 + 圆角
    ALERT_STYLE = """
        QWidget {
            background-color: #fff3cd;
            border: 1px solid #ffc107;
            border-radius: 4px;
            padding: 8px;
        }
        QLabel {
            color: #856404;
            font-size: 13px;
        }
        QPushButton {
            background-color: transparent;
            border: none;
            color: #856404;
            font-size: 16px;
            font-weight: bold;
            padding: 0 4px;
        }
        QPushButton:hover {
            background-color: #ffc107;
            border-radius: 3px;
        }
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._current_alert_id: str | None = None
        self._setup_ui()
        self._load_initial_alerts()

    def _setup_ui(self) -> None:
        """初始化 UI 组件"""
        self.setStyleSheet(self.ALERT_STYLE)
        self.setVisible(False)  # 初始隐藏

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(0)

        # 告警内容行
        content_row = QHBoxLayout()
        content_row.setSpacing(8)

        self._message_label = QLabel()
        self._message_label.setWordWrap(True)
        self._message_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        content_row.addWidget(self._message_label, stretch=1)

        self._close_btn = QPushButton("×")
        self._close_btn.setFixedSize(24, 24)
        self._close_btn.setToolTip("关闭此告警")
        self._close_btn.clicked.connect(self._on_close_clicked)
        content_row.addWidget(self._close_btn)

        layout.addLayout(content_row)

    def _load_initial_alerts(self) -> None:
        """从 session.json 加载持久化的告警，显示最新一条"""
        alerts = get_alerts()
        if alerts:
            self._display_alert(alerts[0])

    def _display_alert(self, alert: dict) -> None:
        """显示指定告警"""
        self._current_alert_id = alert.get("id")
        message = alert.get("message", "")
        self._message_label.setText(message)
        self.setVisible(True)

    def push_alert(self, alert_id: str, message: str, timestamp: str) -> None:
        """推送新告警到栈顶并显示

        Args:
            alert_id: 告警唯一标识
            message: 告警文本
            timestamp: 时间戳（ISO 格式）
        """
        from ..core.config.session import add_alert

        if add_alert(alert_id, message, timestamp):
            # 新增成功，立即显示
            self._display_alert({"id": alert_id, "message": message, "timestamp": timestamp})

    def _on_close_clicked(self) -> None:
        """关闭按钮点击事件：移除当前告警，显示下一条"""
        if self._current_alert_id:
            dismiss_alert(self._current_alert_id)
            self._current_alert_id = None

        # 尝试显示下一条告警
        alerts = get_alerts()
        if alerts:
            self._display_alert(alerts[0])
        else:
            self.setVisible(False)

    def dismiss_current(self) -> None:
        """手动关闭当前告警（供外部调用）"""
        self._on_close_clicked()
