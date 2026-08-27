"""用户管理对话框 - 左右分列式布局"""

from loguru import logger
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from lvjiang.core.user_config import UserConfigManager

from ..i18n import tr

# ─── 样式常量 ────────────────────────────────────────────

_ACCENT = "#0078d4"

_STYLE_LIST = """
QListWidget#userList {
    border: 1px solid palette(midlight);
    border-radius: 6px;
    background: palette(base);
    outline: none;
    padding: 4px;
}
QListWidget#userList::item {
    padding: 5px 10px;
    border-radius: 4px;
    margin: 1px 0;
    color: palette(text);
}
QListWidget#userList::item:hover {
    background: palette(alternate-base);
}
QListWidget#userList::item:selected {
    background: #0078d4;
    color: white;
}
"""

_STYLE_BTN_PRIMARY = f"""
QPushButton {{
    background: {_ACCENT};
    color: white;
    border: none;
    border-radius: 4px;
    padding: 6px 14px;
}}
QPushButton:hover {{ background: #106ebe; }}
QPushButton:pressed {{ background: #005a9e; }}
QPushButton:disabled {{ background: palette(mid); }}
"""

_STYLE_BTN_DANGER = """
QPushButton {
    background: transparent;
    color: #d13438;
    border: 1px solid #d13438;
    border-radius: 4px;
    padding: 6px 14px;
}
QPushButton:hover { background: palette(alternate-base); }
QPushButton:pressed { background: palette(midlight); }
QPushButton:disabled { color: palette(mid); border-color: palette(mid); }
"""

_STYLE_BTN_GHOST = """
QPushButton {
    background: transparent;
    color: palette(text);
    border: 1px solid palette(mid);
    border-radius: 4px;
    padding: 6px 14px;
}
QPushButton:hover { background: palette(alternate-base); }
QPushButton:pressed { background: palette(midlight); }
"""

_STYLE_CARD = """
QFrame#detailCard {
    background: palette(base);
    border: 1px solid palette(midlight);
    border-radius: 8px;
}
"""

_STYLE_BADGE_ACTIVE = f"""
QLabel {{
    background: {_ACCENT};
    color: white;
    border-radius: 9px;
    padding: 2px 10px;
    font-size: 11px;
}}
"""


def _format_iso_time(iso_str: str) -> str:
    """ISO 时间戳转易读格式"""
    if not iso_str:
        return "-"
    return iso_str[:19].replace("T", " ")


