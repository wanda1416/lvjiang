"""用户信息 Tab 与用户详情页

UserInfoTab: 展示当前用户的详细信息（按模型类型分区）
_DetailPage: 用户详情页 - 按模型类型分区展示单个用户的完整信息
"""

from __future__ import annotations

from loguru import logger
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ...core.profile.models import (
    MODEL_LABELS,
    MODEL_NOTE,
    MODEL_QUOTA,
    MODEL_REGEN,
    MODEL_STOCK,
    KeyDef,
    RegenKeyDef,
)
from ...core.profile.regen import compute_regen_entry
from ...core.profile.repository import db_read_all
from ...i18n import tr
from ..avatar_editor_dialog import AvatarEditorDialog
from ..avatar_widget import AvatarWidget
from ..user_toolbar import USER_ACTION_BTN_STYLE, add_user_toolbar_buttons
from .user_info_styles import USER_INFO_GROUP_STYLE
from .user_notes import UserNotesPanel

# ─── 用户信息 Tab ────────────────────────────────────────────


class UserInfoTab(QWidget):
    """用户信息 Tab - 展示当前用户的详细信息（按模型类型分区）"""

    def __init__(self, host, parent=None):
        super().__init__(parent)
        self._host = host
        self._detail_page: _DetailPage | None = None
        self._content_point_size = 0
        self._pending_detail_refresh = False
        self._detail_refresh_timer = self._make_debounce_timer(
            self, self._refresh_pending_detail
        )
        self._setup_ui()
        size = getattr(
            getattr(getattr(host, "_user_config", None), "font_sizes", None),
            "user_info",
            0,
        )
        if isinstance(size, int) and size > 0:
            self.apply_content_font_size(size)
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
            from ...core.profile.engine import get_or_create_engine
            engine = get_or_create_engine(self._host.user_manager)
            engine.data_updated.connect(self._on_profile_data_updated)
            engine.alert_triggered.connect(self._on_alert_triggered)
        except Exception as e:
            logger.debug(f"UserInfoTab 连接 ProfileEngine 失败: {e}")

    def _on_profile_data_updated(self, user_name: str) -> None:
        if user_name == self._host.active_user_name():
            self._pending_detail_refresh = True
            if not self._detail_refresh_timer.isActive():
                self._detail_refresh_timer.start()

    def _on_alert_triggered(self, key: str, label: str, message: str) -> None:
        """处理 ProfileEngine 的告警信号，刷新公共告警面板

        持久化已由引擎侧 add_alert 完成，这里仅通知面板刷新显示。
        """
        if getattr(self._host, 'alert_panel', None) is not None:
            self._host.alert_panel.refresh()

    def _refresh_pending_detail(self) -> None:
        if self._pending_detail_refresh and self._detail_page is not None:
            self._detail_page.refresh()
        self._pending_detail_refresh = False

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        # 刷新按钮
        btn_row = QHBoxLayout()
        add_user_toolbar_buttons(
            btn_row,
            self._host,
            self._refresh_current_user,
            refresh_tooltip=tr("重新读取用户数据"),
        )
        btn_row.addStretch()
        self._add_note_button = QPushButton(tr("＋ 添加便利贴"))
        self._add_note_button.setStyleSheet(USER_ACTION_BTN_STYLE)
        self._add_note_button.clicked.connect(self._add_current_user_note)
        self._add_note_button.setEnabled(False)
        btn_row.addWidget(self._add_note_button)
        layout.addLayout(btn_row)

        # 详情容器
        self._content_widget = QWidget()
        self._detail_container = QVBoxLayout(self._content_widget)
        self._detail_container.setContentsMargins(0, 0, 0, 0)
        self._detail_container.setSpacing(0)
        layout.addWidget(self._content_widget, stretch=1)

    def apply_content_font_size(self, point_size: int) -> None:
        """只调整刷新工具栏下方的用户信息内容。"""
        if not 8 <= int(point_size) <= 24:
            return
        font = QFont(self._content_widget.font())
        font.setPointSize(int(point_size))
        self._content_point_size = int(point_size)
        self._content_widget.setFont(font)
        for child in self._content_widget.findChildren(QWidget):
            child.setFont(font)
        if self._detail_page is not None:
            self._detail_page.set_content_font_size(int(point_size))

    def _apply_font_to_new_content(self, widget: QWidget) -> None:
        if self._content_point_size <= 0:
            return
        font = QFont(self._content_widget.font())
        widget.setFont(font)
        for child in widget.findChildren(QWidget):
            child.setFont(font)
        if isinstance(widget, _DetailPage):
            widget.set_content_font_size(self._content_point_size)

    def _refresh_current_user(self):
        """根据当前用户重建详情页"""
        self._detail_page = None
        self._add_note_button.setEnabled(False)
        while self._detail_container.count() > 0:
            item = self._detail_container.takeAt(0)
            w = item.widget()
            if w:
                w.setParent(None)

        user_name = self._host.active_user_name()
        if not user_name:
            placeholder = QLabel(tr("请先选择用户"))
            placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
            placeholder.setStyleSheet(
                "color: palette(mid); padding: 40px;")
            self._detail_container.addWidget(placeholder)
            self._apply_font_to_new_content(placeholder)
            return

        self._detail_page = _DetailPage(
            user_name,
            self._host.user_manager,
            screenshot_callback=getattr(self._host, "_refresh_capture", None),
        )
        self._detail_container.addWidget(self._detail_page)
        self._apply_font_to_new_content(self._detail_page)
        self._add_note_button.setEnabled(True)

    def _add_current_user_note(self) -> None:
        if self._detail_page is not None:
            self._detail_page.add_note()


