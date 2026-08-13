"""其他信息 Tab 与角色详情页

ProfileTab: 展示当前用户的详细信息（按模型类型分区）
_DetailPage: 角色详情页 - 按模型类型分区展示单个角色的完整信息
"""

from __future__ import annotations

from loguru import logger
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ...config.profile_models import (
    MODEL_LABELS,
    MODEL_QUOTA,
    MODEL_REGEN,
    MODEL_STOCK,
    KeyDef,
    RegenKeyDef,
)
from ...profile.profile_db import db_read_all
from ...profile.profile_engine import compute_regen_entry

# 统一的刷新按钮样式（overview.py 与 tab.py 共用）
REFRESH_BTN_STYLE = (
    "QPushButton { background-color: #607D8B; color: white; font-size: 12px; "
    "padding: 4px; border-radius: 3px; }"
    "QPushButton:hover { background-color: #78909C; }"
)


# ─── 其他信息 Tab ────────────────────────────────────────────


class ProfileTab(QWidget):
    """其他信息 Tab - 展示当前用户的详细信息（按模型类型分区）"""

    def __init__(self, host, parent=None):
        super().__init__(parent)
        self._host = host
        self._detail_page: _DetailPage | None = None
        self._pending_detail_refresh = False
        self._detail_refresh_timer = self._make_debounce_timer(
            self, self._refresh_pending_detail
        )
        self._setup_ui()
        self._refresh_current_user()
        host.user_changed.connect(lambda _name: self._refresh_current_user())
        self._connect_profile_engine()

    @staticmethod
    def _make_debounce_timer(parent, callback, interval_ms: int = 500):
        """创建一个单次触发的防抖定时器"""
        from PyQt6.QtCore import QTimer
        timer = QTimer(parent)
        timer.setSingleShot(True)
        timer.setInterval(interval_ms)
        timer.timeout.connect(callback)
        return timer

    def _connect_profile_engine(self) -> None:
        """让后台 profile 更新能刷新当前用户详情。"""
        try:
            from ...profile.profile_engine import get_or_create_engine
            engine = get_or_create_engine(self._host.user_manager, self._host.session_manager)
            engine.data_updated.connect(self._on_profile_data_updated)
        except Exception as e:
            logger.debug(f"ProfileTab 连接 ProfileEngine 失败: {e}")

    def _on_profile_data_updated(self, user_name: str) -> None:
        if user_name == self._host.active_user_name():
            self._pending_detail_refresh = True
            if not self._detail_refresh_timer.isActive():
                self._detail_refresh_timer.start()

    def _refresh_pending_detail(self) -> None:
        if self._pending_detail_refresh and self._detail_page is not None:
            self._detail_page.refresh()
        self._pending_detail_refresh = False

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        # 刷新按钮
        btn_row = QHBoxLayout()
        btn_refresh = QPushButton("刷新")
        btn_refresh.setFixedWidth(60)
        btn_refresh.setToolTip("重新读取角色数据")
        btn_refresh.setStyleSheet(REFRESH_BTN_STYLE)
        btn_refresh.clicked.connect(self._refresh_current_user)
        btn_row.addWidget(btn_refresh)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        # 详情容器
        self._detail_container = QVBoxLayout()
        layout.addLayout(self._detail_container, stretch=1)

    def _refresh_current_user(self):
        """根据当前用户重建详情页"""
        while self._detail_container.count() > 0:
            item = self._detail_container.takeAt(0)
            w = item.widget()
            if w:
                w.setParent(None)

        user_name = self._host.active_user_name()
        if not user_name:
            placeholder = QLabel("请先选择用户")
            placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
            placeholder.setStyleSheet("color: #999; font-size: 14px; padding: 40px;")
            self._detail_container.addWidget(placeholder)
            return

        self._detail_page = _DetailPage(user_name)
        self._detail_container.addWidget(self._detail_page)


# ─── 角色详情页 ──────────────────────────────────────────────


class _DetailPage(QWidget):
    """角色详情页 - 按模型类型分区展示单个角色的完整信息"""

    def __init__(self, user_name: str, parent=None):
        super().__init__(parent)
        self._user_name = user_name
        self._value_labels: dict[str, QLabel] = {}
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self._container = QWidget()
        self._form_layout = QVBoxLayout(self._container)
        self._form_layout.setSpacing(8)
        scroll.setWidget(self._container)
        layout.addWidget(scroll)

        self._build_form()
        self.refresh()

    def _build_form(self):
        """按模型类型并列三列展示"""
        from ...config import get_profile_config

        config = get_profile_config()

        row = QHBoxLayout()
        row.setSpacing(12)

        _GROUP_STYLE = """
            QGroupBox {
                font-weight: bold;
                border: 1px solid #cccccc;
                border-radius: 4px;
                margin-top: 8px;
                padding-top: 12px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 4px;
            }
        """

        for model_type in (MODEL_QUOTA, MODEL_STOCK, MODEL_REGEN):
            keys = config.get_keys_by_model(model_type)
            model_label = MODEL_LABELS.get(model_type, model_type)
            box = QGroupBox(model_label)
            box.setStyleSheet(_GROUP_STYLE)

            form = QFormLayout(box)
            form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

            for kd in keys:
                label = QLabel("")
                label.setStyleSheet("color: #333333;")
                form.addRow(f"{kd.label}:", label)
                self._value_labels[kd.key] = label

            row.addWidget(box, stretch=1)

        self._form_layout.addLayout(row)
        self._form_layout.addStretch()

    def refresh(self):
        """从 profile DB 加载数据并刷新"""
        try:
            data = db_read_all(self._user_name)
        except Exception as e:
            logger.warning(f"加载用户 {self._user_name} profile 失败: {e}")
            return

        from ...config import get_profile_config
        config = get_profile_config()

        for key, label in self._value_labels.items():
            model_type = config.get_model_type(key) or ""
            kd = config.get_key(key)
            if not kd:
                label.setText("")
                continue

            text = _format_detail_value(kd, model_type, data)
            label.setText(text)


def _format_detail_value(kd: KeyDef, model_type: str, data: dict) -> str:
    """格式化详情页的值显示（纯数值，取整）"""
    entry = data.get(model_type, {}).get(kd.key, {})
    if not entry:
        return ""

    value = entry.get("value")
    if value is None:
        return ""

    if model_type == MODEL_QUOTA:
        if isinstance(value, bool):
            return "已完成" if value else "未完成"
        return str(int(value))

    if model_type == MODEL_REGEN:
        if isinstance(kd, RegenKeyDef):
            computed, _ = compute_regen_entry(entry, kd)
            return str(int(computed))
        return str(int(value))

    if model_type == MODEL_STOCK:
        return str(int(value))

    return str(int(value))
