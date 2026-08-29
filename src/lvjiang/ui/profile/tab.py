"""档案总览 Tab

ProfileTab: 宽表展示所有用户的概要信息，交互式列头配置。

数据来源：profile.db（按 username 隔离）
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

from ...core.profile.models import format_sync_label
from ...core.profile.repository import db_read_all
from ...core.profile.store import (
    create_overview_group,
    get_active_group,
    get_groups,
    remove_overview_group,
    rename_overview_group,
    set_active_group,
)
from ...i18n import tr
from ..user_toolbar import USER_ACTION_BTN_STYLE, add_user_toolbar_refresh_button
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


def _make_debounce_timer(parent: QObject, callback, interval_ms: int = 500) -> QTimer:
    """创建一个单次触发的防抖定时器"""
    timer = QTimer(parent)
    timer.setSingleShot(True)
    timer.setInterval(interval_ms)
    timer.timeout.connect(callback)
    return timer


# ─── 档案总览 Tab ────────────────────────────────────────────


class ProfileTab(ProfileColumnMixin, ProfileCellEditingMixin, QWidget):
    """档案总览 Tab - QTabWidget 分组展示"""

    def __init__(self, host, parent=None):
        super().__init__(parent)
        self._host = host
        self._tables: dict[str, QTableWidget] = {}
        # UI 只展示当前 schema 中可解析的列，但持久化配置必须原样保留。
        # profile.yaml 暂时缺失/尚未加载时，刷新不能把用户配置删空。
        self._visible_column_keys: dict[str, list[str]] = {}
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
            from ...core.profile.engine import get_or_create_engine
            engine = get_or_create_engine(self._host.user_manager)
            engine.data_updated.connect(lambda _user_name: self._schedule_refresh())
        except Exception as e:
            logger.debug(f"ProfileTab 连接 ProfileEngine 失败: {e}")

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
            refresh_tooltip=tr("重新读取用户数据"),
        )
        toolbar.addSpacing(24)

        btn_add_group = QPushButton(tr("新建分组"))
        btn_add_group.setStyleSheet(USER_ACTION_BTN_STYLE)
        btn_add_group.setMinimumHeight(32)
        btn_add_group.clicked.connect(self._add_group)
        toolbar.addWidget(btn_add_group)

        btn_rename_group = QPushButton(tr("重命名分组"))
        btn_rename_group.setStyleSheet(USER_ACTION_BTN_STYLE)
        btn_rename_group.setMinimumHeight(32)
        btn_rename_group.clicked.connect(self._rename_group)
        toolbar.addWidget(btn_rename_group)

        btn_remove_group = QPushButton(tr("删除分组"))
        btn_remove_group.setStyleSheet(USER_ACTION_BTN_STYLE)
        btn_remove_group.setMinimumHeight(32)
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
        # 与同页工具栏的新建/重命名/删除分组同一套样式，别再是系统默认灰按钮
        btn_settings.setStyleSheet(USER_ACTION_BTN_STYLE)
        btn_settings.setMinimumHeight(32)
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
        self._visible_column_keys.clear()

        groups = get_groups()
        if not groups:
            # 首次展示的默认分组只存在于 UI；加载路径不得写 overview_groups。
            groups = {tr("默认"): {"columns": []}}

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
                background-color: palette(midlight);
                color: palette(text);
            }
            QTableWidget::item:selected:focus {
                background-color: palette(highlight);
                color: palette(highlighted-text);
            }
            QTableWidget::item:focus {
                outline: 1px solid palette(mid);
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

        from ...core.profile import get_profile_config

        config = get_profile_config()
        groups = get_groups()
        group_data = groups.get(group_name, {})
        column_keys = group_data.get("columns", [])

        # schema 中暂时不存在的 key 只在本次渲染中隐藏，不能反向清理配置。
        # schema 文件可能在启动、同步或编辑期间短暂不可用；若在刷新路径保存
        # valid_keys，会把所有分组永久写成 columns: []。
        column_keys = [k for k in column_keys if config.get_key(k)]
        self._visible_column_keys[group_name] = list(column_keys)

        key_defs = [kd for kd in (config.get_key(k) for k in column_keys) if kd is not None]

        # 第一列是用户名，后面是数据列
        col_count = len(key_defs) + 1
        headers = [tr("用户名")] + [kd.label for kd in key_defs]

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

        ordered_names = self._host.user_manager.list_users()

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
        from ...core.profile import reload_profile_config
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
        stored_groups = get_groups()
        groups = stored_groups or {tr("默认"): {"columns": []}}
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

        # 空配置时“默认”只存在于 UI；用户明确新增分组后再一并落盘，
        # 保持“新增”是增加而不是替换当前分组。
        if not stored_groups:
            create_overview_group(tr("默认"))
        create_overview_group(name)
        set_active_group(name)
        self._build_groups()

    def _rename_group(self):
        """重命名当前分组"""
        stored_groups = get_groups()
        groups = stored_groups or {tr("默认"): {"columns": []}}
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

        if stored_groups:
            rename_overview_group(old_name, new_name)
        else:
            # 用户明确重命名临时默认分组，此时直接以新名称首次落盘。
            create_overview_group(new_name)
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
        groups = get_groups() or {tr("默认"): {"columns": []}}

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
        remove_overview_group(group_name)

        # 切换到相邻分组
        new_names = [name for name in groups if name != group_name]
        new_idx = min(idx, len(new_names) - 1)
        set_active_group(new_names[new_idx])
        self._build_groups()

    def _on_tab_changed(self, index: int):
        """Tab 切换时记录活跃分组"""
        if 0 <= index < self._tab_widget.count():
            set_active_group(self._tab_widget.tabText(index))