# ─── 用户详情页 ──────────────────────────────────────────────


class _DetailPage(QWidget):
    """用户详情页 - 按模型类型分区展示单个用户的完整信息"""

    def __init__(self, user_name: str, user_manager, parent=None, *,
                 screenshot_callback=None):
        super().__init__(parent)
        self._user_name = user_name
        self._user_manager = user_manager
        self._screenshot_callback = screenshot_callback
        self._pending_avatar_filename = ""
        self._value_labels: dict[str, QLabel] = {}
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        # UserInfoTab 已提供统一的页面外边距；详情页本身不再重复内缩。
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        self._container = QWidget()
        self._form_layout = QVBoxLayout(self._container)
        self._form_layout.setContentsMargins(8, 8, 8, 8)
        self._form_layout.setSpacing(8)
        scroll.setWidget(self._container)
        layout.addWidget(scroll)

        self._profile_row = QHBoxLayout()
        self._profile_row.setSpacing(12)

        self._avatar_box = QGroupBox(tr("用户头像"))
        self._avatar_box.setStyleSheet(USER_INFO_GROUP_STYLE)
        avatar_layout = QVBoxLayout(self._avatar_box)
        avatar_layout.setContentsMargins(16, 20, 16, 16)
        self._avatar = AvatarWidget(self._user_name, editable=True, size=176)
        self._avatar.edit_requested.connect(self._open_avatar_editor)
        self._avatar_load_timer = QTimer(self)
        self._avatar_load_timer.setSingleShot(True)
        self._avatar_load_timer.setInterval(25)
        self._avatar_load_timer.timeout.connect(self._load_pending_avatar)
        avatar_layout.addWidget(
            self._avatar,
            alignment=Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
        )
        avatar_layout.addStretch()
        self._avatar_box.setMaximumWidth(260)
        self._profile_row.addWidget(
            self._avatar_box, stretch=1, alignment=Qt.AlignmentFlag.AlignTop)

        self._notes_panel = UserNotesPanel(self._user_name)
        self._profile_row.addWidget(
            self._notes_panel,
            stretch=3,
            alignment=Qt.AlignmentFlag.AlignTop,
        )
        self._form_layout.addLayout(self._profile_row)

        self._build_form()
        self.refresh()

    def _build_form(self):
        """按模型类型并列展示"""
        from ...core.profile import get_profile_config

        config = get_profile_config()

        row = QHBoxLayout()
        row.setSpacing(12)

        for model_type in (MODEL_QUOTA, MODEL_STOCK, MODEL_REGEN, MODEL_NOTE):
            keys = config.get_keys_by_model(model_type)
            model_label = MODEL_LABELS.get(model_type, model_type)
            box = QGroupBox(model_label)
            box.setStyleSheet(USER_INFO_GROUP_STYLE)

            form = QFormLayout(box)
            form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

            for kd in keys:
                label = QLabel("")
                label.setStyleSheet("color: palette(text);")
                form.addRow(f"{kd.label}:", label)
                self._value_labels[kd.key] = label

            row.addWidget(box, stretch=1)

        self._form_layout.addLayout(row)
        self._form_layout.addStretch()

    def refresh(self):
        """从 profile DB 加载数据并刷新"""
        self._notes_panel.refresh()
        user = self._user_manager.get_user(self._user_name)
        self._schedule_avatar_load(user.avatar if user is not None else "")
        try:
            data = db_read_all(self._user_name)
        except Exception as e:
            logger.warning(f"加载用户 {self._user_name} profile 失败: {e}")
            return

        from ...core.profile import get_profile_config
        config = get_profile_config()

        for key, label in self._value_labels.items():
            model_type = config.get_model_type(key) or ""
            kd = config.get_key(key)
            if not kd:
                label.setText("")
                continue

            text = _format_detail_value(kd, model_type, data)
            label.setText(text)

    def _schedule_avatar_load(self, filename: str) -> None:
        """Defer avatar disk I/O until the rest of the detail panel can render."""
        self._pending_avatar_filename = filename
        self._avatar_load_timer.start()

    def _load_pending_avatar(self) -> None:
        self._avatar.set_avatar(
            self._user_name, self._pending_avatar_filename)

    def add_note(self) -> None:
        """从用户信息页工具栏新增便利贴。"""
        self._notes_panel.add_note()

    def set_content_font_size(self, point_size: int) -> None:
        """下发系统设置中的用户信息正文字号给动态内容组件。"""
        self._notes_panel.set_content_font_size(point_size)

    def _open_avatar_editor(self) -> None:
        """从主页面直接编辑当前头像，不经过用户管理窗口。"""
        dialog = AvatarEditorDialog(
            self._user_name,
            self._user_manager,
            self,
            screenshot_callback=self._screenshot_callback,
        )
        dialog.avatar_changed.connect(self._on_avatar_changed)
        dialog.exec()

    def _on_avatar_changed(self, username: str, filename: str) -> None:
        if username == self._user_name:
            self._avatar_load_timer.stop()
            self._pending_avatar_filename = filename
            self._avatar.set_avatar(username, filename)


def _format_detail_value(kd: KeyDef, model_type: str, data: dict) -> str:
    """格式化详情页的值显示（纯数值，取整）"""
    entry = data.get(model_type, {}).get(kd.key, {})
    if not entry:
        return ""

    if model_type == MODEL_NOTE:
        text = entry.get("value_text", "")
        if text:
            return str(text)
        # 兼容旧数据：尚未迁入 value_text 时仍可展示原数值。
        value = entry.get("value")
        return str(int(value)) if value else ""

    value = entry.get("value")
    if value is None:
        return ""

    if model_type == MODEL_QUOTA:
        if isinstance(value, bool):
            return tr("已完成") if value else tr("未完成")
        return str(int(value))

    if model_type == MODEL_REGEN:
        if isinstance(kd, RegenKeyDef):
            return str(int(compute_regen_entry(entry, kd).value))
        return str(int(value))

    if model_type == MODEL_STOCK:
        return str(int(value))

    return str(int(value))
