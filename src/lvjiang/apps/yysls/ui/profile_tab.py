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
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QMenu,
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
from ..config.profile_store import (
    get_active_group,
    get_groups,
    migrate_from_legacy,
    save_groups,
    set_active_group,
)
from ..config.user_profile import read_profile_entry
from ..profile.profile_engine import _compute_realtime_value

# 统一的刷新按钮样式
_REFRESH_BTN_STYLE = (
    "QPushButton { background-color: #607D8B; color: white; font-size: 12px; "
    "padding: 4px; border-radius: 3px; }"
    "QPushButton:hover { background-color: #78909C; }"
)

# 列宽配置仍在 ui_state 下
_COLUMN_WIDTHS_KEY = "profile_overview_column_widths"


def _get_column_widths() -> dict:
    """获取各分组列宽配置 {group_name: [width, ...]}"""
    ui_state = get_session_store().get_node("ui_state", {})
    if isinstance(ui_state, dict):
        return ui_state.get(_COLUMN_WIDTHS_KEY, {})
    return {}


def _save_column_widths(widths: dict) -> None:
    """保存各分组列宽到 ui_state"""
    get_session_store().update_node("ui_state", {_COLUMN_WIDTHS_KEY: widths})

# ─── 档案总览 Tab ────────────────────────────────────────────


