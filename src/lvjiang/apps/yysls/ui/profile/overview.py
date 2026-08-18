"""档案总览 Tab

ProfileOverviewTab: 宽表展示所有角色的概要信息，交互式列头配置。

数据来源：user.json 的 profile 节点
    profile:
      quota:
        key_name: { value: ..., updated_at: ... }
      regen:
        key_name: { value: ..., updated_at: ... }
      stock:
        key_name: { value: ..., updated_at: ... }
"""

from __future__ import annotations

import math

from loguru import logger
from PyQt6.QtCore import QObject, Qt, QTimer
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from lvjiang.core.config import get_session_store
from lvjiang.core.user_config import UserConfigManager

from .....i18n import tr
from ...config.profile_models import (
    ALL_MODELS,
    MODEL_LABELS,
    MODEL_NOTE,
    MODEL_QUOTA,
    MODEL_REGEN,
    MODEL_STOCK,
    KeyDef,
    QuotaKeyDef,
    RegenKeyDef,
    StepDef,
    StockKeyDef,
    format_sync_label,
)
from ...config.profile_store import (
    get_active_group,
    get_groups,
    save_groups,
    set_active_group,
)
from ...config.user_profile import (
    get_profile_config,
    save_profile_config,
)
from ...core.profile_engine.profile_db import db_read_all
from ...core.profile_engine.regen_math import (
    compute_regen_entry,
    is_realtime_regen,
)
from .cell_formatting import (
    apply_cell_style,
    format_cell_tooltip,
    format_profile_cell,
    is_sync_target_at_hard_cap,
)
from .dialogs import HistoryDialog, ask_value_dialog
from .tab import REFRESH_BTN_STYLE

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


def _make_debounce_timer(parent: QObject, callback, interval_ms: int = 500) -> QTimer:
    """创建一个单次触发的防抖定时器"""
    timer = QTimer(parent)
    timer.setSingleShot(True)
    timer.setInterval(interval_ms)
    timer.timeout.connect(callback)
    return timer


# 哨兵值：表示解析失败
_PARSE_ERROR = object()


def _is_continuous_regen(kd) -> bool:
    return isinstance(kd, RegenKeyDef) and is_realtime_regen(kd)


def _current_regen_value(entry: dict, kd: RegenKeyDef) -> float:
    return compute_regen_entry(entry, kd).value


# ─── 档案总览 Tab ────────────────────────────────────────────


