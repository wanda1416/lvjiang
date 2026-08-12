"""批量配置对话框 — 管理多个命名配置

工具菜单「批量配置」打开此对话框。
每个配置包含：名字 + 用户自定义列的 table + 生命周期 wf 槽位。
支持：新建配置 / 删除配置 / 切换配置 / 自定义列名 / 编辑行数据。
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from ...core.batch_config import (
    BatchConfigItem,
    BatchWorkflows,
    load_batch_config,
    save_batch_config,
)


class BatchConfigDialog(QDialog):
    """批量配置对话框"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("批量配置")
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
        config_row.addWidget(QLabel("配置："))
        self._config_combo = QComboBox()
        self._config_combo.setMinimumWidth(150)
        self._config_combo.currentIndexChanged.connect(self._on_config_selected)
        config_row.addWidget(self._config_combo, stretch=1)

        self._btn_new = QPushButton("新建")
        self._btn_new.setFixedWidth(60)
        self._btn_new.clicked.connect(self._on_new_config)
        config_row.addWidget(self._btn_new)

        self._btn_delete = QPushButton("删除")
        self._btn_delete.setFixedWidth(60)
        self._btn_delete.clicked.connect(self._on_delete_config)
        config_row.addWidget(self._btn_delete)

        layout.addLayout(config_row)

        # ── 列定义行 ──
        col_row = QHBoxLayout()
        col_row.addWidget(QLabel("列名（逗号分隔）："))
        self._columns_input = QLineEdit()
        self._columns_input.setPlaceholderText("col1, col2, col3, ...")
        self._columns_input.editingFinished.connect(self._on_columns_changed)
        col_row.addWidget(self._columns_input, stretch=1)
        layout.addLayout(col_row)

        # ── 用户名列 ──
        user_col_row = QHBoxLayout()
        user_col_row.addWidget(QLabel("用户名列："))
        self._user_column_input = QLineEdit()
        self._user_column_input.setPlaceholderText("(可选)")
        self._user_column_input.setFixedWidth(120)
        user_col_row.addWidget(self._user_column_input)
        user_col_row.addStretch()
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
        layout.addWidget(self._table, stretch=1)

        # ── 表格操作行 ──
        table_btn_row = QHBoxLayout()
        self._btn_add_row = QPushButton("添加行")
        self._btn_add_row.clicked.connect(self._on_add_row)
        table_btn_row.addWidget(self._btn_add_row)

        self._btn_del_row = QPushButton("删除行")
        self._btn_del_row.clicked.connect(self._on_delete_row)
        table_btn_row.addWidget(self._btn_del_row)

        table_btn_row.addStretch()

        self._btn_up = QPushButton("↑ 上移")
        self._btn_up.setFixedWidth(72)
        self._btn_up.clicked.connect(lambda: self._move_row(-1))
        table_btn_row.addWidget(self._btn_up)

        self._btn_down = QPushButton("↓ 下移")
        self._btn_down.setFixedWidth(72)
        self._btn_down.clicked.connect(lambda: self._move_row(1))
        table_btn_row.addWidget(self._btn_down)

        layout.addLayout(table_btn_row)

        # ── 生命周期 wf 槽位 ──
        wf_form = QFormLayout()
        wf_form.setContentsMargins(0, 4, 0, 0)

        self._wf_batch_setup = self._create_wf_selector()
        wf_form.addRow("批次初始化 wf：", self._wf_batch_setup)

        self._wf_prepare_item = self._create_wf_selector()
        wf_form.addRow("条目准备 wf：", self._wf_prepare_item)

        self._wf_finish_item = self._create_wf_selector()
        wf_form.addRow("条目收尾 wf：", self._wf_finish_item)

        self._wf_batch_teardown = self._create_wf_selector()
        wf_form.addRow("批次收尾 wf：", self._wf_batch_teardown)

        layout.addLayout(wf_form)

        # ── 底部按钮 ──
        bottom_row = QHBoxLayout()
        bottom_row.addStretch()
        btn_save = QPushButton("保存")
        btn_save.setDefault(True)
        btn_save.clicked.connect(self._on_save)
        bottom_row.addWidget(btn_save)
        btn_cancel = QPushButton("取消")
        btn_cancel.clicked.connect(self.reject)
        bottom_row.addWidget(btn_cancel)
        layout.addLayout(bottom_row)

    def _create_wf_selector(self) -> QHBoxLayout:
        """创建 wf 文件选择器（输入框 + 浏览按钮）"""
        row = QHBoxLayout()
        line_edit = QLineEdit()
        line_edit.setPlaceholderText("选择 wf 文件...")
        row.addWidget(line_edit, stretch=1)

        btn = QPushButton("浏览...")
        btn.setFixedWidth(70)
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
            self, "选择工作流文件", start_dir,
            "工作流文件 (*.wf);;所有文件 (*)"
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

        # 列名
        self._columns_input.setText(", ".join(item.columns))

        # 用户名列
        self._user_column_input.setText(item.user_column)

        # 表格：先清空，再创建行
        self._table.setColumnCount(len(item.columns))
        self._table.setHorizontalHeaderLabels(item.columns)
        self._table.setRowCount(0)

        for row_data in item.rows:
            row_idx = self._table.rowCount()
            self._table.insertRow(row_idx)
            for col_idx, col_name in enumerate(item.columns):
                val = str(row_data.get(col_name, ""))
                self._table.setItem(row_idx, col_idx, QTableWidgetItem(val))

        # wf 槽位
        self._set_wf_text(self._wf_batch_setup, item.workflows.batch_setup)
        self._set_wf_text(self._wf_prepare_item, item.workflows.prepare_item)
        self._set_wf_text(self._wf_finish_item, item.workflows.finish_item)
        self._set_wf_text(self._wf_batch_teardown, item.workflows.batch_teardown)

    def _clear_editor(self):
        """清空编辑区"""
        self._current_name = ""
        self._columns_input.clear()
        self._user_column_input.clear()
        self._table.setRowCount(0)
        self._table.setColumnCount(0)
        self._set_wf_text(self._wf_batch_setup, "")
        self._set_wf_text(self._wf_prepare_item, "")
        self._set_wf_text(self._wf_finish_item, "")
        self._set_wf_text(self._wf_batch_teardown, "")

    def _on_config_selected(self, index: int):
        """配置下拉框切换"""
        if index < 0:
            return
        name = self._config_combo.itemText(index)
        self._load_config_to_editor(name)

    def _on_new_config(self):
        """新建配置"""
        from PyQt6.QtWidgets import QInputDialog
        name, ok = QInputDialog.getText(self, "新建配置", "配置名称：")
        if not ok or not name.strip():
            return
        name = name.strip()
        if name in self._cfg.configs:
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "重复", f"配置 {name!r} 已存在")
            return

        item = BatchConfigItem(
            name=name,
            columns=["col1", "col2"],
            rows=[],
            workflows=BatchWorkflows(),
            user_column="",
        )
        self._cfg.configs[name] = item
        self._cfg.active_config = name
        self._refresh_config_list()

    def _on_delete_config(self):
        """删除当前配置"""
        if not self._current_name:
            return
        from PyQt6.QtWidgets import QMessageBox
        reply = QMessageBox.question(
            self, "确认删除",
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

    def _on_columns_changed(self):
        """列名变更 → 直接替换列名，保留数据"""
        new_columns = self._parse_columns()
        if not new_columns:
            return

        # 直接设置新列名，保留现有数据
        self._table.setColumnCount(len(new_columns))
        self._table.setHorizontalHeaderLabels(new_columns)

    def _parse_columns(self) -> list[str]:
        """解析列名输入"""
        text = self._columns_input.text().strip()
        if not text:
            return []
        return [c.strip() for c in text.split(",") if c.strip()]

    # ─── 表格操作 ─────────────────────────────────────────

    def _read_table_rows(self) -> list[dict]:
        """从表格读取所有行数据"""
        columns = self._parse_columns()
        rows = []
        for row_idx in range(self._table.rowCount()):
            row_data = {}
            for col_idx, col_name in enumerate(columns):
                item = self._table.item(row_idx, col_idx)
                row_data[col_name] = item.text().strip() if item else ""
            rows.append(row_data)
        return rows

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
        copy_action = QAction("复制为新行", self)
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
        save_batch_config(self._cfg)
        self.accept()

    def _save_current_config(self):
        """将编辑区的内容写回当前配置"""
        if not self._current_name:
            return
        item = self._cfg.configs.get(self._current_name)
        if not item:
            return

        columns = self._parse_columns()
        item.columns = columns
        item.rows = self._read_table_rows()
        item.user_column = self._user_column_input.text().strip()
        item.workflows = BatchWorkflows(
            batch_setup=self._get_wf_text(self._wf_batch_setup),
            prepare_item=self._get_wf_text(self._wf_prepare_item),
            finish_item=self._get_wf_text(self._wf_finish_item),
            batch_teardown=self._get_wf_text(self._wf_batch_teardown),
        )