class ProfileOverviewTab(QWidget):
    """档案总览 Tab - QTabWidget 分组展示，每组一个表格，角色名固定首列"""

    def __init__(self, host, parent=None):
        super().__init__(parent)
        self._host = host
        self._tables: dict[str, QTableWidget] = {}
        self._loading = False
        self._editing_cap_cell = False
        self._restoring_widths = False
        self._reordering = False
        migrate_from_legacy()
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        # ── 工具栏：刷新 + 分组管理 ──
        toolbar = QHBoxLayout()

        btn_refresh = QPushButton("刷新")
        btn_refresh.setFixedWidth(60)
        btn_refresh.setToolTip("重新读取角色数据")
        btn_refresh.setStyleSheet(_REFRESH_BTN_STYLE)
        btn_refresh.clicked.connect(self.refresh)
        toolbar.addWidget(btn_refresh)

        btn_add_group = QPushButton("新建分组")
        btn_add_group.setFixedWidth(70)
        btn_add_group.clicked.connect(self._add_group)
        toolbar.addWidget(btn_add_group)

        btn_rename_group = QPushButton("重命名分组")
        btn_rename_group.setFixedWidth(80)
        btn_rename_group.clicked.connect(self._rename_group)
        toolbar.addWidget(btn_rename_group)

        btn_remove_group = QPushButton("删除分组")
        btn_remove_group.setFixedWidth(70)
        btn_remove_group.clicked.connect(self._remove_group)
        toolbar.addWidget(btn_remove_group)

        toolbar.addStretch()
        layout.addLayout(toolbar)

        # ── QTabWidget：每个分组一个 Tab ──
        self._tab_widget = QTabWidget()
        self._tab_widget.currentChanged.connect(self._on_tab_changed)
        layout.addWidget(self._tab_widget, stretch=1)

        # ── 底部按钮行 ──
        btn_row = QHBoxLayout()
        btn_row.addStretch()

        btn_settings = QPushButton("数据模型")
        btn_settings.setFixedWidth(70)
        btn_settings.setToolTip("定义数据模型 key")
        btn_settings.clicked.connect(self._open_metadata_dialog)
        btn_row.addWidget(btn_settings)

        layout.addLayout(btn_row)

        self._build_groups()

    # ─── 分组构建与刷新 ────────────────────────────────────────

    def _build_groups(self):
        """根据分组数据重建所有 Tab 页签和表格"""
        self._tab_widget.blockSignals(True)
        self._tab_widget.clear()
        self._tables.clear()

        groups = get_groups()
        if not groups:
            groups = {"默认": {"columns": []}}
            save_groups(groups)

        active_group = get_active_group()
        active_idx = 0

        for idx, (group_name, _group_data) in enumerate(groups.items()):
            table = self._create_table_for_group(group_name)
            self._tables[group_name] = table
            self._tab_widget.addTab(table, group_name)
            if group_name == active_group:
                active_idx = idx

        self._tab_widget.blockSignals(False)
        if self._tab_widget.count() > 0:
            self._tab_widget.setCurrentIndex(active_idx)
            # _on_tab_changed 会自动调用 set_active_group

    def _create_table_for_group(self, group_name: str) -> QTableWidget:
        """为指定分组创建并绑定一个 QTableWidget"""
        table = QTableWidget()
        table.setAlternatingRowColors(True)
        table.setShowGrid(True)
        v_header = table.verticalHeader()
        assert v_header is not None
        v_header.setDefaultSectionSize(24)
        v_header.setVisible(False)
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)

        # 右键菜单：快速增减数值
        table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        table.customContextMenuRequested.connect(
            lambda pos, gn=group_name, t=table: self._on_cell_context_menu(pos, gn, t)
        )

        # 绑定信号（通过 lambda 携带 group_name）
        table.itemChanged.connect(lambda item, gn=group_name: self._on_item_changed(item, gn))
        table.cellDoubleClicked.connect(
            lambda row, col, gn=group_name: self._on_cell_double_clicked(row, col, gn)
        )

        h_header = table.horizontalHeader()
        assert h_header is not None
        h_header.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        h_header.setSectionsMovable(True)  # 允许拖拽列头调整顺序
        h_header.customContextMenuRequested.connect(
            lambda pos, gn=group_name: self._on_header_context_menu(pos, gn)
        )
        h_header.sectionDoubleClicked.connect(
            lambda idx, gn=group_name: self._on_header_double_clicked(idx, gn)
        )
        h_header.sectionMoved.connect(
            lambda _logical, _old, _new, gn=group_name, t=table: self._on_columns_reordered(gn, t)
        )
        h_header.sectionResized.connect(
            lambda _logical, _old, _new, gn=group_name, t=table: self._on_column_resized(gn, t)
        )

        self._refresh_group(group_name, table)
        return table

    def refresh(self):
        """刷新所有分组的表格数据"""
        for group_name, table in self._tables.items():
            self._refresh_group(group_name, table)

    def _refresh_group(self, group_name: str, table: QTableWidget):
        """刷新指定分组的表格数据"""
        from ..config import get_profile_config

        config = get_profile_config()
        groups = get_groups()
        group_data = groups.get(group_name, {})
        column_keys = group_data.get("columns", [])

        # 过滤掉不存在的 key
        valid_keys = [k for k in column_keys if config.get_key(k)]
        if valid_keys != column_keys:
            group_data["columns"] = valid_keys
            groups[group_name] = group_data
            save_groups(groups)
            column_keys = valid_keys

        key_defs = [kd for kd in (config.get_key(k) for k in column_keys) if kd is not None]

        # 第一列固定为角色名
        col_count = 1 + len(key_defs)
        table.setColumnCount(col_count)
        headers = ["角色名"] + [kd.label for kd in key_defs]
        table.setHorizontalHeaderLabels(headers)

        users_data = self._load_all_users()

        self._loading = True
        table.setRowCount(len(users_data))
        for row, (name, data) in enumerate(users_data.items()):
            name_item = QTableWidgetItem(name)
            name_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            name_item.setFlags(name_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            name_item.setForeground(Qt.GlobalColor.darkGray)
            table.setItem(row, 0, name_item)

            for col, kd in enumerate(key_defs):
                model_type = config.get_model_type(kd.key) or ""
                display_text, style = self._format_profile_cell(kd, model_type, data)
                item = QTableWidgetItem(display_text)
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self._apply_cell_style(item, style)
                table.setItem(row, col + 1, item)

        header = table.horizontalHeader()
        assert header is not None
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setDefaultSectionSize(120)
        header.setStretchLastSection(False)
        self._loading = False
        self._restore_column_widths(group_name, table)

    def _format_profile_cell(self, kd: KeyDef, model_type: str, data: dict) -> tuple[str, str]:
        """根据模型类型格式化 profile 值用于总览显示

        返回 (display_text, style)，style 为 "" | "red_bold" | "orange_bold"
        """
        entry = read_profile_entry(data, model_type, kd.key)
        if not entry:
            return "", ""

        value = entry.get("value")
        if value is None:
            return "", ""

        if model_type == MODEL_DAILY:
            if isinstance(kd, DailyKeyDef) and kd.show_cap and kd.cap:
                return f"{int(value)}/{kd.cap}", ""
            return str(int(value)), ""

        if model_type == MODEL_REALTIME:
            if isinstance(kd, RealtimeKeyDef):
                # 实时计算当前值
                updated_at_str = entry.get("updated_at", "")
                computed, _ = _compute_realtime_value(
                    value, updated_at_str,
                    kd.regen_period, kd.regen_value, kd.cap,
                    kd.reset_time,
                )
                int_value = int(computed)
                style = ""
                if kd.cap is not None and computed >= kd.cap:
                    style = "red_bold"
                elif kd.alert_above is not None and computed >= kd.alert_above:
                    style = "orange_bold"
                if kd.show_cap and kd.cap:
                    return f"{int_value}/{kd.cap}", style
                return str(int_value), style
            return str(int(value)), ""

        if model_type == MODEL_RESOURCE:
            return str(int(value)), ""

        if model_type == MODEL_ACTIVITY:
            if isinstance(kd, ActivityKeyDef) and kd.show_cap and kd.cap:
                return f"{int(value)}/{kd.cap}", ""
            return str(int(value)), ""

        return str(value), ""

    @staticmethod
    def _apply_cell_style(item: QTableWidgetItem, style: str) -> None:
        """应用单元格样式: '' | 'red_bold' | 'orange_bold'"""
        if style == "red_bold":
            font = QFont(item.font())
            font.setBold(True)
            item.setFont(font)
            item.setForeground(Qt.GlobalColor.red)
        elif style == "orange_bold":
            font = QFont(item.font())
            font.setBold(True)
            item.setFont(font)
            item.setForeground(QColor(255, 165, 0))  # 橙色

    def _on_columns_reordered(self, group_name: str, table: QTableWidget):
        """拖拽列头后持久化新顺序"""
        if self._reordering or self._loading:
            return
        self._reordering = True
        try:
            h_header = table.horizontalHeader()
            assert h_header is not None
            groups = get_groups()
            group_data = groups.get(group_name, {"columns": []})
            column_keys = group_data.get("columns", [])
            # 跳过第 0 列（角色名），从第 1 列开始读取新的视觉顺序
            new_order = []
            for visual_idx in range(1, h_header.count()):
                logical_idx = h_header.logicalIndex(visual_idx)
                if 0 <= logical_idx - 1 < len(column_keys):
                    new_order.append(column_keys[logical_idx - 1])

            if column_keys != new_order:
                group_data["columns"] = new_order
                groups[group_name] = group_data
                save_groups(groups)
                self._refresh_group(group_name, table)
        finally:
            self._reordering = False

    def _on_column_resized(self, group_name: str, table: QTableWidget):
        """列宽拖拽调整后持久化"""
        if self._restoring_widths or self._loading:
            return
        h_header = table.horizontalHeader()
        assert h_header is not None
        widths = [h_header.sectionSize(i) for i in range(h_header.count())]
        all_widths = _get_column_widths()
        all_widths[group_name] = widths
        _save_column_widths(all_widths)

    def _restore_column_widths(self, group_name: str, table: QTableWidget):
        """恢复指定分组的列宽配置"""
        all_widths = _get_column_widths()
        widths = all_widths.get(group_name)
        if not widths:
            return
        h_header = table.horizontalHeader()
        assert h_header is not None
        if len(widths) != h_header.count():
            return
        self._restoring_widths = True
        for idx, w in enumerate(widths):
            h_header.resizeSection(idx, w)
        self._restoring_widths = False

    def _on_header_context_menu(self, pos, group_name: str):
        """表头右键菜单（分组上下文）"""
        from PyQt6.QtWidgets import QMenu

        table = self._tables.get(group_name)
        if not table:
            return

        h_header = table.horizontalHeader()
        assert h_header is not None
        logical_index = h_header.logicalIndexAt(pos)
        menu = QMenu(self)

        menu.addAction("右侧新增列", lambda: self._add_column(group_name, logical_index))

        if logical_index > 0:
            menu.addAction("删除当前列", lambda: self._remove_column(group_name, logical_index))

        menu.exec(h_header.mapToGlobal(pos))

    def _on_header_double_clicked(self, logical_index: int, group_name: str):
        """表头双击：选择字段（角色名列不可修改）"""
        if logical_index < 1:
            return

        from ..config import get_profile_config
        config = get_profile_config()
        all_keys = config.get_all_keys()

        if not all_keys:
            QMessageBox.information(self, "提示", "没有可用的数据模型 key，请先在数据模型定义中添加")
            return

        groups = get_groups()
        column_keys = groups.get(group_name, {}).get("columns", [])
        current_key = column_keys[logical_index - 1] if logical_index - 1 < len(column_keys) else ""

        from PyQt6.QtWidgets import QDialog, QVBoxLayout

        dialog = QDialog(self)
        dialog.setWindowTitle("选择字段")
        dialog.setMinimumWidth(250)
        layout = QVBoxLayout(dialog)

        combo = QComboBox()
        combo.addItem("（请选择）", "")
        for kd in all_keys:
            model_type = config.get_model_type(kd.key) or ""
            model_label = MODEL_LABELS.get(model_type, model_type)
            combo.addItem(f"[{model_label}] {kd.label} ({kd.key})", kd.key)

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
                self._set_column_field(group_name, logical_index, selected_key)

    def _add_column(self, group_name: str, after_index: int):
        """在指定分组的指定列后新增一列"""
        from ..config import get_profile_config
        config = get_profile_config()

        # 过滤掉该分组已有的 key
        groups = get_groups()
        group_data = groups.get(group_name, {"columns": []})
        used_keys = set(group_data.get("columns", []))
        all_keys = [kd for kd in config.get_all_keys() if kd.key not in used_keys]

        if not all_keys:
            QMessageBox.information(self, "提示", "没有可用的数据模型 key，请先在数据模型定义中添加")
            return

        from PyQt6.QtWidgets import QDialog, QVBoxLayout

        dialog = QDialog(self)
        dialog.setWindowTitle("选择字段")
        dialog.setMinimumWidth(250)
        layout = QVBoxLayout(dialog)

        combo = QComboBox()
        for kd in all_keys:
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
                groups = get_groups()
                group_data = groups.get(group_name, {"columns": []})
                column_keys = group_data.get("columns", [])
                if selected_key in column_keys:
                    QMessageBox.warning(self, "重复", f"Key '{selected_key}' 已在该分组中显示")
                    return
                insert_idx = max(0, min(after_index, len(column_keys)))
                column_keys.insert(insert_idx, selected_key)
                group_data["columns"] = column_keys
                groups[group_name] = group_data
                save_groups(groups)
                self._refresh_group(group_name, self._tables[group_name])

    def _remove_column(self, group_name: str, logical_index: int):
        """从指定分组中删除指定列"""
        groups = get_groups()
        group_data = groups.get(group_name, {"columns": []})
        column_keys = group_data.get("columns", [])
        col_idx = logical_index - 1
        if 0 <= col_idx < len(column_keys):
            del column_keys[col_idx]
            group_data["columns"] = column_keys
            groups[group_name] = group_data
            save_groups(groups)
            self._refresh_group(group_name, self._tables[group_name])

    def _set_column_field(self, group_name: str, logical_index: int, field_key: str):
        """设置指定分组的指定列字段"""
        groups = get_groups()
        group_data = groups.get(group_name, {"columns": []})
        column_keys = group_data.get("columns", [])
        col_idx = logical_index - 1
        if not (0 <= col_idx < len(column_keys)):
            return

        if field_key in column_keys and column_keys.index(field_key) != col_idx:
            QMessageBox.warning(self, "重复", f"Key '{field_key}' 已在该分组中显示")
            return

        column_keys[col_idx] = field_key
        group_data["columns"] = column_keys
        groups[group_name] = group_data
        save_groups(groups)
        self._refresh_group(group_name, self._tables[group_name])

    def _on_cell_double_clicked(self, row: int, col: int, group_name: str):
        """单元格双击：对有 cap 的列，剥离 /cap 后缀再进入编辑"""
        if col < 1 or self._loading:
            return

        from ..config import get_profile_config
        config = get_profile_config()
        groups = get_groups()
        column_keys = groups.get(group_name, {}).get("columns", [])
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

        table = self._tables.get(group_name)
        if not table:
            return
        item = table.item(row, col)
        if not item:
            return

        text = item.text()
        if "/" in text:
            value_part = text.split("/")[0].strip()
            self._editing_cap_cell = True
            item.setText(value_part)
            self._editing_cap_cell = False

    def _on_item_changed(self, item: QTableWidgetItem, group_name: str):
        """单元格编辑完成后回写到 profile 节点"""
        if self._loading or self._editing_cap_cell:
            return

        row = item.row()
        col = item.column()

        if col < 1:
            return

        from ..config import get_profile_config
        config = get_profile_config()

        groups = get_groups()
        column_keys = groups.get(group_name, {}).get("columns", [])
        field_idx = col - 1
        if field_idx >= len(column_keys):
            return

        key_str = column_keys[field_idx]
        kd = config.get_key(key_str)
        if not kd:
            return

        model_type = config.get_model_type(key_str) or ""

        table = self._tables.get(group_name)
        if not table:
            return

        name_item = table.item(row, 0)
        if not name_item:
            return
        user_name = name_item.text()

        raw_value = item.text()
        parsed_value = self._parse_value(raw_value, model_type, kd)
        if parsed_value is _PARSE_ERROR:
            self._loading = True
            user_data = self._load_user_data(user_name)
            text, style = self._format_profile_cell(kd, model_type, user_data)
            item.setText(text)
            self._apply_cell_style(item, style)
            self._loading = False
            return

        self._write_profile_entry(user_name, model_type, key_str, parsed_value)

        self._loading = True
        user_data = self._load_user_data(user_name)
        text, style = self._format_profile_cell(kd, model_type, user_data)
        item.setText(text)
        self._apply_cell_style(item, style)
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
                return float(raw) if raw else 0.0
            except ValueError:
                QMessageBox.warning(None, "输入错误", f"{kd.label} 必须是数字")
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

    def _on_cell_context_menu(self, pos, group_name: str, table: QTableWidget):
        """右键菜单：快速增减数值"""
        item = table.itemAt(pos)
        if not item:
            return

        row = item.row()
        col = item.column()
        if col < 1:  # 角色名列不处理
            return

        from ..config import get_profile_config
        config = get_profile_config()

        groups = get_groups()
        column_keys = groups.get(group_name, {}).get("columns", [])
        field_idx = col - 1
        if field_idx >= len(column_keys):
            return

        key_str = column_keys[field_idx]
        kd = config.get_key(key_str)
        if not kd:
            return

        model_type = config.get_model_type(key_str) or ""

        # 资源模型不支持增减
        if model_type == MODEL_RESOURCE:
            return

        name_item = table.item(row, 0)
        if not name_item:
            return
        user_name = name_item.text()

        # 获取当前值
        user_data = self._load_user_data(user_name)
        entry = user_data.get("profile", {}).get(model_type, {}).get(key_str, {})
        current_value = entry.get("value", 0)
        if current_value is None:
            current_value = 0

        # 构建菜单
        menu = QMenu(self)
        menu.setTitle(f"{kd.label} ({user_name})")

        # 增减选项
        steps = [1, 10, 100]
        for step in steps:
            # 增加
            action_up = menu.addAction(f"+{step}")
            if action_up:
                action_up.triggered.connect(
                    lambda checked, s=step: self._adjust_value(
                        user_name, model_type, key_str, kd, current_value, s
                    )
                )

        menu.addSeparator()

        for step in steps:
            # 减少
            action_down = menu.addAction(f"-{step}")
            if action_down:
                action_down.triggered.connect(
                    lambda checked, s=step: self._adjust_value(
                        user_name, model_type, key_str, kd, current_value, -s
                    )
                )

        # 自定义增减
        menu.addSeparator()
        action_custom = menu.addAction("自定义增减...")
        if action_custom:
            action_custom.triggered.connect(
                lambda: self._adjust_value_custom(
                    user_name, model_type, key_str, kd, current_value
                )
            )

        viewport = table.viewport()
        if viewport:
            menu.exec(viewport.mapToGlobal(pos))

    def _adjust_value(
        self,
        user_name: str,
        model_type: str,
        key: str,
        kd,
        current_value,
        delta: int | float,
    ):
        """增减数值并写回"""
        new_value = current_value + delta

        # 下限：0
        new_value = max(0, new_value)

        # 上限：cap
        cap = getattr(kd, "cap", None)
        if cap is not None:
            new_value = min(new_value, cap)

        self._write_profile_entry(user_name, model_type, key, new_value)

        # 刷新表格
        current_group = self._get_current_group_name()
        table = self._tables.get(current_group)
        if table:
            self._refresh_group(current_group, table)

    def _adjust_value_custom(
        self,
        user_name: str,
        model_type: str,
        key: str,
        kd,
        current_value,
    ):
        """自定义增减数值"""
        from PyQt6.QtWidgets import QInputDialog

        if model_type == MODEL_REALTIME:
            delta, ok = QInputDialog.getDouble(
                self,
                f"自定义增减 - {kd.label}",
                f"当前值: {current_value}\n输入增减量（正数增加，负数减少）:",
                0,
                -999999,
                999999,
                4,
            )
        else:
            delta, ok = QInputDialog.getInt(
                self,
                f"自定义增减 - {kd.label}",
                f"当前值: {int(current_value)}\n输入增减量（正数增加，负数减少）:",
                0,
                -999999,
                999999,
                1,
            )
        if not ok:
            return

        self._adjust_value(user_name, model_type, key, kd, current_value, delta)

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
            self._build_groups()

    # ─── 分组管理 ──────────────────────────────────────────────

    def _get_current_group_name(self) -> str:
        """获取当前 Tab 对应的分组名"""
        idx = self._tab_widget.currentIndex()
        if idx < 0:
            return ""
        return self._tab_widget.tabText(idx)

    def _add_group(self):
        """新建分组"""
        groups = get_groups()
        name, ok = QInputDialog.getText(self, "新建分组", "分组名称:")
        if not ok:
            return
        name = name.strip()
        if not name:
            QMessageBox.warning(self, "错误", "分组名不能为空")
            return
        if name in groups:
            QMessageBox.warning(self, "错误", f"分组 '{name}' 已存在")
            return

        groups[name] = {"columns": []}
        save_groups(groups)
        set_active_group(name)
        self._build_groups()

    def _rename_group(self):
        """重命名当前分组"""
        groups = get_groups()
        if not groups:
            return
        old_name = self._get_current_group_name()
        if not old_name:
            return

        new_name, ok = QInputDialog.getText(
            self, "重命名分组", "新名称:", text=old_name
        )
        if not ok:
            return
        new_name = new_name.strip()
        if not new_name:
            QMessageBox.warning(self, "错误", "分组名不能为空")
            return
        if new_name == old_name:
            return
        if new_name in groups:
            QMessageBox.warning(self, "错误", f"分组 '{new_name}' 已存在")
            return

        # 保持插入顺序：用新 key 替换旧 key
        new_groups = {}
        for k, v in groups.items():
            if k == old_name:
                new_groups[new_name] = v
            else:
                new_groups[k] = v
        save_groups(new_groups)
        # 同步更新列宽字典的 key
        all_widths = _get_column_widths()
        if old_name in all_widths:
            all_widths[new_name] = all_widths.pop(old_name)
            _save_column_widths(all_widths)
        if get_active_group() == old_name:
            set_active_group(new_name)
        self._build_groups()

    def _remove_group(self):
        """删除当前分组"""
        groups = get_groups()
        if not groups:
            return

        group_name = self._get_current_group_name()
        if not group_name:
            return

        if len(groups) <= 1:
            QMessageBox.warning(self, "错误", "至少保留一个分组")
            return

        reply = QMessageBox.question(
            self, "确认删除",
            f"确定要删除分组 '{group_name}' 吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        group_names = list(groups.keys())
        idx = group_names.index(group_name)
        del groups[group_name]
        save_groups(groups)

        # 切换到相邻分组
        new_names = list(groups.keys())
        new_idx = min(idx, len(new_names) - 1)
        set_active_group(new_names[new_idx])
        self._build_groups()

    def _on_tab_changed(self, index: int):
        """Tab 切换时记录活跃分组"""
        if 0 <= index < self._tab_widget.count():
            set_active_group(self._tab_widget.tabText(index))


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
                updated_at_str = entry.get("updated_at", "")
                computed, _ = _compute_realtime_value(
                    value, updated_at_str,
                    kd.regen_period, kd.regen_value, kd.cap,
                    kd.reset_time,
                )
                int_value = int(computed)
                period_labels = {"minute": "分钟", "hour": "小时", "day": "天"}
                period_text = period_labels.get(kd.regen_period, kd.regen_period)
                cap_text = f" / {kd.cap}" if kd.cap else ""
                return f"{int_value}{cap_text}  (回复: {kd.regen_value}/{period_text})"
            return str(int(value))

        if model_type == MODEL_RESOURCE:
            return str(value)

        if model_type == MODEL_ACTIVITY:
            if isinstance(kd, ActivityKeyDef) and kd.cap:
                return f"{value} / {kd.cap}  (周期: {kd.period})"
            if isinstance(value, bool):
                return "已完成" if value else "未完成"
            return str(value)

        return str(value)
