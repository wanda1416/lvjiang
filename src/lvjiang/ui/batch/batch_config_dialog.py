"""批量配置对话框 — 管理多个命名配置

工具菜单「批量配置」打开此对话框。
每个配置包含：名字 + 用户自定义列的 table + 生命周期 wf 槽位。
列定义直接由表头维护：右键增删，双击重命名。
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from ...core.batch_config import (
    BatchConfigItem,
    BatchWorkflows,
    config_enabled_flags,
    load_batch_config,
    load_enabled_rows,
    save_batch_config,
    save_enabled_rows,
)
from ...i18n import tr
from ..button_styles import apply_button_style

# 行勾选状态挂在第 0 列 item 上，随行搬运。
_ENABLED_ROLE = Qt.ItemDataRole.UserRole


class BatchConfigDialog(QDialog):
    """批量配置对话框"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("批量配置"))
        self.setMinimumSize(600, 500)
        self._cfg = load_batch_config()
        self._current_name: str = ""  # 当前编辑的配置名
        self._setup_ui()
        self._refresh_config_list()

    # ─── UI 构建 ─────────────────────────────────────────

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(8)

        # ── 配置选择行 ──
        config_row = QHBoxLayout()
        config_row.addWidget(QLabel(tr("配置：")))
        self._config_combo = QComboBox()
        self._config_combo.setMinimumWidth(150)
        self._config_combo.currentIndexChanged.connect(self._on_config_selected)
        config_row.addWidget(self._config_combo, stretch=1)

        self._btn_new = QPushButton(tr("新建"))
        self._btn_new.setFixedWidth(60)
        self._btn_new.clicked.connect(self._on_new_config)
        config_row.addWidget(self._btn_new)

        self._btn_delete = QPushButton(tr("删除"))
        self._btn_delete.setFixedWidth(60)
        self._btn_delete.clicked.connect(self._on_delete_config)
        config_row.addWidget(self._btn_delete)
        apply_button_style(self._btn_new)
        apply_button_style(self._btn_delete, variant="danger")

        layout.addLayout(config_row)

        # ── 用户名列 ──
        user_col_row = QHBoxLayout()
        user_col_row.addWidget(QLabel(tr("用户名列：")))
        self._user_column_combo = QComboBox()
        self._user_column_combo.setMinimumWidth(160)
        user_col_row.addWidget(self._user_column_combo)
        user_col_row.addStretch()
        user_col_row.addWidget(QLabel(tr("右键表头增删列，双击表头重命名")))
        layout.addLayout(user_col_row)

        # ── 条目表 ──
        self._table = QTableWidget(0, 0)
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self._table.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked
            | QAbstractItemView.EditTrigger.EditKeyPressed
        )
        self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._on_context_menu)
        header = self._table.horizontalHeader()
        header.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        header.customContextMenuRequested.connect(self._on_header_context_menu)
        header.sectionDoubleClicked.connect(self._on_header_double_clicked)
        layout.addWidget(self._table, stretch=1)

        # ── 表格操作行 ──
        table_btn_row = QHBoxLayout()
        self._btn_add_row = QPushButton(tr("添加行"))
        self._btn_add_row.clicked.connect(self._on_add_row)
        table_btn_row.addWidget(self._btn_add_row)

        self._btn_del_row = QPushButton(tr("删除行"))
        self._btn_del_row.clicked.connect(self._on_delete_row)
        table_btn_row.addWidget(self._btn_del_row)

        table_btn_row.addStretch()

        self._btn_up = QPushButton(tr("↑ 上移"))
        self._btn_up.setFixedWidth(72)
        self._btn_up.clicked.connect(lambda: self._move_row(-1))
        table_btn_row.addWidget(self._btn_up)

        self._btn_down = QPushButton(tr("↓ 下移"))
        self._btn_down.setFixedWidth(72)
        self._btn_down.clicked.connect(lambda: self._move_row(1))
        table_btn_row.addWidget(self._btn_down)
        apply_button_style(self._btn_add_row)
        apply_button_style(self._btn_del_row, variant="danger")
        apply_button_style(self._btn_up, self._btn_down, variant="neutral")

        layout.addLayout(table_btn_row)

        # ── 生命周期 wf 槽位 ──
        wf_form = QFormLayout()
        wf_form.setContentsMargins(0, 4, 0, 0)

        self._wf_batch_setup = self._create_wf_selector()
        wf_form.addRow(tr("批次初始化 wf："), self._wf_batch_setup)

        self._wf_prepare_item = self._create_wf_selector()
        wf_form.addRow(tr("条目准备 wf："), self._wf_prepare_item)

        self._wf_finish_item = self._create_wf_selector()
        wf_form.addRow(tr("条目收尾 wf："), self._wf_finish_item)

        self._wf_batch_teardown = self._create_wf_selector()
        wf_form.addRow(tr("批次收尾 wf："), self._wf_batch_teardown)

        self._skip_single_lifecycle = QCheckBox(
            tr("单条目执行时跳过上述生命周期操作")
        )
        self._skip_single_lifecycle.setToolTip(
            tr("仅启用一个条目时，直接执行所选脚本，不运行四个生命周期 wf")
        )
        wf_form.addRow("", self._skip_single_lifecycle)

        layout.addLayout(wf_form)

        # ── 底部按钮 ──
        bottom_row = QHBoxLayout()
        bottom_row.addStretch()
        self._btn_save = QPushButton(tr("保存"))
        self._btn_save.setDefault(True)
        self._btn_save.clicked.connect(self._on_save)
        bottom_row.addWidget(self._btn_save)
        self._btn_cancel = QPushButton(tr("取消"))
        self._btn_cancel.clicked.connect(self.reject)
        bottom_row.addWidget(self._btn_cancel)
        apply_button_style(self._btn_save)
        apply_button_style(self._btn_cancel, variant="neutral")
        layout.addLayout(bottom_row)

    def _create_wf_selector(self) -> QHBoxLayout:
        """创建 wf 文件选择器（输入框 + 浏览按钮）"""
        row = QHBoxLayout()
        line_edit = QLineEdit()
        line_edit.setPlaceholderText(tr("选择 wf 文件..."))
        row.addWidget(line_edit, stretch=1)

        btn = QPushButton(tr("浏览..."))
        btn.setFixedWidth(70)
        apply_button_style(btn, variant="neutral")
        btn.clicked.connect(lambda: self._browse_wf(line_edit))
        row.addWidget(btn)

        # 把 line_edit 附加到 row 上，方便后续访问
        row._line_edit = line_edit  # type: ignore
        return row

    def _browse_wf(self, line_edit: QLineEdit):
        """浏览选择 wf 文件"""
        from ...core.config import get_resolver
        workflows_dir = get_resolver().system_dir / "workflows"
        start_dir = str(workflows_dir) if workflows_dir.exists() else ""
        path, _ = QFileDialog.getOpenFileName(
            self, tr("选择工作流文件"), start_dir,
            tr("工作流文件 (*.wf);;所有文件 (*)")
        )
        if path:
            # 转换为相对于 workflows 目录的路径
            try:
                from pathlib import Path
                rel = Path(path).relative_to(workflows_dir)
                line_edit.setText(str(rel).replace("\\", "/"))
            except ValueError:
                # 不在 workflows 目录下，使用绝对路径
                line_edit.setText(path)

    def _get_wf_text(self, selector: QHBoxLayout) -> str:
        """获取 wf 选择器中的文本"""
        return selector._line_edit.text().strip()  # type: ignore

    def _set_wf_text(self, selector: QHBoxLayout, text: str):
        """设置 wf 选择器中的文本"""
        selector._line_edit.setText(text)  # type: ignore

    # ─── 配置管理 ─────────────────────────────────────────

    def _refresh_config_list(self):
        """刷新配置下拉列表"""
        self._config_combo.blockSignals(True)
        self._config_combo.clear()
        for name in self._cfg.configs:
            self._config_combo.addItem(name)

        # 选中当前配置
        if self._cfg.active_config and self._cfg.active_config in self._cfg.configs:
            idx = self._config_combo.findText(self._cfg.active_config)
            if idx >= 0:
                self._config_combo.setCurrentIndex(idx)
        self._config_combo.blockSignals(False)

        # 加载当前配置到编辑区
        if self._config_combo.count() > 0:
            self._load_config_to_editor(self._config_combo.currentText())
        else:
            self._clear_editor()

    def _load_config_to_editor(self, name: str):
        """将指定配置加载到编辑区"""
        self._current_name = name
        item = self._cfg.configs.get(name)
        if not item:
            self._clear_editor()
            return

        columns = self._normalized_editor_columns(item.columns, item.user_column)

        # 表格：先清空，再创建行
        self._table.setColumnCount(len(columns))
        self._table.setHorizontalHeaderLabels(columns)
        self._sync_user_column_combo(
            item.user_column if item.user_column in columns else columns[0]
        )
        self._table.setRowCount(0)

        # 行勾选状态挂在第 0 列 item 的 UserRole 上：增删/上下移都是整
        # item 搬运，状态自然跟着行走。它过去是一个按行号索引的独立数组，
        # 这里一动就会张冠李戴——禁用的账号照跑，启用的反而被跳过。
        flags = config_enabled_flags(item)
        for row_idx, row_data in enumerate(item.rows):
            self._table.insertRow(row_idx)
            for col_idx, col_name in enumerate(columns):
                val = str(row_data.get(col_name, ""))
                cell = QTableWidgetItem(val)
                if col_idx == 0:
                    cell.setData(_ENABLED_ROLE, flags[row_idx])
                self._table.setItem(row_idx, col_idx, cell)

        # wf 槽位
        self._set_wf_text(self._wf_batch_setup, item.workflows.batch_setup)
        self._set_wf_text(self._wf_prepare_item, item.workflows.prepare_item)
        self._set_wf_text(self._wf_finish_item, item.workflows.finish_item)
        self._set_wf_text(self._wf_batch_teardown, item.workflows.batch_teardown)
        self._skip_single_lifecycle.setChecked(
            item.skip_lifecycle_for_single_item
        )

    def _clear_editor(self):
        """清空编辑区"""
        self._current_name = ""
        self._table.setRowCount(0)
        self._table.setColumnCount(1)
        self._table.setHorizontalHeaderLabels(["user"])
        self._sync_user_column_combo("user")
        self._set_wf_text(self._wf_batch_setup, "")
        self._set_wf_text(self._wf_prepare_item, "")
        self._set_wf_text(self._wf_finish_item, "")
        self._set_wf_text(self._wf_batch_teardown, "")
        self._skip_single_lifecycle.setChecked(True)

    def _on_config_selected(self, index: int):
        """配置下拉框切换"""
        if index < 0:
            return
        name = self._config_combo.itemText(index)
        if self._current_name and self._current_name != name:
            self._save_current_config()
        self._load_config_to_editor(name)

    def _on_new_config(self):
        """新建配置"""
        name, ok = QInputDialog.getText(self, tr("新建配置"), tr("配置名称："))
        if not ok or not name.strip():
            return
        name = name.strip()
        if name in self._cfg.configs:
            QMessageBox.warning(self, tr("重复"), f"配置 {name!r} 已存在")
            return

        self._save_current_config()

        item = BatchConfigItem(
            name=name,
            columns=["user"],
            rows=[],
            workflows=BatchWorkflows(),
            user_column="user",
            skip_lifecycle_for_single_item=True,
        )
        self._cfg.configs[name] = item
        self._cfg.active_config = name
        self._refresh_config_list()

    def _on_delete_config(self):
        """删除当前配置"""
        if not self._current_name:
            return
        reply = QMessageBox.question(
            self, tr("确认删除"),
            f"确定删除配置 {self._current_name!r}？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        del self._cfg.configs[self._current_name]
        if self._cfg.active_config == self._current_name:
            self._cfg.active_config = next(iter(self._cfg.configs), "")
        self._refresh_config_list()

    # ─── 列定义 ──────────────────────────────────────────

    @staticmethod
    def _normalized_editor_columns(
        columns: list[str], user_column: str,
    ) -> list[str]:
        """清理旧配置列；仅对真正的零列异常补一个可编辑入口。"""
        normalized: list[str] = []
        for value in columns:
            name = str(value).strip()
            if name and name not in normalized:
                normalized.append(name)
        if not normalized:
            normalized.append(user_column.strip() or "user")
        return normalized

    def _defined_columns(self) -> list[str]:
        """以表头为列 schema 的唯一来源。"""
        columns: list[str] = []
        for index in range(self._table.columnCount()):
            item = self._table.horizontalHeaderItem(index)
            columns.append(item.text().strip() if item else "")
        return columns

    def _sync_user_column_combo(self, preferred: str | None = None) -> None:
        """列增删改名后实时同步用户名列候选。"""
        columns = [name for name in self._defined_columns() if name]
        current = preferred or self._user_column_combo.currentText()
        self._user_column_combo.blockSignals(True)
        self._user_column_combo.clear()
        self._user_column_combo.addItems(columns)
        target = current if current in columns else columns[0]
        self._user_column_combo.setCurrentText(target)
        self._user_column_combo.blockSignals(False)

    def _add_column(self, after_index: int) -> None:
        """询问列名，并在指定表头右侧插入空列。"""
        name, ok = QInputDialog.getText(
            self, tr("新增列"), tr("列名：")
        )
        if not ok or not name.strip():
            return
        name = name.strip()
        if name in self._defined_columns():
            QMessageBox.warning(
                self, tr("重复"),
                tr("列名 '{name}' 已存在").format(name=name),
            )
            return

        insert_at = max(0, min(after_index + 1, self._table.columnCount()))
        self._table.insertColumn(insert_at)
        self._table.setHorizontalHeaderItem(insert_at, QTableWidgetItem(name))
        for row in range(self._table.rowCount()):
            self._table.setItem(row, insert_at, QTableWidgetItem(""))
        self._sync_user_column_combo()
        if self._table.rowCount() > 0:
            self._table.setCurrentCell(0, insert_at)

    def _is_username_column(self, index: int) -> bool:
        columns = self._defined_columns()
        return (
            0 <= index < len(columns)
            and columns[index] == self._user_column_combo.currentText()
        )

    def _remove_column(self, index: int) -> None:
        """删除非用户名列；用户名列作为最后编辑入口始终保留。"""
        if not (0 <= index < self._table.columnCount()):
            return
        if self._is_username_column(index):
            return
        self._table.removeColumn(index)
        self._sync_user_column_combo()

    def _rename_column(self, index: int, new_name: str) -> bool:
        """重命名列并保持用户名列指向与单元格数据不变。"""
        columns = self._defined_columns()
        if not (0 <= index < len(columns)):
            return False
        old_name = columns[index]
        new_name = new_name.strip()
        if not new_name:
            QMessageBox.warning(self, tr("错误"), tr("列名不能为空"))
            return False
        if new_name != old_name and new_name in columns:
            QMessageBox.warning(
                self, tr("重复"),
                tr("列名 '{name}' 已存在").format(name=new_name),
            )
            return False
        if new_name == old_name:
            return True
        username = self._user_column_combo.currentText()
        self._table.setHorizontalHeaderItem(index, QTableWidgetItem(new_name))
        self._sync_user_column_combo(
            new_name if username == old_name else username
        )
        return True

    def _on_header_context_menu(self, pos) -> None:
        header = self._table.horizontalHeader()
        index = header.logicalIndexAt(pos)
        after_index = index if index >= 0 else self._table.columnCount() - 1
        menu = QMenu(header)
        menu.addAction(tr("右侧新增列"), lambda: self._add_column(after_index))
        if index >= 0:
            delete_action = menu.addAction(
                tr("删除当前列"), lambda: self._remove_column(index)
            )
            if delete_action is not None:
                delete_action.setEnabled(not self._is_username_column(index))
        menu.exec(header.mapToGlobal(pos))

    def _on_header_double_clicked(self, index: int) -> None:
        columns = self._defined_columns()
        if not (0 <= index < len(columns)):
            return
        name, ok = QInputDialog.getText(
            self, tr("重命名列"), tr("列名："), text=columns[index]
        )
        if ok:
            self._rename_column(index, name)

    # ─── 表格操作 ─────────────────────────────────────────

    def _read_table_rows(self) -> list[dict]:
        """从表格读取所有行数据"""
        columns = self._defined_columns()
        rows = []
        for row_idx in range(self._table.rowCount()):
            row_data = {}
            for col_idx, col_name in enumerate(columns):
                item = self._table.item(row_idx, col_idx)
                row_data[col_name] = item.text().strip() if item else ""
            rows.append(row_data)
        return rows

    def _persist_enabled_flags(self, config_name: str) -> None:
        """按表格当前行序写回勾选状态；新行默认启用。"""
        flags: list[bool] = []
        for row_idx in range(self._table.rowCount()):
            cell = self._table.item(row_idx, 0)
            stored = cell.data(_ENABLED_ROLE) if cell else None
            flags.append(True if stored is None else bool(stored))
        enabled = load_enabled_rows()
        enabled[config_name] = flags
        save_enabled_rows(enabled)

    def _on_add_row(self):
        """添加空行"""
        row = self._table.rowCount()
        self._table.insertRow(row)
        for col in range(self._table.columnCount()):
            self._table.setItem(row, col, QTableWidgetItem(""))
        self._table.scrollToBottom()
        self._table.selectRow(row)

    def _on_delete_row(self):
        """删除选中行"""
        row = self._table.currentRow()
        if row < 0:
            return
        self._table.removeRow(row)

    def _on_context_menu(self, pos):
        """右键菜单：复制为新行"""
        row = self._table.rowAt(pos.y())
        if row < 0:
            return
        ctx_menu = QMenu(self._table)
        copy_action = QAction(tr("复制为新行"), self)
        copy_action.triggered.connect(lambda: self._copy_row(row))
        ctx_menu.addAction(copy_action)
        ctx_menu.exec(self._table.viewport().mapToGlobal(pos))

    def _copy_row(self, src_row: int):
        """复制指定行为新行"""
        insert_at = src_row + 1
        self._table.insertRow(insert_at)
        for col in range(self._table.columnCount()):
            item = self._table.item(src_row, col)
            text = item.text() if item else ""
            self._table.setItem(insert_at, col, QTableWidgetItem(text))
        self._table.selectRow(insert_at)

    def _move_row(self, delta: int):
        """上移/下移选中行"""
        row = self._table.currentRow()
        if row < 0:
            return
        new_row = row + delta
        if new_row < 0 or new_row >= self._table.rowCount():
            return
        for col in range(self._table.columnCount()):
            item_a = self._table.takeItem(row, col)
            item_b = self._table.takeItem(new_row, col)
            self._table.setItem(row, col, item_b)
            self._table.setItem(new_row, col, item_a)
        self._table.selectRow(new_row)

    # ─── 保存 ────────────────────────────────────────────

    def _on_save(self):
        """保存当前配置"""
        # 先保存当前编辑区的修改
        self._save_current_config()
        # self._cfg 是打开对话框那一刻的快照。对话框虽是模态，但全局热键
        # 仍能在此期间启动批量并写 script_ids，直接回写快照会把它revert。
        # 只落本对话框真正编辑的部分：configs 与 active_config。
        latest = load_batch_config()
        latest.configs = self._cfg.configs
        latest.active_config = self._cfg.active_config
        save_batch_config(latest)
        self.accept()

    def _save_current_config(self):
        """将编辑区的内容写回当前配置"""
        if not self._current_name:
            return
        item = self._cfg.configs.get(self._current_name)
        if not item:
            return

        columns = self._defined_columns()
        item.columns = columns
        item.rows = self._read_table_rows()
        self._persist_enabled_flags(self._current_name)
        item.user_column = self._user_column_combo.currentText()
        item.workflows = BatchWorkflows(
            batch_setup=self._get_wf_text(self._wf_batch_setup),
            prepare_item=self._get_wf_text(self._wf_prepare_item),
            finish_item=self._get_wf_text(self._wf_finish_item),
            batch_teardown=self._get_wf_text(self._wf_batch_teardown),
        )
        item.skip_lifecycle_for_single_item = (
            self._skip_single_lifecycle.isChecked()
        )
