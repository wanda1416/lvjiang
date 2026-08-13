"""批量配置对话框 — 管理 (账号, 角色) 条目列表

工具菜单「批量配置」打开此对话框。
支持：添加 / 删除 / 右键复制 / 上下移动排序 / 单元格直接编辑。
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from ..core.batch_config import BatchConfig, BatchEntry, load_batch_config, save_batch_config

_COLUMNS = ["账号全称", "手机尾号", "角色名", "角色下标(1-4)"]
_COL_ACCOUNT = 0
_COL_PHONE = 1
_COL_ROLE = 2
_COL_ROLE_IDX = 3


class BatchConfigDialog(QDialog):
    """批量配置对话框"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("批量配置")
        self.setMinimumSize(520, 400)
        self._setup_ui()
        self._load()

    # ─── UI 构建 ─────────────────────────────────────────

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(8)

        # ── 条目表 ──
        self._table = QTableWidget(0, 4)
        self._table.setHorizontalHeaderLabels(_COLUMNS)
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
        layout.addWidget(self._table)

        # ── 按钮行 ──
        btn_row = QHBoxLayout()

        self._btn_add = QPushButton("添加账号")
        self._btn_add.clicked.connect(self._on_add)
        btn_row.addWidget(self._btn_add)

        self._btn_del = QPushButton("删除")
        self._btn_del.clicked.connect(self._on_delete)
        btn_row.addWidget(self._btn_del)

        btn_row.addStretch()

        self._btn_up = QPushButton("↑ 上移")
        self._btn_up.setFixedWidth(72)
        self._btn_up.clicked.connect(lambda: self._move_row(-1))
        btn_row.addWidget(self._btn_up)

        self._btn_down = QPushButton("↓ 下移")
        self._btn_down.setFixedWidth(72)
        self._btn_down.clicked.connect(lambda: self._move_row(1))
        btn_row.addWidget(self._btn_down)

        layout.addLayout(btn_row)

        # ── 底部提示 + 确定 ──
        hint = QLabel("提示：双击单元格可直接编辑；右键条目可复制为新条目")
        hint.setStyleSheet("color: #999; font-size: 11px;")
        layout.addWidget(hint)

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

    # ─── 数据读写 ─────────────────────────────────────────

    def _load(self):
        """从 batch_config 加载条目到表格"""
        cfg = load_batch_config()
        self._table.setRowCount(0)
        for entry in cfg.entries:
            self._append_row(entry)

    def _read_entries(self) -> list[BatchEntry]:
        """从表格读取所有条目"""
        entries: list[BatchEntry] = []
        for row in range(self._table.rowCount()):
            account = self._cell(row, _COL_ACCOUNT)
            phone_tail = self._cell(row, _COL_PHONE)
            role = self._cell(row, _COL_ROLE)
            role_idx_text = self._cell(row, _COL_ROLE_IDX)
            try:
                role_index = max(1, min(4, int(role_idx_text)))
            except (ValueError, TypeError):
                role_index = 1
            if not account and not role:
                continue
            entries.append(BatchEntry(
                account=account,
                phone_tail=phone_tail,
                role=role,
                role_index=role_index,
            ))
        return entries

    def _cell(self, row: int, col: int) -> str:
        item = self._table.item(row, col)
        return item.text().strip() if item else ""

    def _append_row(self, entry: BatchEntry):
        """向表格追加一行"""
        row = self._table.rowCount()
        self._table.insertRow(row)
        self._table.setItem(row, _COL_ACCOUNT, QTableWidgetItem(entry.account))
        self._table.setItem(row, _COL_PHONE, QTableWidgetItem(entry.phone_tail))
        self._table.setItem(row, _COL_ROLE, QTableWidgetItem(entry.role))
        self._table.setItem(row, _COL_ROLE_IDX, QTableWidgetItem(str(entry.role_index)))

    # ─── 操作 ────────────────────────────────────────────

    def _on_add(self):
        """添加空行，用户双击单元格填写"""
        row = self._table.rowCount()
        self._table.insertRow(row)
        for col in range(self._table.columnCount()):
            self._table.setItem(row, col, QTableWidgetItem(""))
        self._table.scrollToBottom()
        self._table.selectRow(row)

    def _on_delete(self):
        """删除选中行"""
        row = self._table.currentRow()
        if row < 0:
            return
        self._table.removeRow(row)

    def _on_context_menu(self, pos):
        """右键菜单：复制为新条目"""
        row = self._table.rowAt(pos.y())
        if row < 0:
            return

        from PyQt6.QtWidgets import QMenu
        ctx_menu = QMenu(self._table)
        copy_action = QAction("复制为新条目", self)
        copy_action.triggered.connect(lambda: self._copy_row(row))
        ctx_menu.addAction(copy_action)
        ctx_menu.exec(self._table.viewport().mapToGlobal(pos))

    def _copy_row(self, src_row: int):
        """复制指定行为新条目（插入到选中行下方）"""
        entry = BatchEntry(
            account=self._cell(src_row, _COL_ACCOUNT),
            phone_tail=self._cell(src_row, _COL_PHONE),
            role=self._cell(src_row, _COL_ROLE),
            role_index=int(self._cell(src_row, _COL_ROLE_IDX) or "1"),
        )
        insert_at = src_row + 1
        self._table.insertRow(insert_at)
        self._table.setItem(insert_at, _COL_ACCOUNT, QTableWidgetItem(entry.account))
        self._table.setItem(insert_at, _COL_PHONE, QTableWidgetItem(entry.phone_tail))
        self._table.setItem(insert_at, _COL_ROLE, QTableWidgetItem(entry.role))
        self._table.setItem(insert_at, _COL_ROLE_IDX, QTableWidgetItem(str(entry.role_index)))
        self._table.selectRow(insert_at)

    def _move_row(self, delta: int):
        """上移/下移选中行"""
        row = self._table.currentRow()
        if row < 0:
            return
        new_row = row + delta
        if new_row < 0 or new_row >= self._table.rowCount():
            return
        # 交换两行数据
        for col in range(self._table.columnCount()):
            item_a = self._table.takeItem(row, col)
            item_b = self._table.takeItem(new_row, col)
            self._table.setItem(row, col, item_b)
            self._table.setItem(new_row, col, item_a)
        self._table.selectRow(new_row)

    # ─── 保存 ────────────────────────────────────────────

    def _on_save(self):
        """保存条目到 batch_config（保留已有的 script_ids）"""
        entries = self._read_entries()
        old_cfg = load_batch_config()
        cfg = BatchConfig(entries=entries, script_ids=old_cfg.script_ids)
        save_batch_config(cfg)
        self.accept()
