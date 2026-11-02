"""用户管理对话框 - 左右分列式布局"""

from collections.abc import Callable

from loguru import logger
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from lvjiang.core.user_config import UserConfigManager

from ..i18n import tr
from .avatar_editor_dialog import AvatarEditorDialog
from .avatar_widget import AvatarWidget

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

def _format_iso_time(iso_str: str) -> str:
    """ISO 时间戳转易读格式"""
    if not iso_str:
        return "-"
    return iso_str[:19].replace("T", " ")


class UserManagerDialog(QDialog):
    """用户管理对话框：左侧用户列表 + 右侧用户详情"""

    avatar_changed = pyqtSignal(str, str)

    def __init__(
        self,
        user_manager: UserConfigManager,
        parent=None,
        *,
        screenshot_callback: Callable[[], object] | None = None,
    ):
        super().__init__(parent)
        self.setWindowTitle(tr("用户管理"))
        self.setMinimumSize(864, 576)
        self.resize(912, 624)

        self._user_manager = user_manager
        self._screenshot_callback = screenshot_callback
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

        # ─── 基本信息卡片 ───
        info_card = QFrame()
        info_card.setObjectName("detailCard")
        info_card.setStyleSheet(_STYLE_CARD)
        info_layout = QHBoxLayout(info_card)
        info_layout.setContentsMargins(20, 16, 20, 16)
        info_layout.setSpacing(24)

        text_panel = QWidget()
        text_layout = QVBoxLayout(text_panel)
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.setSpacing(10)
        text_layout.addWidget(self._section_title(tr("基本信息")))

        form = QFormLayout()
        form.setHorizontalSpacing(24)
        form.setVerticalSpacing(10)

        self._lbl_name = QLabel("-")
        form.addRow(self._field_label(tr("用户名")), self._lbl_name)

        self._lbl_created = QLabel("-")
        form.addRow(self._field_label(tr("创建时间")), self._lbl_created)

        text_layout.addLayout(form)
        text_layout.addStretch()
        info_layout.addWidget(text_panel, stretch=1)

        avatar_panel = QVBoxLayout()
        avatar_title = self._section_title(tr("用户头像"))
        avatar_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        avatar_panel.addWidget(avatar_title)
        self._avatar = AvatarWidget(editable=True, size=156)
        self._avatar.edit_requested.connect(self._open_avatar_editor)
        avatar_panel.addWidget(self._avatar, alignment=Qt.AlignmentFlag.AlignCenter)
        avatar_hint = QLabel(tr("悬浮点击编辑，或双击头像"))
        avatar_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        avatar_hint.setStyleSheet("color: palette(mid); font-size: 11px;")
        avatar_panel.addWidget(avatar_hint)
        info_layout.addLayout(avatar_panel)
        layout.addWidget(info_card)

        attr_card = QFrame()
        attr_card.setObjectName("detailCard")
        attr_card.setStyleSheet(_STYLE_CARD)
        attr_layout = QVBoxLayout(attr_card)
        attr_layout.setContentsMargins(20, 16, 20, 16)
        attr_layout.setSpacing(10)
        attr_layout.addWidget(self._section_title(tr("用户属性")))
        self._attribute_table = QTableWidget(0, 2)
        self._attribute_table.setHorizontalHeaderLabels(["Key", "Value"])
        self._attribute_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self._attribute_table.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked
            | QAbstractItemView.EditTrigger.EditKeyPressed
        )
        attr_header = self._attribute_table.horizontalHeader()
        assert attr_header is not None
        attr_header.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        attr_header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._attribute_table.setColumnWidth(0, 180)
        attr_vertical_header = self._attribute_table.verticalHeader()
        assert attr_vertical_header is not None
        attr_vertical_header.setVisible(False)
        attr_layout.addWidget(self._attribute_table)
        attr_actions = QHBoxLayout()
        self._btn_add_attribute = QPushButton(tr("新增属性"))
        self._btn_delete_attribute = QPushButton(tr("删除属性"))
        self._btn_add_attribute.clicked.connect(self._add_attribute)
        self._btn_delete_attribute.clicked.connect(self._delete_attributes)
        self._btn_add_attribute.setStyleSheet(_STYLE_BTN_GHOST)
        self._btn_delete_attribute.setStyleSheet(_STYLE_BTN_DANGER)
        attr_actions.addWidget(self._btn_add_attribute)
        attr_actions.addWidget(self._btn_delete_attribute)
        attr_actions.addStretch()
        self._btn_save_attributes = QPushButton(tr("保存属性"))
        self._btn_save_attributes.setStyleSheet(_STYLE_BTN_PRIMARY)
        self._btn_save_attributes.clicked.connect(self._save_attributes)
        attr_actions.addWidget(self._btn_save_attributes)
        attr_layout.addLayout(attr_actions)
        layout.addWidget(attr_card)
        self._attribute_baseline: dict[str, str] = {}

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
        self._lbl_name.setText(user.name)
        self._lbl_created.setText(_format_iso_time(user.created_at))
        self._avatar.set_avatar(user.name, user.avatar)
        self._load_attributes(user.attributes)
        self._attribute_table.setEnabled(True)
        self._btn_add_attribute.setEnabled(True)
        self._btn_delete_attribute.setEnabled(True)
        self._btn_save_attributes.setEnabled(True)

    def _clear_detail(self):
        """清空详情显示"""
        self._lbl_name.setText("-")
        self._lbl_created.setText("-")
        self._avatar.set_avatar("", "")
        self._attribute_table.setRowCount(0)
        self._attribute_table.setEnabled(False)
        self._attribute_baseline = {}
        self._btn_add_attribute.setEnabled(False)
        self._btn_delete_attribute.setEnabled(False)
        self._btn_save_attributes.setEnabled(False)

    def _current_user_name(self) -> str | None:
        """当前选中的用户名"""
        item = self._user_list.currentItem()
        if item is None:
            return None
        return item.data(Qt.ItemDataRole.UserRole)

    # ─── 操作 ────────────────────────────────────────────

    def _load_attributes(self, attributes: dict[str, str]) -> None:
        self._attribute_table.setRowCount(0)
        for key, value in attributes.items():
            row = self._attribute_table.rowCount()
            self._attribute_table.insertRow(row)
            self._attribute_table.setItem(row, 0, QTableWidgetItem(key))
            self._attribute_table.setItem(row, 1, QTableWidgetItem(value))
        self._attribute_baseline = dict(attributes)

    def _add_attribute(self) -> None:
        row = self._attribute_table.rowCount()
        self._attribute_table.insertRow(row)
        key_item = QTableWidgetItem("")
        self._attribute_table.setItem(row, 0, key_item)
        self._attribute_table.setItem(row, 1, QTableWidgetItem(""))
        self._attribute_table.setCurrentCell(row, 0)
        self._attribute_table.editItem(key_item)

    def _delete_attributes(self) -> None:
        rows = sorted({index.row() for index in self._attribute_table.selectedIndexes()},
                      reverse=True)
        for row in rows:
            self._attribute_table.removeRow(row)

    def _read_attributes(self) -> dict[str, str] | None:
        attributes: dict[str, str] = {}
        for row in range(self._attribute_table.rowCount()):
            key_item = self._attribute_table.item(row, 0)
            value_item = self._attribute_table.item(row, 1)
            key = key_item.text().strip() if key_item else ""
            value = value_item.text() if value_item else ""
            if not key and not value:
                continue
            if not key:
                QMessageBox.warning(self, tr("保存失败"), tr("属性 Key 不能为空"))
                return None
            if key in attributes:
                QMessageBox.warning(
                    self, tr("保存失败"), tr("属性 Key 不能重复：{key}").format(key=key)
                )
                return None
            attributes[key] = value
        return attributes

    def _save_attributes(self) -> None:
        name = self._current_user_name()
        if not name:
            return
        values = self._read_attributes()
        if values is None:
            return
        from ..core.user_config import UserMetadataConflictError
        try:
            saved = self._user_manager.replace_user_attributes(
                name, values, self._attribute_baseline
            )
        except UserMetadataConflictError as exc:
            QMessageBox.warning(self, tr("保存失败"), str(exc))
            return
        if saved:
            self._attribute_baseline = dict(values)
            logger.info(f"用户属性已更新: {name}")

    def _open_avatar_editor(self):
        name = self._current_user_name()
        if not name:
            return
        dialog = AvatarEditorDialog(
            name,
            self._user_manager,
            self,
            screenshot_callback=self._screenshot_callback,
        )
        dialog.avatar_changed.connect(self._on_avatar_changed)
        dialog.exec()

    def _on_avatar_changed(self, username: str, filename: str) -> None:
        if username == self._current_user_name():
            self._avatar.set_avatar(username, filename)
        self.avatar_changed.emit(username, filename)

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
            tr("确定要删除用户「{name}」吗？\n"
               "用户将从当前列表和批量配置中移除，关联数据文件会保留；"
               "以后添加同名用户即可恢复。").format(name=name),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        deleted = self._user_manager.delete_user(name)
        if deleted:
            self._refresh_user_list(select_name="")
            logger.info(f"用户已删除: {name}")
        else:
            QMessageBox.warning(self, tr("失败"), tr("无法删除该用户"))
