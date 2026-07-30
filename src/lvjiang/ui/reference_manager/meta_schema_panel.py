"""元数据定义面板 - 定义图库的 meta 语义字段（显示名 / 标识 / 可筛选）

名称、分组、备注为内置固定字段，以只读锁定行展示作为上下文；
其余 meta 字段由用户在此定义，值统一按文本存储。
"""

import re

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from lvjiang.core.reference_db import MetaFieldDef, ReferenceDatabase

# 内置固定字段（只读展示，不可编辑/删除）
# (显示名, key, filterable, type, sort_by)
_BUILTIN_ROWS = [
    ("名称", "label", True,  "text", "asc"),
    ("分组", "group", True,  "text", "asc"),
    ("备注", "notes", False, "text", "asc"),
]

_TYPE_OPTIONS = [("文本", "text"), ("数字", "number")]
_SORT_OPTIONS = [("升序", "asc"), ("降序", "desc")]

_KEY_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")


class MetaSchemaPanel(QWidget):
    """元数据定义面板

    表格列：显示名 | 字段标识(key) | 可筛选(复选)
    顶部内置行锁定展示 名称/分组/备注；其下为用户可编辑的 meta 字段。
    """

    schema_changed = pyqtSignal()  # 保存后发射

    def __init__(self, db: ReferenceDatabase, parent=None):
        super().__init__(parent)
        self._db = db
        self._init_ui()
        self.reload()

    def _init_ui(self):
        layout = QVBoxLayout(self)

        hint = QLabel(
            "定义“名称、分组”之外的元数据字段。值统一按文本存储；"
            "勾选“可筛选”的字段才会出现在图库管理的顶部筛选栏。"
        )
        hint.setStyleSheet("color: #888; font-size: 12px;")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        # 工具栏
        toolbar = QHBoxLayout()
        self._add_btn = QPushButton("新增字段")
        self._add_btn.clicked.connect(self._on_add_field)
        toolbar.addWidget(self._add_btn)

        self._delete_btn = QPushButton("删除选中")
        self._delete_btn.clicked.connect(self._on_delete_field)
        toolbar.addWidget(self._delete_btn)

        toolbar.addStretch()

        self._save_btn = QPushButton("保存")
        self._save_btn.setStyleSheet(
            "QPushButton { background-color: #1976d2; color: white; }"
            "QPushButton:hover { background-color: #1565c0; }"
        )
        self._save_btn.clicked.connect(self._on_save)
        toolbar.addWidget(self._save_btn)
        layout.addLayout(toolbar)

        # 表格
        self._table = QTableWidget(0, 5)
        self._table.setHorizontalHeaderLabels(
            ["显示名", "字段标识 (key)", "类型", "排序", "可筛选"]
        )
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        self._table.verticalHeader().setVisible(False)
        layout.addWidget(self._table)

    # ── 数据加载 ──

    def reload(self):
        """从数据库重新加载 schema 到表格"""
        self._table.setRowCount(0)
        # 内置行（锁定）
        for name, key, filterable, ftype, sort_by in _BUILTIN_ROWS:
            self._append_row(name, key, filterable, ftype, sort_by, builtin=True)
        # 用户 schema 行
        for field in self._db.get_meta_schema():
            self._append_row(
                field.name, field.key, field.filterable,
                field.type, field.sort_by, builtin=False,
            )

    def _append_row(self, name: str, key: str, filterable: bool,
                    ftype: str, sort_by: str, builtin: bool):
        row = self._table.rowCount()
        self._table.insertRow(row)

        name_item = QTableWidgetItem(name + ("（内置）" if builtin else ""))
        key_item = QTableWidgetItem(key)
        if builtin:
            for item in (name_item, key_item):
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                item.setForeground(Qt.GlobalColor.gray)
            name_item.setText(name)
            name_item.setToolTip("内置字段，不可编辑或删除")
        # 用 UserRole 标记是否内置
        name_item.setData(Qt.ItemDataRole.UserRole, builtin)
        self._table.setItem(row, 0, name_item)
        self._table.setItem(row, 1, key_item)

        # 类型下拉（col 2）
        type_combo = QComboBox()
        for label, val in _TYPE_OPTIONS:
            type_combo.addItem(label, val)
        type_combo.setCurrentIndex(
            max(0, next((i for i, (_, v) in enumerate(_TYPE_OPTIONS) if v == ftype), 0))
        )
        type_combo.setEnabled(not builtin)
        self._table.setCellWidget(row, 2, type_combo)

        # 排序下拉（col 3）
        sort_combo = QComboBox()
        for label, val in _SORT_OPTIONS:
            sort_combo.addItem(label, val)
        sort_combo.setCurrentIndex(
            max(0, next((i for i, (_, v) in enumerate(_SORT_OPTIONS) if v == sort_by), 0))
        )
        sort_combo.setEnabled(not builtin)
        self._table.setCellWidget(row, 3, sort_combo)

        # 可筛选复选框（col 4，居中）
        check = QCheckBox()
        check.setChecked(filterable)
        check.setEnabled(not builtin)
        wrapper = QWidget()
        wl = QHBoxLayout(wrapper)
        wl.setContentsMargins(0, 0, 0, 0)
        wl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        wl.addWidget(check)
        self._table.setCellWidget(row, 4, wrapper)

    def _is_builtin_row(self, row: int) -> bool:
        item = self._table.item(row, 0)
        return bool(item and item.data(Qt.ItemDataRole.UserRole))

    def _row_checkbox(self, row: int) -> QCheckBox | None:
        wrapper = self._table.cellWidget(row, 4)
        if wrapper is None:
            return None
        return wrapper.findChild(QCheckBox)

    def _row_type(self, row: int) -> str:
        combo = self._table.cellWidget(row, 2)
        return combo.currentData() if isinstance(combo, QComboBox) else "text"

    def _row_sort_by(self, row: int) -> str:
        combo = self._table.cellWidget(row, 3)
        return combo.currentData() if isinstance(combo, QComboBox) else "asc"

    # ── 槽函数 ──

    def _on_add_field(self):
        self._append_row("", "", False, "text", "asc", builtin=False)
        # 聚焦到新行的显示名单元格
        row = self._table.rowCount() - 1
        self._table.setCurrentCell(row, 0)
        self._table.editItem(self._table.item(row, 0))

    def _on_delete_field(self):
        row = self._table.currentRow()
        if row < 0:
            return
        if self._is_builtin_row(row):
            QMessageBox.information(self, "提示", "内置字段不可删除")
            return
        self._table.removeRow(row)

    def _collect_schema(self) -> list[MetaFieldDef] | None:
        """从表格收集用户字段，校验后返回；校验失败返回 None"""
        result: list[MetaFieldDef] = []
        seen_keys: set[str] = set()
        builtin_keys = {key for _, key, *_ in _BUILTIN_ROWS}

        for row in range(self._table.rowCount()):
            if self._is_builtin_row(row):
                continue
            name = (self._table.item(row, 0).text() if self._table.item(row, 0) else "").strip()
            key = (self._table.item(row, 1).text() if self._table.item(row, 1) else "").strip()
            check = self._row_checkbox(row)
            filterable = bool(check and check.isChecked())
            ftype = self._row_type(row)
            sort_by = self._row_sort_by(row)

            if not key:
                QMessageBox.warning(self, "校验失败", f"第 {row + 1} 行：字段标识不能为空")
                return None
            if not _KEY_PATTERN.match(key):
                QMessageBox.warning(
                    self, "校验失败",
                    f"字段标识 “{key}” 非法：需以字母开头，仅含字母/数字/下划线"
                )
                return None
            if key in builtin_keys:
                QMessageBox.warning(self, "校验失败", f"字段标识 “{key}” 与内置字段冲突")
                return None
            if key in seen_keys:
                QMessageBox.warning(self, "校验失败", f"字段标识 “{key}” 重复")
                return None
            if not name:
                QMessageBox.warning(self, "校验失败", f"字段 “{key}” 的显示名不能为空")
                return None

            seen_keys.add(key)
            result.append(MetaFieldDef(
                key=key, name=name, filterable=filterable,
                type=ftype, sort_by=sort_by,
            ))
        return result

    def _on_save(self):
        schema = self._collect_schema()
        if schema is None:
            return
        self._db.set_meta_schema(schema)
        self.schema_changed.emit()
        QMessageBox.information(self, "已保存", "元数据字段定义已保存")
