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

from lvjiang.core.user_config import UserConfigManager

from .....i18n import tr
from ...config.profile_models import format_sync_label
from ...config.profile_store import (
    get_active_group,
    get_groups,
    save_groups,
    set_active_group,
)
from ...core.profile_engine.profile_db import db_read_all
from .cell_editing import ProfileCellEditingMixin
from .cell_formatting import (
    apply_cell_style,
    format_cell_tooltip,
    format_profile_cell,
    is_sync_target_at_hard_cap,
)
from .column_management import (
    ProfileColumnMixin,
    _get_column_widths,
    _save_column_widths,
)
from .tab import add_user_toolbar_refresh_button


def _make_debounce_timer(parent: QObject, callback, interval_ms: int = 500) -> QTimer:
    """创建一个单次触发的防抖定时器"""
    timer = QTimer(parent)
    timer.setSingleShot(True)
    timer.setInterval(interval_ms)
    timer.timeout.connect(callback)
    return timer


# ─── 档案总览 Tab ────────────────────────────────────────────


class ProfileOverviewTab(ProfileColumnMixin, ProfileCellEditingMixin, QWidget):
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

        add_user_toolbar_refresh_button(
            toolbar,
            self.refresh,
            refresh_tooltip=tr("重新读取角色数据"),
        )

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
