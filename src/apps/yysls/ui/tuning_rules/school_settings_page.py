"""规则设置页（规则级字段）

key（只读）、名称、weapon_rules 武器规则表（名字/主武器/主增伤
词条/副武器/副增伤词条，增伤留空 = 不需要增伤），及「删除本规则」
入口。武器与增伤词条为下拉选择，候选来自游戏配置数据源
（AttrRuleManager：weapon_types 注册表 / 指定武学增效词条），
不开放手写；名字列为文本格，直接对应 YAML weapon_rules 节字段。
"""

from __future__ import annotations

from typing import Callable

import re

from PyQt6.QtWidgets import (
    QFormLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
    QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from src.apps.yysls.evaluator.attr_rules import get_attr_rule_manager
from src.ui.widgets import NoWheelComboBox

# 规则 key 约束（作文件名，与 rules._KEY_RE 一致）
_KEY_RE = re.compile(r"^[a-z][a-z0-9_]*$")


class SchoolSettingsPage(QWidget):
    """规则级设置页（编辑共享 raw dict，变更即回调保存）"""

    def __init__(self, data: dict, on_changed: Callable[[], None],
                 on_delete: Callable[[], None] | None = None,
                 on_rename: Callable[[str, str, str], None] | None = None,
                 parent=None):
        super().__init__(parent)
        self._data = data
        self._on_changed = on_changed
        self._on_delete = on_delete
        self._on_rename = on_rename
        self._loading = True
        # 武器/增伤词条候选来自游戏配置数据源（保存时已刷新单例）
        mgr = get_attr_rule_manager()
        weapons = mgr.get_weapon_types()
        wuxue = mgr.get_wuxue_affix_names()
        self._col_candidates: dict[int, list[str]] = {
            1: weapons, 2: wuxue, 3: weapons, 4: wuxue}
        self._init_ui()
        self._load()
        self._loading = False

    def _init_ui(self):
        layout = QVBoxLayout(self)

        form = QFormLayout()
        self._key_edit = QLineEdit()
        self._key_edit.editingFinished.connect(self._apply_basic)
        form.addRow("标识 key：", self._key_edit)

        self._name_edit = QLineEdit()
        self._name_edit.editingFinished.connect(self._apply_basic)
        form.addRow("规则名称：", self._name_edit)
        layout.addLayout(form)

        # ── weapon_rules 武器规则表 ──
        layout.addWidget(QLabel(
            "<b>武器规则表</b>（名字 = 调律 Tab 勾选项；"
            "增伤词条留空 = 该侧不需要增伤；"
            "武器/词条候选来自游戏配置）"))
        self._weapon_table = QTableWidget(0, 5)
        self._weapon_table.setHorizontalHeaderLabels(
            ["名字", "主武器", "主增伤词条", "副武器", "副增伤词条"])
        # 增伤词条列比武器列更宽；不拉伸末列，列宽固定、列表偏左，
        # 未占满 dialog 时右侧留白（避免末列被拉升占满全宽）
        for col, width in enumerate((100, 90, 200, 90, 200)):
            self._weapon_table.setColumnWidth(col, width)
        self._weapon_table.cellChanged.connect(self._apply_weapon_rules)
        layout.addWidget(self._weapon_table)
        layout.addLayout(
            self._table_buttons(self._weapon_table, self._apply_weapon_rules))

        # ── 删除本规则 ──
        del_row = QHBoxLayout()
        btn_delete = QPushButton("删除本规则")
        btn_delete.setStyleSheet("color: #c62828;")
        btn_delete.clicked.connect(self._confirm_delete)
        del_row.addWidget(btn_delete)
        del_row.addStretch()
        layout.addLayout(del_row)
        layout.addStretch()

    def _table_buttons(self, table: QTableWidget, apply) -> QHBoxLayout:
        row = QHBoxLayout()
        btn_add = QPushButton("添加行")
        btn_add.clicked.connect(
            lambda: self._insert_weapon_row(table.rowCount()))
        btn_del = QPushButton("删除选中行")

        def _delete():
            r = table.currentRow()
            if r >= 0:
                table.removeRow(r)
                apply()

        btn_del.clicked.connect(_delete)
        row.addWidget(btn_add)
        row.addWidget(btn_del)
        row.addStretch()
        return row

    # ── 武器规则表行构建（名字为文本格，其余列为下拉格）──

    def _insert_weapon_row(self, row: int,
                           values: tuple = ("", "", "", "", "")):
        table = self._weapon_table
        table.insertRow(row)
        table.setItem(row, 0, QTableWidgetItem(values[0]))
        for col in range(1, 5):
            table.setCellWidget(
                row, col, self._make_cell_combo(col, values[col]))

    def _make_cell_combo(self, col: int, value: str) -> NoWheelComboBox:
        """单元格下拉框：候选来自游戏配置数据源；
        失效旧值保留展示便于改正"""
        combo = NoWheelComboBox()
        combo.addItem("")  # 留空 = 未配置 / 该侧不需要增伤
        candidates = self._col_candidates[col]
        combo.addItems(candidates)
        if value and value not in candidates:
            combo.addItem(value)
        combo.setCurrentText(value)
        combo.currentTextChanged.connect(
            lambda _text: self._apply_weapon_rules())
        return combo

    def _combo_text(self, row: int, col: int) -> str:
        widget = self._weapon_table.cellWidget(row, col)
        return widget.currentText().strip() if widget else ""

    # ── 回填 ──

    def _load(self):
        d = self._data
        self._key_edit.setText(str(d.get("key", "")))
        self._name_edit.setText(str(d.get("name", "")))

        rules = d.get("weapon_rules") or {}
        self._weapon_table.blockSignals(True)
        self._weapon_table.setRowCount(0)
        for name, raw in rules.items():
            raw = raw or {}
            main = raw.get("main") or {}
            sub = raw.get("sub") or {}
            self._insert_weapon_row(self._weapon_table.rowCount(), (
                name,
                str(main.get("weapon") or ""),
                str(main.get("damage") or ""),
                str(sub.get("weapon") or ""),
                str(sub.get("damage") or "")))
        self._weapon_table.blockSignals(False)

    # ── 收集（写回共享 dict） ──

    def _apply_basic(self):
        if self._loading:
            return
        old_key = str(self._data.get("key", ""))
        new_key = self._key_edit.text().strip()
        new_name = self._name_edit.text().strip()
        if not new_key or not _KEY_RE.match(new_key):
            QMessageBox.warning(
                self, "规则设置",
                "标识 key 须为小写字母开头的英文/数字/下划线")
            self._key_edit.setText(old_key)
            return
        if not new_name:
            QMessageBox.warning(self, "规则设置", "规则名称不能为空")
            self._name_edit.setText(str(self._data.get("name", "")))
            return
        # key 变更：重命名文件并通知面板/对话框更新 Tab
        if new_key != old_key and self._on_rename is not None:
            self._on_rename(old_key, new_key, new_name)
        self._data["key"] = new_key
        self._data["name"] = new_name
        self._on_changed()

    def _apply_weapon_rules(self):
        if self._loading:
            return
        rules: dict = {}
        for i in range(self._weapon_table.rowCount()):
            name = self._cell(self._weapon_table, i, 0)
            if not name:
                continue
            rules[name] = {
                "main": {
                    "weapon": self._combo_text(i, 1),
                    "damage": self._combo_text(i, 2) or None,
                },
                "sub": {
                    "weapon": self._combo_text(i, 3),
                    "damage": self._combo_text(i, 4) or None,
                },
            }
        if rules:
            self._data["weapon_rules"] = rules
        else:
            self._data.pop("weapon_rules", None)
        self._on_changed()

    @staticmethod
    def _cell(table: QTableWidget, row: int, col: int) -> str:
        item = table.item(row, col)
        return item.text().strip() if item else ""

    # ── 删除 ──

    def _confirm_delete(self):
        if self._on_delete is None:
            return
        name = str(self._data.get("name") or self._data.get("key") or "")
        ret = QMessageBox.question(
            self, "删除规则",
            f"确定删除调律规则「{name}」？规则文件将被删除，不可恢复。")
        if ret == QMessageBox.StandardButton.Yes:
            self._on_delete()