class ProfileOverviewTab(QWidget):
    """档案总览 Tab - QTabWidget 分组展示"""

    def __init__(self, host, parent=None):
        super().__init__(parent)
        self._host = host
        self._tables: dict[str, QTableWidget] = {}
        self._loading = False
        self._editing_cap_cell = False
        self._restoring_widths = False
        self._reordering = False
        self._refresh_timer = _make_debounce_timer(self, self._refresh_when_idle)
        self._setup_ui()
        self._connect_profile_engine()

    def _connect_profile_engine(self) -> None:
        """让后台 profile 更新能刷新总览 UI。"""
        try:
            from ...core.profile_engine.profile_engine import get_or_create_engine
            engine = get_or_create_engine(self._host.user_manager, self._host.session_manager)
            engine.data_updated.connect(lambda _user_name: self._schedule_refresh())
        except Exception as e:
            logger.debug(f"ProfileOverviewTab 连接 ProfileEngine 失败: {e}")

    def _schedule_refresh(self) -> None:
        """合并后台批量更新，避免每个用户更新都刷新整张总览表。"""
        if not self._refresh_timer.isActive():
            self._refresh_timer.start()

    def _is_editing_cell(self) -> bool:
        """判断总览表是否有正在编辑的单元格。"""
        return any(
            table.state() == QAbstractItemView.State.EditingState
            for table in self._tables.values()
        )

    def _refresh_when_idle(self) -> None:
        """后台刷新只在单元格未编辑时执行，避免重建表格打断编辑。"""
        if self._is_editing_cell():
            self._refresh_timer.start()
            return
        self.refresh()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        # ── 工具栏：刷新 + 分组管理 ──
        toolbar = QHBoxLayout()

        btn_refresh = QPushButton(tr("刷新"))
        btn_refresh.setFixedWidth(60)
        btn_refresh.setToolTip(tr("重新读取角色数据"))
        btn_refresh.setStyleSheet(REFRESH_BTN_STYLE)
        btn_refresh.clicked.connect(self.refresh)
        toolbar.addWidget(btn_refresh)

        btn_add_group = QPushButton(tr("新建分组"))
        btn_add_group.setFixedWidth(70)
        btn_add_group.clicked.connect(self._add_group)
        toolbar.addWidget(btn_add_group)

        btn_rename_group = QPushButton(tr("重命名分组"))
        btn_rename_group.setFixedWidth(80)
        btn_rename_group.clicked.connect(self._rename_group)
        toolbar.addWidget(btn_rename_group)

        btn_remove_group = QPushButton(tr("删除分组"))
        btn_remove_group.setFixedWidth(70)
        btn_remove_group.clicked.connect(self._remove_group)
        toolbar.addWidget(btn_remove_group)

        toolbar.addStretch()
        layout.addLayout(toolbar)

        # ── 内容区：QTabWidget ──
        self._tab_widget = QTabWidget()
        self._tab_widget.currentChanged.connect(self._on_tab_changed)
        layout.addWidget(self._tab_widget, stretch=1)

        # ── 底部按钮行 ──
        btn_row = QHBoxLayout()
        btn_row.addStretch()

        btn_settings = QPushButton(tr("数据模型"))
        btn_settings.setFixedWidth(70)
        btn_settings.setToolTip(tr("定义数据模型 key"))
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
            groups = {tr("默认"): {"columns": []}}
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
        # 选中行保持轻量提示，当前编辑/移动焦点格使用更深的底色突出。
        table.setStyleSheet("""
            QTableWidget::item:selected {
                background-color: #e6e6e6;
                color: #202020;
            }
            QTableWidget::item:selected:focus {
                background-color: #666666;
                color: #ffffff;
            }
            QTableWidget::item:focus {
                outline: 1px solid #4a4a4a;
                outline-offset: -2px;
            }
        """)

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
        from PyQt6.QtGui import QFont

        from ...config import get_profile_config

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

        # 第一列是角色名，后面是数据列
        col_count = len(key_defs) + 1
        headers = [tr("角色名")] + [kd.label for kd in key_defs]

        users_data = self._load_all_users()

        self._loading = True
        # 先清空表格内容，再设置新的行列数
        # 列数不变时 setColumnCount 不会重置列的视觉顺序，拖拽列头后
        # visual != logical 会残留；先置 0 再重建，确保 visual == logical
        # == 持久化顺序，否则重建的表头会与拖拽位移叠加导致列错位
        table.setRowCount(0)
        table.setColumnCount(0)
        table.setColumnCount(col_count)
        table.setHorizontalHeaderLabels(headers)

        # 设置表头字体为粗体
        h_header = table.horizontalHeader()
        if h_header:
            bold_font = QFont(h_header.font())
            bold_font.setBold(True)
            for col in range(col_count):
                header_item = table.horizontalHeaderItem(col)
                if header_item:
                    header_item.setFont(bold_font)

        table.setRowCount(len(users_data))
        for row, (name, data) in enumerate(users_data.items()):
            name_item = QTableWidgetItem(name)
            name_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            name_item.setFlags(name_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            table.setItem(row, 0, name_item)
            for col, kd in enumerate(key_defs):
                model_type = config.get_model_type(kd.key) or ""

                # 检查 sync_targets 目标是否已达硬上限（按命名空间查对应模型）
                capped_targets = [
                    t for t in kd.sync_targets
                    if is_sync_target_at_hard_cap(t.key, data)
                ]
                sync_capped = len(capped_targets) > 0

                if sync_capped:
                    display_text = "—"
                    style = ""
                else:
                    display_text, style = format_profile_cell(kd, model_type, data)

                item = QTableWidgetItem(display_text)
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

                if sync_capped:
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                    capped_labels = [
                        format_sync_label(t.key) for t in capped_targets
                    ]
                    item.setToolTip(
                        f"{kd.label} 的同步目标 {', '.join(capped_labels)} 已达上限"
                    )
                else:
                    apply_cell_style(item, style)
                    # 设置悬停提示，显示元信息
                    tooltip = format_cell_tooltip(kd, model_type, data)
                    if tooltip:
                        item.setToolTip(tooltip)

                table.setItem(row, col + 1, item)

        header = table.horizontalHeader()
        assert header is not None
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setDefaultSectionSize(120)
        header.setStretchLastSection(False)
        self._loading = False
        self._restore_column_widths(group_name, table)

    # ─── 列管理 ──────────────────────────────────────────────

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
            # 读取新的视觉顺序，跳过第 0 列（角色名）
            new_order = []
            for visual_idx in range(h_header.count()):
                logical_idx = h_header.logicalIndex(visual_idx)
                # logical_idx 0 是角色名，数据列从 1 开始
                data_idx = logical_idx - 1
                if 0 <= data_idx < len(column_keys):
                    new_order.append(column_keys[data_idx])

            if column_keys != new_order:
                group_data["columns"] = new_order
                groups[group_name] = group_data
                save_groups(groups)
                self._refresh_group(group_name, table)
        finally:
            self._reordering = False

    def _on_column_resized(self, group_name: str, table: QTableWidget):
        """列宽拖拽调整后持久化，并同步角色名列宽到所有分组"""
        if self._restoring_widths or self._loading:
            return
        h_header = table.horizontalHeader()
        assert h_header is not None
        widths = [h_header.sectionSize(i) for i in range(h_header.count())]
        all_widths = _get_column_widths()
        all_widths[group_name] = widths

        # 同步角色名列宽（第 0 列）到所有其他分组，并更新持久化数据
        name_col_width = widths[0] if widths else 0
        if name_col_width > 0:
            for other_group, other_table in self._tables.items():
                if other_group == group_name:
                    continue
                other_header = other_table.horizontalHeader()
                if other_header is not None and other_header.count() > 0:
                    self._restoring_widths = True
                    other_header.resizeSection(0, name_col_width)
                    self._restoring_widths = False
                    # 同步更新持久化的列宽，如果没有记录则创建
                    other_widths = all_widths.get(other_group)
                    col_count = other_header.count()
                    if not other_widths or len(other_widths) != col_count:
                        # 创建默认列宽记录
                        other_widths = [other_header.sectionSize(i) for i in range(col_count)]
                        all_widths[other_group] = other_widths
                    other_widths[0] = name_col_width

        _save_column_widths(all_widths)

    def _restore_column_widths(self, group_name: str, table: QTableWidget):
        """恢复指定分组的列宽配置"""
        all_widths = _get_column_widths()
        widths = all_widths.get(group_name)
        if not widths:
            return
        h_header = table.horizontalHeader()
        assert h_header is not None
        col_count = h_header.count()
        if len(widths) != col_count:
            default_w = h_header.defaultSectionSize()
            widths = [*widths[:col_count], *([default_w] * max(0, col_count - len(widths)))]
            all_widths[group_name] = widths
            _save_column_widths(all_widths)
        self._restoring_widths = True
        for idx, w in enumerate(widths):
            h_header.resizeSection(idx, w)
        self._restoring_widths = False

    def _insert_column_width(self, group_name: str, data_insert_idx: int, table: QTableWidget) -> None:
        """新增数据列时同步列宽数组；第 0 列为角色名。"""
        h_header = table.horizontalHeader()
        assert h_header is not None
        all_widths = _get_column_widths()
        widths = list(all_widths.get(group_name) or [])
        if not widths:
            widths = [h_header.sectionSize(i) for i in range(h_header.count())]
        width_idx = max(1, min(data_insert_idx + 1, len(widths)))
        new_width = h_header.defaultSectionSize()
        widths.insert(width_idx, new_width)
        all_widths[group_name] = widths
        _save_column_widths(all_widths)

    def _remove_column_width(self, group_name: str, data_idx: int, table: QTableWidget) -> None:
        """删除数据列时同步列宽数组；第 0 列为角色名。"""
        h_header = table.horizontalHeader()
        assert h_header is not None
        all_widths = _get_column_widths()
        widths = list(all_widths.get(group_name) or [])
        if not widths:
            widths = [h_header.sectionSize(i) for i in range(h_header.count())]
        width_idx = data_idx + 1
        if 0 <= width_idx < len(widths):
            del widths[width_idx]
            all_widths[group_name] = widths
            _save_column_widths(all_widths)

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

        if logical_index == 0:
            menu.addAction(tr("右侧新增列"), lambda: self._add_column(group_name, -1))
        else:
            # 数据列索引需要减 1（跳过角色名列）
            data_index = logical_index - 1
            menu.addAction(tr("右侧新增列"), lambda: self._add_column(group_name, data_index))
            menu.addAction(tr("删除当前列"), lambda: self._remove_column(group_name, data_index))

        menu.exec(h_header.mapToGlobal(pos))

    def _on_header_double_clicked(self, logical_index: int, group_name: str):
        """表头双击：选择字段"""
        # 第 0 列是角色名，不可编辑
        if logical_index == 0:
            return

        from PyQt6.QtWidgets import QDialog

        from ...config import get_profile_config
        config = get_profile_config()
        all_keys = config.get_all_keys()

        if not all_keys:
            QMessageBox.information(self, tr("提示"), tr("没有可用的数据模型 key，请先在数据模型定义中添加"))
            return

        # 数据列索引需要减 1（跳过角色名列）
        data_index = logical_index - 1
        groups = get_groups()
        column_keys = groups.get(group_name, {}).get("columns", [])
        current_key = column_keys[data_index] if data_index < len(column_keys) else ""

        dialog = QDialog(self)
        dialog.setWindowTitle(tr("选择字段"))
        dialog.setMinimumWidth(250)
        layout = QVBoxLayout(dialog)

        selected = [current_key]  # 可变容器，供级联菜单回调
        btn = self._create_key_picker(config, all_keys, current_key, selected)
        layout.addWidget(btn)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_ok = QPushButton(tr("确定"))
        btn_ok.clicked.connect(dialog.accept)
        btn_row.addWidget(btn_ok)
        btn_cancel = QPushButton(tr("取消"))
        btn_cancel.clicked.connect(dialog.reject)
        btn_row.addWidget(btn_cancel)
        layout.addLayout(btn_row)

        if dialog.exec():
            if selected[0]:
                self._set_column_field(group_name, data_index, selected[0])

    def _add_column(self, group_name: str, after_index: int):
        """在指定分组的指定列后新增一列"""
        from PyQt6.QtWidgets import QDialog

        from ...config import get_profile_config
        config = get_profile_config()

        # 过滤掉该分组已有的 key
        groups = get_groups()
        group_data = groups.get(group_name, {"columns": []})
        used_keys = set(group_data.get("columns", []))
        all_keys = [kd for kd in config.get_all_keys() if kd.key not in used_keys]

        if not all_keys:
            QMessageBox.information(self, tr("提示"), tr("没有可用的数据模型 key，请先在数据模型定义中添加"))
            return

        dialog = QDialog(self)
        dialog.setWindowTitle(tr("选择字段"))
        dialog.setMinimumWidth(250)
        layout = QVBoxLayout(dialog)

        selected = [""]  # 可变容器，供级联菜单回调
        btn = self._create_key_picker(config, all_keys, "", selected)
        layout.addWidget(btn)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_ok = QPushButton(tr("确定"))
        btn_ok.clicked.connect(dialog.accept)
        btn_row.addWidget(btn_ok)
        btn_cancel = QPushButton(tr("取消"))
        btn_cancel.clicked.connect(dialog.reject)
        btn_row.addWidget(btn_cancel)
        layout.addLayout(btn_row)

        if dialog.exec():
            selected_key = selected[0]
            if selected_key:
                groups = get_groups()
                group_data = groups.get(group_name, {"columns": []})
                column_keys = group_data.get("columns", [])
                if selected_key in column_keys:
                    QMessageBox.warning(self, tr("重复"), tr("Key '{key}' 已在该分组中显示").format(key=selected_key))
                    return
                insert_idx = max(0, min(after_index + 1, len(column_keys)))
                column_keys.insert(insert_idx, selected_key)
                group_data["columns"] = column_keys
                groups[group_name] = group_data
                save_groups(groups)
                self._insert_column_width(group_name, insert_idx, self._tables[group_name])
                self._refresh_group(group_name, self._tables[group_name])

    def _remove_column(self, group_name: str, logical_index: int):
        """从指定分组中删除指定列"""
        groups = get_groups()
        group_data = groups.get(group_name, {"columns": []})
        column_keys = group_data.get("columns", [])
        if 0 <= logical_index < len(column_keys):
            del column_keys[logical_index]
            group_data["columns"] = column_keys
            groups[group_name] = group_data
            save_groups(groups)
            self._remove_column_width(group_name, logical_index, self._tables[group_name])
            self._refresh_group(group_name, self._tables[group_name])

    def _create_key_picker(
        self, config, all_keys: list, current_key: str, selected: list,
    ) -> QPushButton:
        """创建级联菜单 key 选择按钮

        一级菜单：模型类型（配额/再生/库存/备注）
        二级菜单：该类型下的具体 key

        selected: 可变容器 [key]，选中后更新 selected[0]。
        """
        from PyQt6.QtWidgets import QMenu

        def _label_for_key(key: str) -> str:
            kd = config.get_key(key)
            if kd:
                return f"{kd.label} ({kd.key})"
            return key

        btn = QPushButton(_label_for_key(current_key) if current_key else tr("（请选择）"))
        btn.setMinimumWidth(200)

        def show_menu():
            menu = QMenu(btn)
            # 按模型类型分组
            keys_by_model: dict[str, list] = {}
            for kd in all_keys:
                mt = config.get_model_type(kd.key) or ""
                keys_by_model.setdefault(mt, []).append(kd)

            for mt in ALL_MODELS:
                kds = keys_by_model.get(mt, [])
                if not kds:
                    continue
                model_label = MODEL_LABELS.get(mt, mt)
                submenu = menu.addMenu(model_label)
                for kd in kds:
                    action = submenu.addAction(f"{kd.label} ({kd.key})")
                    action.triggered.connect(
                        lambda checked, k=kd.key: (
                            selected.__setitem__(0, k),
                            btn.setText(_label_for_key(k)),
                        )
                    )

            menu.exec(btn.mapToGlobal(btn.rect().bottomLeft()))

        btn.clicked.connect(show_menu)
        return btn

    def _set_column_field(self, group_name: str, logical_index: int, field_key: str):
        """设置指定分组的指定列字段"""
        groups = get_groups()
        group_data = groups.get(group_name, {"columns": []})
        column_keys = group_data.get("columns", [])
        if not (0 <= logical_index < len(column_keys)):
            return

        if field_key in column_keys and column_keys.index(field_key) != logical_index:
            QMessageBox.warning(self, tr("重复"), tr("Key '{key}' 已在该分组中显示").format(key=field_key))
            return

        column_keys[logical_index] = field_key
        group_data["columns"] = column_keys
        groups[group_name] = group_data
        save_groups(groups)
        self._refresh_group(group_name, self._tables[group_name])

    # ─── 单元格事件与编辑 ──────────────────────────────────────

    def _on_cell_double_clicked(self, row: int, col: int, group_name: str):
        """单元格双击：对有 cap 的列，剥离 /cap 后缀再进入编辑"""
        if self._loading:
            return

        from ...config import get_profile_config
        config = get_profile_config()
        groups = get_groups()
        column_keys = groups.get(group_name, {}).get("columns", [])
        if col >= len(column_keys):
            return

        kd = config.get_key(column_keys[col])
        if not kd:
            return

        model_type = config.get_model_type(column_keys[col]) or ""
        has_cap = (
            (model_type == MODEL_REGEN and isinstance(kd, RegenKeyDef) and kd.cap is not None)
            or (model_type == MODEL_QUOTA and isinstance(kd, QuotaKeyDef) and kd.cap is not None)
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
        table = item.tableWidget()
        if not table:
            return

        from ...config import get_profile_config
        config = get_profile_config()

        groups = get_groups()
        column_keys = groups.get(group_name, {}).get("columns", [])
        # 第 0 列是角色名，数据列从 1 开始
        if col < 1 or col - 1 >= len(column_keys):
            return

        key_str = column_keys[col - 1]
        kd = config.get_key(key_str)
        if not kd:
            return

        model_type = config.get_model_type(key_str) or ""

        name_item = table.item(row, 0)
        if not name_item:
            return
        user_name = name_item.text()

        raw_value = item.text()

        # ── note 模型：直接写入文本，不走数值管线 ──
        if model_type == MODEL_NOTE:
            from ...core.profile_engine.profile_ops import profile_action
            try:
                profile_action(
                    user_name, key_str,
                    model_type=model_type,
                    set_value=raw_value,
                    source="",
                )
            except Exception as e:
                logger.error(f"note 写入失败: {e}")
                QMessageBox.warning(self, tr("保存失败"), tr("回写用户数据失败:\n{e}").format(e=e))
            return

        parsed_value = _parse_value(raw_value, model_type, kd)
        if parsed_value is _PARSE_ERROR:
            self._loading = True
            user_data = self._load_user_data(user_name)
            text, style = format_profile_cell(kd, model_type, user_data)
            item.setText(text)
            apply_cell_style(item, style)
            self._loading = False
            return

        # 读取当前值，计算 delta，走 action 路径（触发 sync）
        profile_data = db_read_all(user_name)
        entry = profile_data.get(model_type, {}).get(key_str, {})
        current_value = entry.get("value", 0) or 0
        if model_type == MODEL_REGEN and isinstance(kd, RegenKeyDef):
            current_value = _current_regen_value(entry, kd)

        delta = parsed_value - current_value
        force_target_write = (
            model_type == MODEL_REGEN
            and _is_continuous_regen(kd)
            and abs(parsed_value - math.floor(parsed_value)) > 1e-9
        )
        if delta == 0 and not force_target_write:
            return

        # Cell 编辑路径根据变动方向选择对应词表（增加→来源，减少→用途）
        if delta > 0:
            cell_source = kd.sources[0] if kd.sources else ""
        else:
            cell_source = kd.uses[0] if kd.uses else ""

        self._adjust_value(
            user_name, model_type, key_str, kd, current_value, delta,
            is_action=True, source=cell_source, expected_entry=dict(entry),
            regen_progress_source="target",
            force_write=force_target_write,
        )

    def _on_cell_context_menu(self, pos, group_name: str, table: QTableWidget):
        """右键菜单：快速增减数值"""
        from PyQt6.QtWidgets import QMenu

        item = table.itemAt(pos)
        if not item:
            return

        row = item.row()
        col = item.column()

        from ...config import get_profile_config
        config = get_profile_config()

        groups = get_groups()
        column_keys = groups.get(group_name, {}).get("columns", [])
        # 第 0 列是角色名，数据列从 1 开始
        if col < 1 or col - 1 >= len(column_keys):
            return

        key_str = column_keys[col - 1]
        kd = config.get_key(key_str)
        if not kd:
            return

        model_type = config.get_model_type(key_str) or ""

        name_item = table.item(row, 0)
        if not name_item:
            return
        user_name = name_item.text()

        # 获取当前值（从 DB 读取）
        profile_data = db_read_all(user_name)
        entry = profile_data.get(model_type, {}).get(key_str, {})
        current_value = entry.get("value", 0)
        if current_value is None:
            current_value = 0
        if model_type == MODEL_REGEN and isinstance(kd, RegenKeyDef):
            current_value = _current_regen_value(entry, kd)
        expected_entry = dict(entry)

        # 构建菜单
        menu = QMenu(self)
        menu.setTitle(f"{kd.label} ({user_name})")

        # note 模型：提供文本编辑选项
        if model_type == MODEL_NOTE:
            action_edit = menu.addAction(tr("编辑文本..."))
            if action_edit:
                current_text = expected_entry.get("value_text", "")
                action_edit.triggered.connect(
                    lambda: self._edit_note_text(
                        user_name, model_type, key_str, kd, current_text,
                        group_name, table,
                    )
                )
            # note 不记录 history，不提供"查看历史记录"菜单项
            viewport = table.viewport()
            if viewport:
                menu.exec(viewport.mapToGlobal(pos))
            return

        # 获取该字段的自定义 steps（Quota、Regen 和 Stock 模型支持）
        kd_steps: list[StepDef] = []
        kd_increment_only = False
        if model_type == MODEL_QUOTA and isinstance(kd, QuotaKeyDef):
            kd_steps = kd.steps
            kd_increment_only = kd.increment_only
        elif model_type == MODEL_REGEN and isinstance(kd, RegenKeyDef):
            kd_steps = kd.steps
        elif model_type == MODEL_STOCK and isinstance(kd, StockKeyDef):
            kd_steps = kd.steps

        if kd_steps:
            # 有自定义 steps：只展示用户定义的幅度，标签优先显示来源
            for step in kd_steps:
                if step.value > 0:
                    val_label = f"+{step.value}"
                elif step.value < 0:
                    val_label = str(step.value)
                else:
                    continue
                label = f"{step.source}({val_label})" if step.source else val_label
                action = menu.addAction(label)
                if action:
                    action.triggered.connect(
                        lambda checked, s=step: self._adjust_value(
                            user_name, model_type, key_str, kd, current_value, s.value,
                            is_action=True, source=s.source, expected_entry=expected_entry,
                        )
                    )
            menu.addSeparator()
            # 始终提供自定义输入入口
            action_inc = menu.addAction(tr("增加..."))
            if action_inc:
                action_inc.triggered.connect(
                    lambda: self._adjust_value_custom(
                        user_name, model_type, key_str, kd, current_value, direction=1,
                        expected_entry=expected_entry,
                    )
                )
            # 单向增加模式下不提供减少
            if not kd_increment_only:
                action_dec = menu.addAction(tr("减少..."))
                if action_dec:
                    action_dec.triggered.connect(
                        lambda: self._adjust_value_custom(
                            user_name, model_type, key_str, kd, current_value, direction=-1,
                            expected_entry=expected_entry,
                        )
                    )
        else:
            # 无自定义 steps：只提供自定义输入
            action_inc = menu.addAction(tr("增加..."))
            if action_inc:
                action_inc.triggered.connect(
                    lambda: self._adjust_value_custom(
                        user_name, model_type, key_str, kd, current_value, direction=1,
                        expected_entry=expected_entry,
                    )
                )
            # 单向增加模式下不提供减少
            if not kd_increment_only:
                action_dec = menu.addAction(tr("减少..."))
                if action_dec:
                    action_dec.triggered.connect(
                        lambda: self._adjust_value_custom(
                            user_name, model_type, key_str, kd, current_value, direction=-1,
                            expected_entry=expected_entry,
                        )
                    )

        # 覆写：直接设定值，不走 sync
        action_override = menu.addAction(tr("覆写..."))
        if action_override:
            action_override.triggered.connect(
                lambda: self._override_value_custom(
                    user_name, model_type, key_str, kd, current_value
                )
            )

        # 历史记录
        menu.addSeparator()
        action_history = menu.addAction(tr("查看历史记录"))
        if action_history:
            action_history.triggered.connect(
                lambda: self._show_history_dialog(user_name, model_type, key_str, kd.label)
            )

        viewport = table.viewport()
        if viewport:
            menu.exec(viewport.mapToGlobal(pos))

    # ─── 数据写入与增减 ──────────────────────────────────────

    def _adjust_value(
        self,
        user_name: str,
        model_type: str,
        key: str,
        kd,
        current_value,
        delta: int | float,
        is_action: bool = True,
        source: str = "",
        expected_entry: dict | None = None,
        use_cas: bool = True,
        regen_progress_source: str = "current",
        force_write: bool = False,
    ):
        """增减数值并写回。

        UI 只负责收集上下文、处理提示和刷新；写入语义统一委托 profile_ops.profile_action。
        """
        from ...core.profile_engine.profile_ops import (
            ProfileWriteConflict,
            profile_action,
        )
        try:
            profile_action(
                user_name, key,
                model_type=model_type,
                delta=delta,
                source=source,
                current_value=current_value,
                expected_entry=expected_entry,
                is_action=is_action,
                use_cas=use_cas,
                regen_progress_source=regen_progress_source,
                force_write=force_write,
            )
        except ProfileWriteConflict:
            logger.warning(f"{user_name} {model_type}.{key} CAS 失败，本次增减未写入")
            QMessageBox.warning(
                self, tr("写入冲突"),
                tr("该数值已被其他进程更新，本次增减未写入。请刷新后重试。"),
            )
            current_group = self._get_current_group_name()
            table = self._tables.get(current_group)
            if table:
                self._refresh_group(current_group, table)
            return
        except Exception as e:
            logger.error(f"回写失败: {e}")
            QMessageBox.warning(self, tr("保存失败"), tr("回写用户数据失败:\n{e}").format(e=e))
            return

        # 刷新表格
        current_group = self._get_current_group_name()
        table = self._tables.get(current_group)
        if table:
            self._refresh_group(current_group, table)

    def _register_new_source(self, kd: KeyDef, source: str, vocab: list[str]) -> None:
        """新词条自动追加到对应词表（来源/用途）并持久化到 profile.yaml

        保存与内存修改原子化：save 失败时回滚内存词表，避免"会话内可见、重启后丢失"。
        """
        if not source or source in vocab:
            return
        vocab.append(source)
        try:
            save_profile_config(get_profile_config())
        except Exception as e:
            try:
                vocab.remove(source)
            except ValueError:
                pass
            logger.warning(f"持久化新词条 '{source}' 失败: {e}")

    def _adjust_value_custom(
        self,
        user_name: str,
        model_type: str,
        key: str,
        kd,
        current_value,
        direction: int = 0,
        expected_entry: dict | None = None,
    ):
        """自定义增减数值（带来源/用途选择）

        direction: 1=增加（展示来源词表），-1=减少（展示用途词表），
            0=双向（两类叠加，来源在上）。
        """
        # 根据 direction 选择词表与标签
        if direction > 0:
            min_val = 0
            prompt = tr("增加量:")
            vocab = kd.sources
            vocab_label = tr("来源")
        elif direction < 0:
            min_val = 0
            prompt = tr("减少量:")
            vocab = kd.uses
            vocab_label = tr("用途")
        else:
            min_val = -999999
            prompt = tr("增减量（正增负减）:")
            vocab = kd.sources + [u for u in kd.uses if u not in kd.sources]
            vocab_label = tr("来源")

        if kd.decimal:
            current_text = f"{current_value:.4f}".rstrip("0").rstrip(".")
            is_float = True
        else:
            current_text = str(int(current_value))
            is_float = False

        value, source, _sync, ok = ask_value_dialog(
            self,
            title=tr("自定义增减 - {label}").format(label=kd.label),
            hint=tr("当前值: {val}").format(val=current_text),
            prompt=prompt,
            is_float=is_float,
            min_val=min_val,
            sources=vocab,
            source_label=vocab_label,
        )
        if not ok:
            return

        delta = float(value) if is_float else int(value)

        # 根据 direction 调整 delta 符号
        if direction > 0:
            delta = abs(delta)
        elif direction < 0:
            delta = -abs(delta)

        # 检查减少后是否小于 0
        new_value = current_value + delta
        if new_value < 0:
            QMessageBox.warning(
                None, tr("数值无效"),
                tr("减少后数值不能小于 0（当前值: {cur}，输入: {inp}）").format(cur=int(current_value), inp=int(abs(delta)))
            )
            return

        # 新词条归入实际变动方向对应的词表：增加→来源，减少→用途
        if delta > 0:
            self._register_new_source(kd, source, kd.sources)
        elif delta < 0:
            self._register_new_source(kd, source, kd.uses)

        # 自定义增减属于 action，触发 sync_targets 同步
        self._adjust_value(
            user_name, model_type, key, kd, current_value, delta,
            is_action=True, source=source, expected_entry=expected_entry,
        )

    def _override_value_custom(
        self,
        user_name: str,
        model_type: str,
        key: str,
        kd,
        current_value,
    ):
        """覆写（编辑语义）：输入目标值，计算 delta 走 CAS 写入。

        默认勾选「同步变更依赖方」→ 走 action 路径（触发 sync_targets 同步）。
        取消勾选 → 纯覆写语义（仅写本 key，不触发任何同步）。
        """
        if kd.decimal:
            current_text = f"{current_value:.4f}".rstrip("0").rstrip(".")
            is_float = True
        else:
            current_text = str(int(current_value))
            is_float = False

        value, source, sync_checked, ok = ask_value_dialog(
            self,
            title=tr("覆写 - {label}").format(label=kd.label),
            hint=tr("当前值: {val}").format(val=current_text),
            prompt=tr("新值:"),
            is_float=is_float,
            min_val=0,
            sources=kd.sources + [u for u in kd.uses if u not in kd.sources],
            initial_value=current_value,
            sync_checkbox=True,
            sync_default=True,
            source_label=tr("来源/用途"),
        )
        if not ok:
            return

        new_value = value
        delta = new_value - current_value
        force_target_write = (
            model_type == MODEL_REGEN
            and _is_continuous_regen(kd)
            and abs(new_value - math.floor(new_value)) > 1e-9
        )
        if delta == 0 and not force_target_write:
            return

        # 新词条归入实际变动方向对应的词表：增加→来源，减少→用途
        if delta > 0:
            self._register_new_source(kd, source, kd.sources)
        elif delta < 0:
            self._register_new_source(kd, source, kd.uses)

        # sync_checked=True: 走 action 路径（触发 sync_targets 同步）
        # sync_checked=False: 纯覆写语义（change_type="override"，不触发同步）
        self._adjust_value(
            user_name, model_type, key, kd, current_value, delta,
            is_action=sync_checked, source=source, use_cas=False,
            regen_progress_source="target",
            force_write=force_target_write,
        )

    def _edit_note_text(
        self,
        user_name: str,
        model_type: str,
        key: str,
        kd,
        current_text: str,
        group_name: str = "",
        table: QTableWidget | None = None,
    ) -> None:
        """弹出多行文本输入框编辑 note 文本"""
        from ...core.profile_engine.profile_ops import profile_action

        text, ok = QInputDialog.getMultiLineText(
            self,
            tr("编辑备注 - {label}").format(label=kd.label),
            tr("备注内容:"),
            current_text,
        )
        if not ok:
            return

        try:
            profile_action(
                user_name, key,
                model_type=model_type,
                set_value=text,
                source="",
            )
        except Exception as e:
            logger.error(f"note 写入失败: {e}")
            QMessageBox.warning(self, tr("保存失败"), tr("回写用户数据失败:\n{e}").format(e=e))
            return

        if not group_name:
            group_name = self._get_current_group_name()
            table = self._tables.get(group_name)
        if table:
            self._refresh_group(group_name, table)

    def _show_history_dialog(
        self, user_name: str, model_type: str, key: str, key_label: str,
    ) -> None:
        """打开历史记录查看器，展示指定 key 的最近变更记录"""
        dialog = HistoryDialog(user_name, model_type, key, key_label, self)
        dialog.exec()

    # ─── 用户数据加载 ──────────────────────────────────────

    def _load_all_users(self) -> dict[str, dict]:
        """加载所有用户 profile 数据（从 DB 读取）"""
        result: dict[str, dict] = {}

        user_mgr = UserConfigManager()
        ordered_names = user_mgr.list_users()

        for user_name in ordered_names:
            try:
                result[user_name] = db_read_all(user_name)
            except Exception as e:
                logger.warning(f"加载用户 {user_name} profile 失败: {e}")
                result[user_name] = {}

        return result

    def _load_user_data(self, user_name: str) -> dict:
        """加载单个用户 profile 数据（从 DB 读取）"""
        try:
            return db_read_all(user_name)
        except Exception as e:
            logger.warning(f"加载用户 {user_name} profile 失败: {e}")
            return {}

    def _open_metadata_dialog(self):
        """打开数据模型定义对话框"""
        from ...config import reload_profile_config
        from .dialogs import ProfileDefinitionDialog
        dialog = ProfileDefinitionDialog(self)
        if dialog.exec():
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
        name, ok = QInputDialog.getText(self, tr("新建分组"), tr("分组名称:"))
        if not ok:
            return
        name = name.strip()
        if not name:
            QMessageBox.warning(self, tr("错误"), tr("分组名不能为空"))
            return
        if name in groups:
            QMessageBox.warning(self, tr("错误"), tr("分组 '{name}' 已存在").format(name=name))
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
            self, tr("重命名分组"), tr("新名称:"), text=old_name
        )
        if not ok:
            return
        new_name = new_name.strip()
        if not new_name:
            QMessageBox.warning(self, tr("错误"), tr("分组名不能为空"))
            return
        if new_name == old_name:
            return
        if new_name in groups:
            QMessageBox.warning(self, tr("错误"), tr("分组 '{name}' 已存在").format(name=new_name))
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
            QMessageBox.warning(self, tr("错误"), tr("至少保留一个分组"))
            return

        reply = QMessageBox.question(
            self, tr("确认删除"),
            tr("确定要删除分组 '{name}' 吗？").format(name=group_name),
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


# ─── 值解析（模块级） ────────────────────────────────────────────


def _parse_value(raw: str, model_type: str, kd: KeyDef):
    """解析用户输入值，返回解析后的值或 _PARSE_ERROR"""
    # decimal 类型的 key 统一走 float 解析
    if kd.decimal:
        try:
            return float(raw) if raw else 0.0
        except ValueError:
            QMessageBox.warning(None, tr("输入错误"), f"{kd.label} 必须是数字")
            return _PARSE_ERROR

    if model_type == MODEL_QUOTA:
        # quota 可以是 int 或 bool（如 shop_of_week）
        if isinstance(kd, QuotaKeyDef) and kd.cap is not None:
            try:
                return int(raw) if raw else 0
            except ValueError:
                QMessageBox.warning(None, tr("输入错误"), tr("{label} 必须是整数").format(label=kd.label))
                return _PARSE_ERROR
        # 无 cap 的 quota 可能是 bool
        upper = raw.upper()
        if upper in ("Y", "TRUE", "1", tr("是"), "YES"):
            return True
        if upper in ("", "N", "FALSE", "0", tr("否"), "NO"):
            return False
        try:
            return int(raw)
        except ValueError:
            return raw

    if model_type == MODEL_REGEN:
        try:
            return int(raw) if raw else 0
        except ValueError:
            QMessageBox.warning(None, tr("输入错误"), tr("{label} 必须是整数").format(label=kd.label))
            return _PARSE_ERROR

    if model_type == MODEL_STOCK:
        try:
            return int(raw) if raw else 0
        except ValueError:
            QMessageBox.warning(None, tr("输入错误"), tr("{label} 必须是整数").format(label=kd.label))
            return _PARSE_ERROR

    return raw