class UserManagerDialog(QDialog):
    """用户管理对话框：左侧用户列表 + 右侧用户详情"""

    def __init__(self, user_manager: UserConfigManager, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("用户管理"))
        self.setMinimumSize(720, 480)
        self.resize(760, 520)

        self._user_manager = user_manager
        self._setup_ui()
        self._refresh_user_list()

    # ─── UI 构建 ─────────────────────────────────────────

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 12)
        root.setSpacing(12)

        body = QHBoxLayout()
        body.setSpacing(16)
        root.addLayout(body, stretch=1)

        body.addWidget(self._build_left_panel())
        body.addWidget(self._build_right_panel(), stretch=1)

        # ─── 底部按钮行 ───
        bottom_row = QHBoxLayout()
        bottom_row.addStretch()
        btn_close = QPushButton(tr("关闭"))
        btn_close.setStyleSheet(_STYLE_BTN_GHOST)
        btn_close.clicked.connect(self.accept)
        bottom_row.addWidget(btn_close)
        root.addLayout(bottom_row)

    def _build_left_panel(self) -> QWidget:
        """左侧：工具栏 + 用户列表"""
        panel = QWidget()
        panel.setFixedWidth(190)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        # 顶部工具栏
        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)

        self._btn_new = QPushButton(tr("＋ 新建用户"))
        self._btn_new.setStyleSheet(_STYLE_BTN_PRIMARY)
        self._btn_new.clicked.connect(self._on_create_user)
        toolbar.addWidget(self._btn_new)

        self._btn_delete = QPushButton(tr("删除"))
        self._btn_delete.setStyleSheet(_STYLE_BTN_DANGER)
        self._btn_delete.setEnabled(False)
        self._btn_delete.clicked.connect(self._on_delete_user)
        toolbar.addWidget(self._btn_delete)

        toolbar.addStretch()
        layout.addLayout(toolbar)

        # 用户列表（支持拖拽排序）
        self._user_list = QListWidget()
        self._user_list.setObjectName("userList")
        self._user_list.setStyleSheet(_STYLE_LIST)
        self._user_list.setDragDropMode(
            QAbstractItemView.DragDropMode.InternalMove,
        )
        self._user_list.setDefaultDropAction(Qt.DropAction.MoveAction)
        self._user_list.currentRowChanged.connect(self._on_user_selected)
        model = self._user_list.model()
        assert model is not None
        model.rowsMoved.connect(self._on_rows_moved)
        layout.addWidget(self._user_list, stretch=1)

        return panel

    def _build_right_panel(self) -> QWidget:
        """右侧：用户详情面板（可滚动，为未来扩展预留空间）"""
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 4, 0)
        layout.setSpacing(12)

        # ─── 头部：用户名 + 当前标记 + 操作 ───
        header_card = QFrame()
        header_card.setObjectName("detailCard")
        header_card.setStyleSheet(_STYLE_CARD)
        header_layout = QHBoxLayout(header_card)
        header_layout.setContentsMargins(20, 16, 20, 16)

        self._lbl_title = QLabel("-")
        title_font = QFont()
        title_font.setPointSize(15)
        title_font.setBold(True)
        self._lbl_title.setFont(title_font)
        header_layout.addWidget(self._lbl_title)

        self._badge_active = QLabel(tr("当前用户"))
        self._badge_active.setStyleSheet(_STYLE_BADGE_ACTIVE)
        self._badge_active.setVisible(False)
        header_layout.addWidget(self._badge_active)

        header_layout.addStretch()

        layout.addWidget(header_card)

        # ─── 基本信息卡片 ───
        info_card = QFrame()
        info_card.setObjectName("detailCard")
        info_card.setStyleSheet(_STYLE_CARD)
        info_layout = QVBoxLayout(info_card)
        info_layout.setContentsMargins(20, 16, 20, 16)
        info_layout.setSpacing(10)

        info_layout.addWidget(self._section_title(tr("基本信息")))

        form = QFormLayout()
        form.setHorizontalSpacing(24)
        form.setVerticalSpacing(10)

        self._lbl_name = QLabel("-")
        form.addRow(self._field_label(tr("用户名")), self._lbl_name)

        self._lbl_created = QLabel("-")
        form.addRow(self._field_label(tr("创建时间")), self._lbl_created)

        info_layout.addLayout(form)
        layout.addWidget(info_card)

        # ─── 数据统计卡片（预留） ───
        stats_card = QFrame()
        stats_card.setObjectName("detailCard")
        stats_card.setStyleSheet(_STYLE_CARD)
        stats_layout = QVBoxLayout(stats_card)
        stats_layout.setContentsMargins(20, 16, 20, 16)
        stats_layout.setSpacing(10)

        stats_layout.addWidget(self._section_title(tr("数据统计")))

        # 占位文案保持中性：这是通用用户管理对话框，具体展示什么数据由插件决定
        self._lbl_stats = QLabel(tr("数据展示功能开发中..."))
        self._lbl_stats.setStyleSheet("color: palette(mid); font-style: italic;")
        stats_layout.addWidget(self._lbl_stats)

        layout.addWidget(stats_card)

        layout.addStretch()

        scroll.setWidget(container)
        scroll.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding,
        )
        return scroll

    @staticmethod
    def _section_title(text: str) -> QLabel:
        """卡片内的小节标题"""
        label = QLabel(text)
        label.setStyleSheet(
            f"color: {_ACCENT}; font-weight: bold; font-size: 13px;"
        )
        return label

    @staticmethod
    def _field_label(text: str) -> QLabel:
        """表单字段名标签"""
        label = QLabel(text)
        label.setStyleSheet("color: palette(mid);")
        return label

    # ─── 列表刷新与选中 ──────────────────────────────────

    def _refresh_user_list(self, select_name: str | None = None):
        """刷新用户列表，可指定刷新后选中的用户"""
        if select_name is None:
            current = self._user_list.currentItem()
            if current is not None:
                select_name = current.data(Qt.ItemDataRole.UserRole)

        self._user_list.blockSignals(True)
        self._user_list.clear()
        active_user = self._user_manager.get_active_user_name()

        select_row = 0
        for row, name in enumerate(self._user_manager.list_users()):
            item = QListWidgetItem(name)
            item.setData(Qt.ItemDataRole.UserRole, name)
            if name == active_user:
                item.setText(f"{name}  ●")
                font = item.font()
                font.setBold(True)
                item.setFont(font)
                item.setToolTip(tr("当前用户"))
            if name == select_name:
                select_row = row
            self._user_list.addItem(item)

        self._user_list.blockSignals(False)

        if self._user_list.count() > 0:
            self._user_list.setCurrentRow(select_row)
            self._on_user_selected(select_row)
        else:
            self._on_user_selected(-1)

    def _on_user_selected(self, row: int):
        """用户列表选中项变化"""
        if row < 0:
            self._btn_delete.setEnabled(False)
            self._clear_detail()
            return

        item = self._user_list.item(row)
        assert item is not None
        name = item.data(Qt.ItemDataRole.UserRole)
        user = self._user_manager.get_user(name)
        if user is None:
            return

        # 激活用户不可删除
        is_active = (name == self._user_manager.get_active_user_name())
        self._btn_delete.setEnabled(not is_active)

        # 显示详情
        self._lbl_title.setText(user.name)
        self._badge_active.setVisible(is_active)
        self._lbl_name.setText(user.name)
        self._lbl_created.setText(_format_iso_time(user.created_at))

    def _clear_detail(self):
        """清空详情显示"""
        self._lbl_title.setText("-")
        self._badge_active.setVisible(False)
        self._lbl_name.setText("-")
        self._lbl_created.setText("-")

    def _current_user_name(self) -> str | None:
        """当前选中的用户名"""
        item = self._user_list.currentItem()
        if item is None:
            return None
        return item.data(Qt.ItemDataRole.UserRole)

    # ─── 操作 ────────────────────────────────────────────

    def _on_rows_moved(self, *_args):
        """拖拽排序后持久化新顺序"""
        names = [
            self._user_list.item(i).data(Qt.ItemDataRole.UserRole)
            for i in range(self._user_list.count())
        ]
        self._user_manager.reorder_users(names)

    def _on_create_user(self):
        """新建用户"""
        name, ok = QInputDialog.getText(
            self, tr("新建用户"), tr("请输入用户名："),
        )
        if not ok or not name:
            return
        name = name.strip()
        if not name:
            return

        # 分开报错：名字非法与重名是两回事，合成一句话用户不知道该改什么。
        # 真正的校验在 UserManager.create_user 里（唯一入口），这里只为提示。
        from ..core.user_config import is_valid_username
        if not is_valid_username(name):
            QMessageBox.warning(
                self, tr("失败"),
                tr("用户名只能用中文、字母、数字、下划线和连字符，最多 32 个字符。"))
            return

        if self._user_manager.create_user(name):
            self._refresh_user_list(select_name=name)
            logger.info(f"用户已创建: {name}")
        else:
            QMessageBox.warning(self, tr("失败"), tr("用户名已存在"))

    def _on_delete_user(self):
        """删除用户"""
        name = self._current_user_name()
        if name is None:
            return

        reply = QMessageBox.question(
            self, tr("确认删除"),
            tr("确定要删除用户「{name}」吗？\n该用户的所有数据将被清除，此操作不可恢复。").format(name=name),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        if self._user_manager.delete_user(name):
            self._refresh_user_list(select_name="")
            logger.info(f"用户已删除: {name}")
        else:
            QMessageBox.warning(self, tr("失败"), tr("无法删除该用户"))
