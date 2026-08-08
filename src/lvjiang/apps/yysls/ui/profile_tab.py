"""燕云「档案总览」与「其他信息」Tab

档案总览：宽表展示所有角色的概要信息，交互式列头配置
其他信息：展示当前用户的详细信息（按模型类型分区）

数据来源：user.json 的 profile 节点
    profile:
      daily:
        key_name: { value: ..., updated_at: ... }
      realtime:
        key_name: { value: ..., updated_at: ... }
      resource:
        key_name: { value: ..., updated_at: ... }
      activity:
        key_name: { value: ..., total: ..., updated_at: ... }
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
    QVBoxLayout,
    QWidget,
)

from lvjiang.constants import USERS_DIR
from lvjiang.core.config import get_session_store
from lvjiang.core.user_config import UserConfigManager

from ..config.profile_models import (
    MODEL_ACTIVITY,
    MODEL_DAILY,
    MODEL_LABELS,
    MODEL_REALTIME,
    MODEL_RESOURCE,
    ActivityKeyDef,
    DailyKeyDef,
    KeyDef,
    RealtimeKeyDef,
)
from ..config.user_profile import read_profile_entry

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
    """保存总览列配置到 session.json"""
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

        # 刷新按钮（右上角）
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_refresh = QPushButton("刷新")
        btn_refresh.setFixedWidth(60)
        btn_refresh.setToolTip("重新读取角色数据")
        btn_refresh.setStyleSheet(_REFRESH_BTN_STYLE)
        btn_refresh.clicked.connect(self.refresh)
        btn_row.addWidget(btn_refresh)
        layout.addLayout(btn_row)

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
        self._table.cellDoubleClicked.connect(self._on_cell_double_clicked)

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

        btn_settings = QPushButton("数据模型")
        btn_settings.setFixedWidth(70)
        btn_settings.setToolTip("定义数据模型 key")
        btn_settings.clicked.connect(self._open_metadata_dialog)
        btn_row.addWidget(btn_settings)

        layout.addLayout(btn_row)

        # 初始加载
        self._loading = False
        self._editing_cap_cell = False  # 标记正在编辑带 cap 的单元格
        self.refresh()

    def refresh(self):
        """刷新表格数据"""
        from ..config import get_profile_config

        config = get_profile_config()
        column_keys = _get_overview_columns()

        # 过滤掉不存在的 key
        valid_keys = [k for k in column_keys if config.get_key(k)]
        if valid_keys != column_keys:
            _set_overview_columns(valid_keys)
            column_keys = valid_keys

        key_defs = [config.get_key(k) for k in column_keys]

        # 第一列固定为角色名，后续列为配置的 key
        col_count = 1 + len(key_defs)
        self._table.setColumnCount(col_count)
        headers = ["角色名"] + [kd.label for kd in key_defs]
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

            # 后续列：配置的 key
            for col, kd in enumerate(key_defs):
                model_type = config.get_model_type(kd.key) or ""
                value = self._format_profile_value(kd, model_type, data)
                item = QTableWidgetItem(value)
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self._table.setItem(row, col + 1, item)

        # 调整列宽
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setStretchLastSection(True)
        self._loading = False

    def _format_profile_value(self, kd: KeyDef, model_type: str, data: dict) -> str:
        """根据模型类型格式化 profile 值用于总览显示"""
        entry = read_profile_entry(data, model_type, kd.key)
        if not entry:
            return ""

        value = entry.get("value")
        if value is None:
            return ""

        if model_type == MODEL_DAILY:
            if isinstance(kd, DailyKeyDef) and kd.cap:
                return f"{value}/{kd.cap}"
            return str(value)

        if model_type == MODEL_REALTIME:
            if isinstance(kd, RealtimeKeyDef) and kd.cap:
                return f"{value}/{kd.cap}"
            return str(value)

        if model_type == MODEL_RESOURCE:
            return str(value)

        if model_type == MODEL_ACTIVITY:
            total = entry.get("total", 0)
            if isinstance(kd, ActivityKeyDef):
                return f"{value}/{kd.period_cap}  ({total}/{kd.lifetime_cap})"
            return str(value)

        return str(value)

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

        from ..config import get_profile_config
        config = get_profile_config()
        all_keys = config.get_all_keys()

        if not all_keys:
            QMessageBox.information(self, "提示", "没有可用的数据模型 key，请先在数据模型定义中添加")
            return

        # 获取当前列的 key
        column_keys = _get_overview_columns()
        current_key = column_keys[logical_index - 1] if logical_index - 1 < len(column_keys) else ""

        # 过滤掉已被其他列使用的 key（保留当前列）
        available = [
            kd for kd in all_keys
            if kd.key not in column_keys or kd.key == current_key
        ]

        # 创建下拉选择对话框
        from PyQt6.QtWidgets import QDialog, QVBoxLayout

        dialog = QDialog(self)
        dialog.setWindowTitle("选择字段")
        dialog.setMinimumWidth(250)
        layout = QVBoxLayout(dialog)

        combo = QComboBox()
        combo.addItem("（请选择）", "")
        for kd in available:
            model_type = config.get_model_type(kd.key) or ""
            model_label = MODEL_LABELS.get(model_type, model_type)
            combo.addItem(f"[{model_label}] {kd.label} ({kd.key})", kd.key)

        # 预选当前 key
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
        from ..config import get_profile_config
        config = get_profile_config()
        all_keys = config.get_all_keys()

        if not all_keys:
            QMessageBox.information(self, "提示", "没有可用的数据模型 key，请先在数据模型定义中添加")
            return

        # 过滤掉已使用的 key
        column_keys = _get_overview_columns()
        available = [kd for kd in all_keys if kd.key not in column_keys]

        if not available:
            QMessageBox.information(self, "提示", "所有数据模型 key 都已被使用")
            return

        # 弹出选择对话框
        from PyQt6.QtWidgets import QDialog, QVBoxLayout

        dialog = QDialog(self)
        dialog.setWindowTitle("选择字段")
        dialog.setMinimumWidth(250)
        layout = QVBoxLayout(dialog)

        combo = QComboBox()
        for kd in available:
            model_type = config.get_model_type(kd.key) or ""
            model_label = MODEL_LABELS.get(model_type, model_type)
            combo.addItem(f"[{model_label}] {kd.label} ({kd.key})", kd.key)
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
                column_keys = _get_overview_columns()
                if selected_key in column_keys:
                    QMessageBox.warning(self, "重复", f"Key '{selected_key}' 已在其他列中显示")
                    return
                column_keys.insert(after_index, selected_key)
                _set_overview_columns(column_keys)
                self.refresh()

    def _remove_column(self, logical_index: int):
        """删除指定列"""
        column_keys = _get_overview_columns()
        col_idx = logical_index - 1
        if 0 <= col_idx < len(column_keys):
            del column_keys[col_idx]
            _set_overview_columns(column_keys)
            self.refresh()

    def _set_column_field(self, logical_index: int, field_key: str):
        """设置指定列的字段"""
        column_keys = _get_overview_columns()
        col_idx = logical_index - 1
        if not (0 <= col_idx < len(column_keys)):
            return

        if field_key in column_keys and column_keys.index(field_key) != col_idx:
            QMessageBox.warning(self, "重复", f"Key '{field_key}' 已在其他列中显示")
            return

        column_keys[col_idx] = field_key
        _set_overview_columns(column_keys)
        self.refresh()

    def _on_cell_double_clicked(self, row: int, col: int):
        """单元格双击：对有 cap 的列，剥离 /cap 后缀再进入编辑"""
        if col < 1 or self._loading:
            return

        from ..config import get_profile_config
        config = get_profile_config()
        column_keys = _get_overview_columns()
        field_idx = col - 1
        if field_idx >= len(column_keys):
            return

        kd = config.get_key(column_keys[field_idx])
        if not kd:
            return

        model_type = config.get_model_type(column_keys[field_idx]) or ""
        has_cap = (
            (model_type == MODEL_REALTIME and isinstance(kd, RealtimeKeyDef) and kd.cap is not None)
            or (model_type == MODEL_DAILY and isinstance(kd, DailyKeyDef) and kd.cap is not None)
        )
        if not has_cap:
            return

        item = self._table.item(row, col)
        if not item:
            return

        # 从 "500/600" 提取 "500"，让用户只编辑当前值
        text = item.text()
        if "/" in text:
            value_part = text.split("/")[0].strip()
            self._editing_cap_cell = True
            item.setText(value_part)
            self._editing_cap_cell = False

    def _on_item_changed(self, item: QTableWidgetItem):
        """单元格编辑完成后回写到 profile 节点"""
        if self._loading or self._editing_cap_cell:
            return

        row = item.row()
        col = item.column()

        if col < 1:
            return

        from ..config import get_profile_config
        config = get_profile_config()

        column_keys = _get_overview_columns()
        field_idx = col - 1
        if field_idx >= len(column_keys):
            return

        key_str = column_keys[field_idx]
        kd = config.get_key(key_str)
        if not kd:
            return

        model_type = config.get_model_type(key_str) or ""

        # 获取角色名
        name_item = self._table.item(row, 0)
        if not name_item:
            return
        user_name = name_item.text()

        # 解析新值
        raw_value = item.text()
        parsed_value = self._parse_value(raw_value, model_type, kd)
        if parsed_value is _PARSE_ERROR:
            self._loading = True
            user_data = self._load_user_data(user_name)
            item.setText(self._format_profile_value(kd, model_type, user_data))
            self._loading = False
            return

        # 回写到 profile 节点
        self._write_profile_entry(user_name, model_type, key_str, parsed_value)

        # 刷新单元格显示（恢复 value/cap 格式）
        self._loading = True
        user_data = self._load_user_data(user_name)
        item.setText(self._format_profile_value(kd, model_type, user_data))
        self._loading = False

    @staticmethod
    def _parse_value(raw: str, model_type: str, kd: KeyDef):
        """解析用户输入值，返回解析后的值或 _PARSE_ERROR"""
        if model_type == MODEL_DAILY:
            # daily 可以是 int 或 bool（如 shop_of_week）
            if isinstance(kd, DailyKeyDef) and kd.cap is not None:
                try:
                    return int(raw) if raw else 0
                except ValueError:
                    QMessageBox.warning(None, "输入错误", f"{kd.label} 必须是整数")
                    return _PARSE_ERROR
            # 无 cap 的 daily 可能是 bool
            upper = raw.upper()
            if upper in ("Y", "TRUE", "1", "是", "YES"):
                return True
            if upper in ("", "N", "FALSE", "0", "否", "NO"):
                return False
            try:
                return int(raw)
            except ValueError:
                return raw

        if model_type == MODEL_REALTIME:
            try:
                return int(raw) if raw else 0
            except ValueError:
                QMessageBox.warning(None, "输入错误", f"{kd.label} 必须是整数")
                return _PARSE_ERROR

        if model_type == MODEL_RESOURCE:
            try:
                return int(raw) if raw else 0
            except ValueError:
                QMessageBox.warning(None, "输入错误", f"{kd.label} 必须是整数")
                return _PARSE_ERROR

        if model_type == MODEL_ACTIVITY:
            try:
                return int(raw) if raw else 0
            except ValueError:
                QMessageBox.warning(None, "输入错误", f"{kd.label} 必须是整数")
                return _PARSE_ERROR

        return raw

    def _write_profile_entry(self, user_name: str, model_type: str, key: str, value):
        """将值写入 user.json 的 profile 节点（通过 SessionManager，与引擎统一写入通道）"""
        try:
            mgr = self._host.session_manager
            data = mgr.load(user_name)
            from ..config.user_profile import write_profile_entry as _write_entry
            _write_entry(data, model_type, key, value)
            mgr.save(user_name, data)
            logger.debug(f"已回写 {user_name}.profile.{model_type}.{key} = {value}")
        except Exception as e:
            logger.error(f"回写失败: {e}")
            QMessageBox.warning(self, "保存失败", f"回写用户数据失败:\n{e}")

    def _load_all_users(self) -> dict[str, dict]:
        """加载所有用户数据（按用户管理定义的顺序）"""
        result: dict[str, dict] = {}
        if not USERS_DIR.exists():
            return result

        user_mgr = UserConfigManager()
        ordered_names = user_mgr.list_users()

        mgr = self._host.session_manager
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
            mgr = self._host.session_manager
            return mgr.load(user_name)
        except Exception as e:
            logger.warning(f"加载用户 {user_name} 失败: {e}")
            return {}

    def _open_metadata_dialog(self):
        """打开数据模型定义对话框"""
        from .profile_settings_dialog import ProfileDefinitionDialog
        dialog = ProfileDefinitionDialog(self)
        if dialog.exec():
            from ..config import reload_profile_config
            reload_profile_config()
            self.refresh()


# 哨兵值：表示解析失败
_PARSE_ERROR = object()


# ─── 角色状态 Tab ────────────────────────────────────────────


class ProfileTab(QWidget):
    """其他信息 Tab - 展示当前用户的详细信息（按模型类型分区）"""

    def __init__(self, host, parent=None):
        super().__init__(parent)
        self._host = host
        self._detail_page: _DetailPage | None = None
        self._setup_ui()
        self._refresh_current_user()
        host.user_changed.connect(lambda _name: self._refresh_current_user())

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        # 刷新按钮
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_refresh = QPushButton("刷新")
        btn_refresh.setFixedWidth(60)
        btn_refresh.setToolTip("重新读取角色数据")
        btn_refresh.setStyleSheet(_REFRESH_BTN_STYLE)
        btn_refresh.clicked.connect(self._refresh_current_user)
        btn_row.addWidget(btn_refresh)
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

        self._detail_page = _DetailPage(user_name, self._host.session_manager)
        self._detail_container.addWidget(self._detail_page)


# ─── 角色详情页 ──────────────────────────────────────────────


class _DetailPage(QWidget):
    """角色详情页 - 按模型类型分区展示单个角色的完整信息"""

    def __init__(self, user_name: str, session_manager, parent=None):
        super().__init__(parent)
        self._user_name = user_name
        self._session_manager = session_manager
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
        """按模型类型分区构建表单"""
        from ..config import get_profile_config

        config = get_profile_config()

        for model_type in (MODEL_DAILY, MODEL_REALTIME, MODEL_RESOURCE, MODEL_ACTIVITY):
            keys = config.get_keys_by_model(model_type)
            if not keys:
                continue

            model_label = MODEL_LABELS.get(model_type, model_type)
            box = QGroupBox(model_label)
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

            for kd in keys:
                label = QLabel("")
                label.setStyleSheet("color: #333333;")
                form.addRow(f"{kd.label}:", label)
                self._value_labels[kd.key] = label

            self._form_layout.addWidget(box)

        self._form_layout.addStretch()

    def refresh(self):
        """从用户 JSON 文件加载数据并刷新"""
        if not USERS_DIR.exists():
            return

        user_file = USERS_DIR / f"{self._user_name}.json"
        if not user_file.exists():
            return

        try:
            data = self._session_manager.load(self._user_name)
        except Exception as e:
            logger.warning(f"加载用户 {self._user_name} 失败: {e}")
            return

        from ..config import get_profile_config
        config = get_profile_config()

        for key, label in self._value_labels.items():
            model_type = config.get_model_type(key) or ""
            kd = config.get_key(key)
            if not kd:
                label.setText("")
                continue

            text = self._format_detail_value(kd, model_type, data)
            label.setText(text)

    def _format_detail_value(self, kd: KeyDef, model_type: str, data: dict) -> str:
        """格式化详情页的值显示"""
        entry = read_profile_entry(data, model_type, kd.key)
        if not entry:
            return ""

        value = entry.get("value")
        if value is None:
            return ""

        if model_type == MODEL_DAILY:
            if isinstance(kd, DailyKeyDef) and kd.cap:
                return f"{value} / {kd.cap}  (周期: {kd.period})"
            if isinstance(value, bool):
                return "已完成" if value else "未完成"
            return str(value)

        if model_type == MODEL_REALTIME:
            if isinstance(kd, RealtimeKeyDef):
                return f"{value} / {kd.cap}  (回复: {kd.regen_rate}/min)"
            return str(value)

        if model_type == MODEL_RESOURCE:
            return str(value)

        if model_type == MODEL_ACTIVITY:
            total = entry.get("total", 0)
            if isinstance(kd, ActivityKeyDef):
                return f"当期: {value}/{kd.period_cap}  总计: {total}/{kd.lifetime_cap}"
            return str(value)

        return str(value)
