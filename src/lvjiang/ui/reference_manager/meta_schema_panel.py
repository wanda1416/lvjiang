"""元数据定义面板 - 定义图库的 meta 语义字段

元数据分两种场景：
- 输入元数据：用户填写、用于筛选管理参考图（显示名 / 标识 / 类型 / 排序 / 可筛选）。
  名称、分组、备注为内置固定字段，以只读锁定行展示作为上下文。
- 输出元数据：识别时按裁剪区域 OCR 产出（显示名 / 标识 / 裁剪区域），用户可编辑。
"""

import re

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
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

from lvjiang.core.reference_db import MetaFieldDef, ReferenceDatabase, validate_crop

from ...i18n import tr
from .combo_sizing import set_combo_minimum_character_capacity

# 内置固定字段（只读展示，不可编辑/删除）
# (显示名, key, filterable, type, sort_by)
_BUILTIN_ROWS = [
    ("名称", "label", True,  "text", "asc"),
    ("分组", "group", True,  "text", "asc"),
    ("备注", "notes", False, "text", "asc"),
]

_TYPE_OPTIONS = [("文本", "text"), ("数字", "number")]
_SORT_OPTIONS = [("升序", "asc"), ("降序", "desc")]
_META_COMBO_CHARACTER_CAPACITY = 4

_KEY_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")


def format_crop(crop: list[float] | None) -> str:
    """crop [x, y, w, h] -> 显示文本 'x, y, w, h'"""
    if not crop:
        return ""
    return ", ".join(f"{v:g}" for v in crop)


def parse_crop(text: str) -> list[float] | None:
    """解析 'x, y, w, h' 文本，非法返回 None

    要求：4 个 0~1 数值，且 x+w<=1、y+h<=1。
    """
    parts = [p.strip() for p in text.strip().split(",") if p.strip()]
    if len(parts) != 4:
        return None
    try:
        values = [float(p) for p in parts]
    except ValueError:
        return None
    return validate_crop(values)


