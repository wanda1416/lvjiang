"""燕云「档案总览」与「角色状态」Tab

档案总览：宽表展示所有角色的概要信息，交互式列头配置
角色状态：按角色分 Tab 展示详细信息
"""
from __future__ import annotations

from loguru import logger
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from lvjiang.constants import USERS_DIR
from lvjiang.core.config import SessionManager, get_session_store
from lvjiang.core.user_config import UserConfigManager

# 统一的刷新按钮样式
_REFRESH_BTN_STYLE = (
    "QPushButton { background-color: #607D8B; color: white; font-size: 12px; "
    "padding: 4px; border-radius: 3px; }"
    "QPushButton:hover { background-color: #78909C; }"
)

# 总览列配置在 session.json 中的 key
_OVERVIEW_COLUMNS_KEY = "profile_overview_columns"


def _get_overview_columns() -> list[str]:
    """从 session.json 获取总览页列配置"""
    return get_session_store().get_node(_OVERVIEW_COLUMNS_KEY, [])


def _set_overview_columns(columns: list[str]) -> None:
    """保存总览页列配置到 session.json"""
    get_session_store().set_node(_OVERVIEW_COLUMNS_KEY, columns)


# ─── 档案总览 Tab ────────────────────────────────────────────


class ProfileOverviewTab(QWidget):
    """档案总览 Tab - 宽表展示所有角色，交互式列头配置"""

    def __init__(self, host, parent=None):
        super().__init__(parent)
        self._host = host
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        # 标题行
        title_row = QHBoxLayout()
        title = QLabel("档案总览")
        title.setStyleSheet("font-size: 15px; font-weight: bold; color: #333333;")
        title_row.addWidget(title)
        title_row.addStretch()

        btn_refresh = QPushButton("刷新")
        btn_refresh.setFixedWidth(60)
        btn_refresh.setToolTip("重新读取角色数据")
        btn_refresh.setStyleSheet(_REFRESH_BTN_STYLE)
        btn_refresh.clicked.connect(self.refresh)
        title_row.addWidget(btn_refresh)

        layout.addLayout(title_row)

        # 表格
        self._table = QTableWidget()
        self._table.setAlternatingRowColors(True)
        self._table.setShowGrid(True)
        self._table.verticalHeader().setDefaultSectionSize(24)
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows
        )
        self._table.itemChanged.connect(self._on_item_changed)

        # 表头交互：右键菜单 + 双击选择字段
        self._table.horizontalHeader().setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu
        )
        self._table.horizontalHeader().customContextMenuRequested.connect(
            self._on_header_context_menu
        )
        self._table.horizontalHeader().sectionDoubleClicked.connect(
            self._on_header_double_clicked
        )

        layout.addWidget(self._table, stretch=1)

        # 底部按钮行
        btn_row = QHBoxLayout()
        btn_row.addStretch()

        btn_settings = QPushButton("元数据")
        btn_settings.setFixedWidth(70)
        btn_settings.setToolTip("定义字段元数据")
        btn_settings.clicked.connect(self._open_metadata_dialog)
        btn_row.addWidget(btn_settings)

        layout.addLayout(btn_row)

        # 初始加载
        self._loading = False
        self.refresh()

    def refresh(self):
        """刷新表格数据"""
        from ..profile import get_profile_config

        config = get_profile_config()
        column_keys = _get_overview_columns()

        # 过滤掉不存在的字段
        valid_keys = [k for k in column_keys if config.get_field(k)]
        if valid_keys != column_keys:
            _set_overview_columns(valid_keys)
            column_keys = valid_keys

        fields = [config.get_field(k) for k in column_keys]

        # 第一列固定为角色名，后续列为配置的字段
        col_count = 1 + len(fields)
        self._table.setColumnCount(col_count)
        headers = ["角色名"] + [f.label for f in fields]
        self._table.setHorizontalHeaderLabels(headers)

        # 加载所有用户数据
        users_data = self._load_all_users()

        self._loading = True
        self._table.setRowCount(len(users_data))
        for row, (name, data) in enumerate(users_data.items()):
            # 第一列：角色名（不可编辑）
            name_item = QTableWidgetItem(name)
            name_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            name_item.setFlags(name_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            name_item.setForeground(Qt.GlobalColor.darkGray)
            self._table.setItem(row, 0, name_item)

            # 后续列：配置的字段
            for col, field_def in enumerate(fields):
                value = self._get_field_value(field_def, name, data)
                item = QTableWidgetItem(str(value))
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

                # 只读字段不可编辑
                if field_def.readonly:
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                    item.setForeground(Qt.GlobalColor.darkGray)

                self._table.setItem(row, col + 1, item)

        # 调整列宽
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setStretchLastSection(True)
        self._loading = False

    def _on_header_context_menu(self, pos):
        """表头右键菜单"""
        from PyQt6.QtWidgets import QMenu

        logical_index = self._table.horizontalHeader().logicalIndexAt(pos)
        menu = QMenu(self)

        # 新增列（在右侧）
        menu.addAction("右侧新增列", lambda: self._add_column(logical_index))

        # 删除当前列（角色名列不可删除）
        if logical_index > 0:
            menu.addAction("删除当前列", lambda: self._remove_column(logical_index))

        menu.exec(self._table.horizontalHeader().mapToGlobal(pos))

    def _on_header_double_clicked(self, logical_index):
        """表头双击：选择字段（角色名列不可修改）"""
        if logical_index < 1:
            return

        from ..profile import get_profile_config
        config = get_profile_config()
        all_fields = config.get_all_fields()

        if not all_fields:
            QMessageBox.information(self, "提示", "没有可用的元数据字段，请先在元数据定义中添加")
            return

        # 获取当前列的字段 key（用于过滤和预选）
        column_keys = _get_overview_columns()
        current_key = column_keys[logical_index - 1] if logical_index - 1 < len(column_keys) else ""

        # 过滤掉已被其他列使用的字段（保留当前列的字段）
        available_fields = [
            f for f in all_fields
            if f.key not in column_keys or f.key == current_key
        ]

        # 创建下拉选择对话框
        from PyQt6.QtWidgets import QDialog, QVBoxLayout

        dialog = QDialog(self)
        dialog.setWindowTitle("选择字段")
        dialog.setMinimumWidth(200)
        layout = QVBoxLayout(dialog)

        combo = QComboBox()
        combo.addItem("（请选择）", "")
        for f in available_fields:
            combo.addItem(f"{f.label} ({f.key})", f.key)

        # 预选当前字段
        if current_key:
            idx = combo.findData(current_key)
            if idx >= 0:
                combo.setCurrentIndex(idx)

        layout.addWidget(combo)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_ok = QPushButton("确定")
        btn_ok.clicked.connect(dialog.accept)
        btn_row.addWidget(btn_ok)
        btn_cancel = QPushButton("取消")
        btn_cancel.clicked.connect(dialog.reject)
        btn_row.addWidget(btn_cancel)
        layout.addLayout(btn_row)

        if dialog.exec():
            selected_key = combo.currentData()
            if selected_key:
                self._set_column_field(logical_index, selected_key)

    def _add_column(self, after_index: int):
        """在指定列后新增一列"""
        from ..profile import get_profile_config
        config = get_profile_config()
        all_fields = config.get_all_fields()

        if not all_fields:
            QMessageBox.information(self, "提示", "没有可用的元数据字段，请先在元数据定义中添加")
            return

        # 过滤掉已使用的字段
        column_keys = _get_overview_columns()
        available_fields = [f for f in all_fields if f.key not in column_keys]

        if not available_fields:
            QMessageBox.information(self, "提示", "所有元数据字段都已被使用")
            return

        # 弹出选择对话框
        from PyQt6.QtWidgets import QDialog, QVBoxLayout

        dialog = QDialog(self)
        dialog.setWindowTitle("选择字段")
        dialog.setMinimumWidth(200)
        layout = QVBoxLayout(dialog)

        combo = QComboBox()
        for f in available_fields:
            combo.addItem(f"{f.label} ({f.key})", f.key)
        layout.addWidget(combo)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_ok = QPushButton("确定")
        btn_ok.clicked.connect(dialog.accept)
        btn_row.addWidget(btn_ok)
        btn_cancel = QPushButton("取消")
        btn_cancel.clicked.connect(dialog.reject)
        btn_row.addWidget(btn_cancel)
        layout.addLayout(btn_row)

        if dialog.exec():
            selected_key = combo.currentData()
            if selected_key:
                # 检查是否重复
                column_keys = _get_overview_columns()
                if selected_key in column_keys:
                    QMessageBox.warning(self, "重复", f"字段 '{selected_key}' 已在其他列中显示")
                    return
                column_keys.insert(after_index, selected_key)
                _set_overview_columns(column_keys)
                self.refresh()

    def _remove_column(self, logical_index: int):
        """删除指定列"""
        column_keys = _get_overview_columns()
        col_idx = logical_index - 1  # 减去角色名列
        if 0 <= col_idx < len(column_keys):
            del column_keys[col_idx]
            _set_overview_columns(column_keys)
            self.refresh()

    def _set_column_field(self, logical_index: int, field_key: str):
        """设置指定列的字段"""
        column_keys = _get_overview_columns()
        col_idx = logical_index - 1
        if not (0 <= col_idx < len(column_keys)):
            return  # 不应通过 set_column_field 扩展列

        # 检查是否重复
        if field_key in column_keys and column_keys.index(field_key) != col_idx:
            QMessageBox.warning(self, "重复", f"字段 '{field_key}' 已在其他列中显示")
            return

        column_keys[col_idx] = field_key
        _set_overview_columns(column_keys)
        self.refresh()

    def _on_item_changed(self, item: QTableWidgetItem):
        """单元格编辑完成后回写到 JSON"""
        if self._loading:
            return

        row = item.row()
        col = item.column()

        # 第一列是角色名，不可编辑，跳过
        if col < 1:
            return

        from ..profile import get_profile_config
        config = get_profile_config()

        column_keys = _get_overview_columns()
        field_idx = col - 1
        if field_idx >= len(column_keys):
            return

        field_key = column_keys[field_idx]
        field_def = config.get_field(field_key)
        if not field_def or field_def.readonly:
            return

        # 获取角色名（第一列）
        name_item = self._table.item(row, 0)
        if not name_item:
            return
        user_name = name_item.text()

        # 解析新值
        new_value = item.text()
        if field_def.type == "int":
            try:
                new_value = int(new_value) if new_value else 0
            except ValueError:
                QMessageBox.warning(self, "输入错误", f"{field_def.label} 必须是整数")
                self._loading = True
                item.setText(str(self._get_field_value(field_def, user_name, self._load_user_data(user_name))))
                self._loading = False
                return
        elif field_def.type == "bool":
            upper = new_value.upper()
            if upper in ("", "N", "FALSE", "0", "否", "NO"):
                new_value = False
            elif upper in ("Y", "TRUE", "1", "是", "YES"):
                new_value = True
            else:
                QMessageBox.warning(self, "输入错误", f"{field_def.label} 必须是布尔值（Y/N）")
                self._loading = True
                item.setText(str(self._get_field_value(field_def, user_name, self._load_user_data(user_name))))
                self._loading = False
                return

        # 回写到 JSON
        self._write_field_value(user_name, field_def, new_value)

    def _write_field_value(self, user_name: str, field_def, value):
        """将字段值写回用户 JSON 文件"""
        import json

        user_file = USERS_DIR / f"{user_name}.json"
        try:
            # 加载数据
            data = json.loads(user_file.read_text(encoding="utf-8")) if user_file.exists() else {}

            source = field_def.source
            if not source:
                data[field_def.key] = value
            else:
                parts = source.split(".")
                obj = data
                # 验证路径存在性，不静默创建
                for part in parts[:-1]:
                    if part not in obj:
                        logger.warning(f"source 路径 '{source}' 中 '{part}' 不存在，跳过写入")
                        return
                    if not isinstance(obj[part], dict):
                        logger.warning(f"source 路径 '{source}' 中 '{part}' 不是 dict，跳过写入")
                        return
                    obj = obj[part]
                obj[parts[-1]] = value

            # 直接写入以便捕获异常
            user_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            logger.debug(f"已回写 {user_name}.{field_def.key} = {value}")
        except Exception as e:
            logger.error(f"回写失败: {e}")
            QMessageBox.warning(self, "保存失败", f"回写用户数据失败:\n{e}")

    def _load_all_users(self) -> dict[str, dict]:
        """加载所有用户数据（按用户管理定义的顺序）"""
        result = {}
        if not USERS_DIR.exists():
            return result

        user_mgr = UserConfigManager()
        ordered_names = user_mgr.list_users()

        mgr = SessionManager()
        for user_name in ordered_names:
            user_file = USERS_DIR / f"{user_name}.json"
            if not user_file.exists():
                continue
            try:
                data = mgr.load(user_name)
                result[user_name] = data
            except Exception as e:
                logger.warning(f"加载用户 {user_name} 失败: {e}")
                result[user_name] = {}

        return result

    def _load_user_data(self, user_name: str) -> dict:
        """加载单个用户数据"""
        if not USERS_DIR.exists():
            return {}
        try:
            mgr = SessionManager()
            return mgr.load(user_name)
        except Exception as e:
            logger.warning(f"加载用户 {user_name} 失败: {e}")
            return {}

    def _get_field_value(self, field_def, user_name: str, data: dict) -> str:
        """从用户数据中提取字段值"""
        key = field_def.key
        source = field_def.source

        # computed/duration 类型暂无数据源
        if field_def.type in ("computed", "duration"):
            return ""

        # 按 source 路径提取
        if not source:
            value = data.get(key)
        else:
            parts = source.split(".")
            value = data
            for part in parts:
                if isinstance(value, dict):
                    value = value.get(part)
                else:
                    return ""
                if value is None:
                    return ""

        # 格式化
        if field_def.type == "bool":
            return "Y" if value else ""
        return str(value) if value is not None else ""

    def _open_metadata_dialog(self):
        """打开元数据定义对话框"""
        from .profile_settings_dialog import MetadataDialog
        dialog = MetadataDialog(self)
        if dialog.exec():
            from ..profile import reload_profile_config
            reload_profile_config()
            self.refresh()


# ─── 角色状态 Tab ────────────────────────────────────────────


class ProfileTab(QWidget):
    """角色状态 Tab - 按角色分 Tab 展示详细信息"""

    def __init__(self, host, parent=None):
        super().__init__(parent)
        self._host = host
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        # 标题行
        title_row = QHBoxLayout()
        title = QLabel("角色详情")
        title.setStyleSheet("font-size: 15px; font-weight: bold; color: #333333;")
        title_row.addWidget(title)
        title_row.addStretch()

        btn_refresh = QPushButton("刷新")
        btn_refresh.setFixedWidth(60)
        btn_refresh.setToolTip("重新读取角色数据")
        btn_refresh.setStyleSheet(_REFRESH_BTN_STYLE)
        btn_refresh.clicked.connect(self._refresh_all)
        title_row.addWidget(btn_refresh)

        layout.addLayout(title_row)

        # Tab 容器
        self._tab_widget = QTabWidget()
        self._tab_widget.setTabsClosable(False)
        self._tab_widget.setMovable(True)
        layout.addWidget(self._tab_widget, stretch=1)

        # 加载角色 Tab
        self._refresh_character_tabs()

    def _refresh_character_tabs(self):
        """从用户管理加载所有角色，按定义顺序创建 Tab"""
        while self._tab_widget.count() > 0:
            self._tab_widget.removeTab(0)

        user_mgr = UserConfigManager()
        ordered_names = user_mgr.list_users()

        for user_name in ordered_names:
            user_file = USERS_DIR / f"{user_name}.json"
            if not user_file.exists():
                continue
            detail_page = _DetailPage(user_name)
            self._tab_widget.addTab(detail_page, user_name)

    def _refresh_all(self):
        """刷新所有角色详情页"""
        for i in range(self._tab_widget.count()):
            page = self._tab_widget.widget(i)
            if isinstance(page, _DetailPage):
                page.refresh()


# ─── 角色详情页 ──────────────────────────────────────────────


class _DetailPage(QWidget):
    """角色详情页 - 按分组展示单个角色的完整信息"""

    def __init__(self, user_name: str, parent=None):
        super().__init__(parent)
        self._user_name = user_name
        self._group_boxes: dict[str, QGroupBox] = {}
        self._field_labels: dict[str, QLabel] = {}
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        # 滚动区域
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self._container = QWidget()
        self._form_layout = QVBoxLayout(self._container)
        self._form_layout.setSpacing(8)
        scroll.setWidget(self._container)
        layout.addWidget(scroll)

        # 初始加载
        self._build_form()
        self.refresh()

    def _build_form(self):
        """根据 profiles.yaml 构建分组表单"""
        from ..profile import get_profile_config

        config = get_profile_config()
        groups = config.get_sorted_groups()

        for group_def in groups:
            fields = config.get_fields_by_group(group_def.key)
            if not fields:
                continue

            box = QGroupBox(group_def.label)
            box.setStyleSheet("""
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
            """)

            form = QFormLayout(box)
            form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

            for field_def in fields:
                label = QLabel("—")
                label.setStyleSheet("color: #333333;")
                form.addRow(f"{field_def.label}:", label)
                self._field_labels[field_def.key] = label

            self._group_boxes[group_def.key] = box
            self._form_layout.addWidget(box)

        self._form_layout.addStretch()

    def refresh(self):
        """从用户 JSON 文件加载数据并刷新表单"""
        if not USERS_DIR.exists():
            return

        user_file = USERS_DIR / f"{self._user_name}.json"
        if not user_file.exists():
            return

        try:
            mgr = SessionManager()
            data = mgr.load(self._user_name)
        except Exception as e:
            logger.warning(f"加载用户 {self._user_name} 失败: {e}")
            return

        for key, label in self._field_labels.items():
            value = self._get_field_value(key, data)
            label.setText(str(value) if value else "—")

    def _get_field_value(self, key: str, data: dict) -> str:
        """从用户数据中提取字段值"""
        from ..profile import get_profile_config

        config = get_profile_config()
        field_def = config.get_field(key)
        if not field_def:
            return ""

        # computed/duration 暂无数据
        if field_def.type in ("computed", "duration"):
            return ""

        source = field_def.source
        if not source:
            value = data.get(key)
        else:
            parts = source.split(".")
            value = data
            for part in parts:
                if isinstance(value, dict):
                    value = value.get(part)
                else:
                    return ""
                if value is None:
                    return ""

        if field_def.type == "bool":
            return "Y" if value else ""
        return str(value) if value is not None else ""