class MetaSchemaPanel(QWidget):
    """元数据定义面板

    输入元数据表：显示名 | 字段标识(key) | 类型 | 排序 | 可筛选
    （顶部内置行锁定展示 名称/分组/备注）
    输出元数据表：显示名 | 字段标识(key) | 裁剪区域
    """

    schema_changed = pyqtSignal()  # 保存后发射

    def __init__(self, db: ReferenceDatabase, parent=None):
        super().__init__(parent)
        self._db = db
        self._init_ui()
        self.reload()

    def _init_ui(self):
        layout = QVBoxLayout(self)

        # 匹配度阈值配置（写入激活空间 yaml 的 match_threshold，随模式路由）
        threshold_row = QHBoxLayout()
        threshold_row.addWidget(QLabel(tr("匹配度阈值")))
        self._threshold_spin = QDoubleSpinBox()
        self._threshold_spin.setRange(0.0, 1.0)
        self._threshold_spin.setSingleStep(0.01)
        self._threshold_spin.setDecimals(2)
        self._threshold_spin.setToolTip(
            tr("图像识别的最低置信度（0~1），低于此值视为未匹配；越高越严格")
        )
        threshold_row.addWidget(self._threshold_spin)
        threshold_row.addStretch()
        layout.addLayout(threshold_row)

        hint = QLabel(
            "输入元数据：用户填写，用于筛选管理参考图；勾选“可筛选”的字段才会出现在顶部筛选栏。\n"
            "输出元数据：识别材料后按裁剪区域（x, y, w, h，归一化 0~1）OCR 产出并回传调用方。"
        )
        hint.setStyleSheet("color: palette(mid); font-size: 12px;")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        # 工具栏（新增/删除作用于当前聚焦的表格）
        toolbar = QHBoxLayout()
        self._add_btn = QPushButton(tr("新增字段"))
        self._add_btn.clicked.connect(self._on_add_field)
        toolbar.addWidget(self._add_btn)

        self._delete_btn = QPushButton(tr("删除选中"))
        self._delete_btn.clicked.connect(self._on_delete_field)
        toolbar.addWidget(self._delete_btn)

        toolbar.addStretch()

        self._save_btn = QPushButton(tr("保存"))
        self._save_btn.setStyleSheet(
            "QPushButton { background-color: #1976d2; color: white; }"
            "QPushButton:hover { background-color: #1565c0; }"
        )
        self._save_btn.clicked.connect(self._on_save)
        toolbar.addWidget(self._save_btn)
        layout.addLayout(toolbar)

        # ── 输入元数据表 ──
        layout.addWidget(QLabel(tr("输入元数据（用于筛选管理）")))
        self._input_table = QTableWidget(0, 5)
        self._input_table.setHorizontalHeaderLabels(
            [tr("显示名"), tr("字段标识 (key)"), tr("类型"), tr("排序"), tr("可筛选")]
        )
        header = self._input_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        self._input_table.verticalHeader().setVisible(False)
        layout.addWidget(self._input_table)

        # ── 输出元数据表 ──
        layout.addWidget(QLabel(tr("输出元数据（识别时按区域 OCR）")))
        self._output_table = QTableWidget(0, 3)
        self._output_table.setHorizontalHeaderLabels(
            [tr("显示名"), tr("字段标识 (key)"), tr("裁剪区域 (x, y, w, h)")]
        )
        out_header = self._output_table.horizontalHeader()
        out_header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        out_header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        out_header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self._output_table.verticalHeader().setVisible(False)
        layout.addWidget(self._output_table)

    # ── 数据加载 ──

    def reload(self):
        """从数据库重新加载 schema 与匹配度阈值到界面"""
        self._threshold_spin.setValue(self._db.get_match_threshold())

        self._input_table.setRowCount(0)
        # 内置行（锁定）
        for name, key, filterable, ftype, sort_by in _BUILTIN_ROWS:
            self._append_input_row(name, key, filterable, ftype, sort_by, builtin=True)

        self._output_table.setRowCount(0)
        # 用户 schema 行按 scope 分发
        for field in self._db.get_meta_schema():
            if field.scope == "output":
                self._append_output_row(field.name, field.key, format_crop(field.crop))
            else:
                self._append_input_row(
                    field.name, field.key, field.filterable,
                    field.type, field.sort_by, builtin=False,
                )

    def _append_input_row(self, name: str, key: str, filterable: bool,
                          ftype: str, sort_by: str, builtin: bool):
        row = self._input_table.rowCount()
        self._input_table.insertRow(row)

        name_item = QTableWidgetItem(name + ("（内置）" if builtin else ""))
        key_item = QTableWidgetItem(key)
        if builtin:
            for item in (name_item, key_item):
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                item.setForeground(Qt.GlobalColor.gray)
            name_item.setText(name)
            name_item.setToolTip(tr("内置字段，不可编辑或删除"))
        # 用 UserRole 标记是否内置
        name_item.setData(Qt.ItemDataRole.UserRole, builtin)
        self._input_table.setItem(row, 0, name_item)
        self._input_table.setItem(row, 1, key_item)

        # 类型下拉（col 2）
        type_combo = QComboBox()
        for label, val in _TYPE_OPTIONS:
            type_combo.addItem(label, val)
        type_combo.setCurrentIndex(
            max(0, next((i for i, (_, v) in enumerate(_TYPE_OPTIONS) if v == ftype), 0))
        )
        set_combo_minimum_character_capacity(
            type_combo, _META_COMBO_CHARACTER_CAPACITY
        )
        type_combo.setEnabled(not builtin)
        self._input_table.setCellWidget(row, 2, type_combo)

        # 排序下拉（col 3）
        sort_combo = QComboBox()
        for label, val in _SORT_OPTIONS:
            sort_combo.addItem(label, val)
        sort_combo.setCurrentIndex(
            max(0, next((i for i, (_, v) in enumerate(_SORT_OPTIONS) if v == sort_by), 0))
        )
        set_combo_minimum_character_capacity(
            sort_combo, _META_COMBO_CHARACTER_CAPACITY
        )
        sort_combo.setEnabled(not builtin)
        self._input_table.setCellWidget(row, 3, sort_combo)

        # 可筛选复选框（col 4，居中）
        check = QCheckBox()
        check.setChecked(filterable)
        check.setEnabled(not builtin)
        wrapper = QWidget()
        wl = QHBoxLayout(wrapper)
        wl.setContentsMargins(0, 0, 0, 0)
        wl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        wl.addWidget(check)
        self._input_table.setCellWidget(row, 4, wrapper)

    def _append_output_row(self, name: str, key: str, crop_text: str):
        row = self._output_table.rowCount()
        self._output_table.insertRow(row)
        self._output_table.setItem(row, 0, QTableWidgetItem(name))
        self._output_table.setItem(row, 1, QTableWidgetItem(key))
        crop_item = QTableWidgetItem(crop_text)
        crop_item.setToolTip(tr("归一化坐标 x, y, w, h（0~1），如 0, 0, 1, 0.5"))
        self._output_table.setItem(row, 2, crop_item)

    def _is_builtin_row(self, row: int) -> bool:
        item = self._input_table.item(row, 0)
        return bool(item and item.data(Qt.ItemDataRole.UserRole))

    def _focused_table(self) -> QTableWidget:
        """返回当前聚焦的表格（默认输入表）"""
        if self._output_table.hasFocus():
            return self._output_table
        return self._input_table

    def _input_row_checkbox(self, row: int) -> QCheckBox | None:
        wrapper = self._input_table.cellWidget(row, 4)
        if wrapper is None:
            return None
        return wrapper.findChild(QCheckBox)

    def _input_row_type(self, row: int) -> str:
        combo = self._input_table.cellWidget(row, 2)
        return combo.currentData() if isinstance(combo, QComboBox) else "text"

    def _input_row_sort_by(self, row: int) -> str:
        combo = self._input_table.cellWidget(row, 3)
        return combo.currentData() if isinstance(combo, QComboBox) else "asc"

    # ── 槽函数 ──

    def _on_add_field(self):
        table = self._focused_table()
        if table is self._output_table:
            self._append_output_row("", "", "")
            row = self._output_table.rowCount() - 1
            self._output_table.setCurrentCell(row, 0)
            self._output_table.editItem(self._output_table.item(row, 0))
        else:
            self._append_input_row("", "", False, "text", "asc", builtin=False)
            row = self._input_table.rowCount() - 1
            self._input_table.setCurrentCell(row, 0)
            self._input_table.editItem(self._input_table.item(row, 0))

    def _on_delete_field(self):
        table = self._focused_table()
        row = table.currentRow()
        if row < 0:
            return
        if table is self._input_table and self._is_builtin_row(row):
            QMessageBox.information(self, tr("提示"), tr("内置字段不可删除"))
            return
        table.removeRow(row)

    def _collect_schema(self) -> list[MetaFieldDef] | None:
        """从两表收集字段，校验后返回；校验失败返回 None"""
        result: list[MetaFieldDef] = []
        seen_keys: set[str] = set()
        builtin_keys = {key for _, key, *_ in _BUILTIN_ROWS}

        def _check_key(name: str, key: str, table_label: str) -> str | None:
            """校验 key 合法性，返回错误信息或 None"""
            if not key:
                return f"{table_label}：字段标识不能为空"
            if not _KEY_PATTERN.match(key):
                return f"字段标识 “{key}” 非法：需以字母开头，仅含字母/数字/下划线"
            if key in builtin_keys:
                return f"字段标识 “{key}” 与内置字段冲突"
            if key in seen_keys:
                return f"字段标识 “{key}” 重复"
            if not name:
                return f"字段 “{key}” 的显示名不能为空"
            return None

        # 输入元数据
        for row in range(self._input_table.rowCount()):
            if self._is_builtin_row(row):
                continue
            name = (self._input_table.item(row, 0).text()
                    if self._input_table.item(row, 0) else "").strip()
            key = (self._input_table.item(row, 1).text()
                   if self._input_table.item(row, 1) else "").strip()
            error = _check_key(name, key, f"输入元数据第 {row + 1} 行")
            if error:
                QMessageBox.warning(self, tr("校验失败"), error)
                return None
            seen_keys.add(key)
            check = self._input_row_checkbox(row)
            result.append(MetaFieldDef(
                key=key, name=name,
                filterable=bool(check and check.isChecked()),
                type=self._input_row_type(row),
                sort_by=self._input_row_sort_by(row),
                scope="input",
            ))

        # 输出元数据
        for row in range(self._output_table.rowCount()):
            name = (self._output_table.item(row, 0).text()
                    if self._output_table.item(row, 0) else "").strip()
            key = (self._output_table.item(row, 1).text()
                   if self._output_table.item(row, 1) else "").strip()
            crop_text = (self._output_table.item(row, 2).text()
                         if self._output_table.item(row, 2) else "").strip()
            error = _check_key(name, key, f"输出元数据第 {row + 1} 行")
            if error:
                QMessageBox.warning(self, tr("校验失败"), error)
                return None
            crop = parse_crop(crop_text)
            if crop is None:
                QMessageBox.warning(
                    self, tr("校验失败"),
                    f"输出字段 “{key}” 的裁剪区域非法：需 4 个 0~1 数值 "
                    f"(x, y, w, h)，且 x+w≤1、y+h≤1"
                )
                return None
            seen_keys.add(key)
            result.append(MetaFieldDef(
                key=key, name=name, scope="output", crop=crop,
            ))
        return result

    def _on_save(self):
        schema = self._collect_schema()
        if schema is None:
            return
        self._db.set_match_threshold(self._threshold_spin.value())
        self._db.set_meta_schema(schema)
        self.schema_changed.emit()
        QMessageBox.information(self, tr("已保存"), tr("元数据字段定义已保存"))
