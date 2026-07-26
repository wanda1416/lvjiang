"""条件受限构造器与词条选择对话框

ConditionEditor：垂直行式条件列表（行间 AND 语义），每行由
原语类型下拉 + 词条 tag 区 + 计数参数组成，产出/回填规则 YAML
的条件原语原始 dict。

ConditionGroupsEditor：条件组列表（组间 OR、组内 AND），三档
判定条件（junk/usable/top）共用；单条件组产出单键 dict，
多条件组产出原语 dict 列表（与 rules._parse_condition_groups 对应）。
候选词条为标准词条全集，由构造方注入。

AffixPickerDialog：标准词条多选对话框（带滚动的多列复选网格），
包内复用（首词条候选、条件词条、词条库添加等场景）。
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QGridLayout,
    QGroupBox, QHBoxLayout, QLabel, QPushButton, QScrollArea, QVBoxLayout,
    QWidget,
)

from src.ui.widgets import NoWheelSpinBox

# 原语类型 → 显示名
_KIND_NAMES = {
    "not_contains": "未出现任一",
    "contains_all": "必须同时出现",
    "not_together": "不同时出现",
    "count_max": "计数上限",
    "count_min": "计数下限",
}


class AffixPickerDialog(QDialog):
    """标准词条多选对话框（候选按传入顺序排列，滚动多列复选网格）"""

    _COLS = 3

    def __init__(self, candidates: list[str], selected: list[str],
                 title: str = "选择词条", parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(520)
        layout = QVBoxLayout(self)

        grid_host = QWidget()
        grid = QGridLayout(grid_host)
        grid.setSpacing(6)
        self._checks: dict[str, QCheckBox] = {}
        chosen = set(selected)
        for i, name in enumerate(candidates):
            cb = QCheckBox(name)
            cb.setChecked(name in chosen)
            grid.addWidget(cb, i // self._COLS, i % self._COLS)
            self._checks[name] = cb

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(grid_host)
        scroll.setMinimumHeight(320)
        layout.addWidget(scroll)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def selected(self) -> list[str]:
        """按候选顺序返回勾选的词条"""
        return [n for n, cb in self._checks.items() if cb.isChecked()]


class _ConditionRow(QWidget):
    """单条条件行：原语类型 + 词条 tag + 计数参数 + 删除"""

    changed = pyqtSignal()
    remove_requested = pyqtSignal(object)

    def __init__(self, kinds: list[str], candidates: list[str], parent=None):
        super().__init__(parent)
        self._candidates = candidates
        self._symbols: list[str] = []
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.kind_combo = QComboBox()
        for kind in kinds:
            self.kind_combo.addItem(_KIND_NAMES[kind], kind)
        self.kind_combo.currentIndexChanged.connect(self._on_kind_changed)
        layout.addWidget(self.kind_combo)

        self.symbols_btn = QPushButton("（点击选择词条）")
        self.symbols_btn.clicked.connect(self._pick_symbols)
        layout.addWidget(self.symbols_btn, 1)

        self.count_spin = NoWheelSpinBox()
        self.count_spin.setRange(1, 5)
        self.count_spin.valueChanged.connect(self.changed)
        layout.addWidget(self.count_spin)

        self.first_check = QCheckBox("含首词条")
        self.first_check.stateChanged.connect(self.changed)
        layout.addWidget(self.first_check)

        btn_del = QPushButton("删除")
        btn_del.setFixedWidth(50)
        btn_del.clicked.connect(lambda: self.remove_requested.emit(self))
        layout.addWidget(btn_del)

        self._update_visibility()

    # ── 数据往返 ──

    def set_condition(self, raw: dict):
        """回填规则 YAML 的条件原语 dict（单键）"""
        kind, args = next(iter(raw.items()))
        idx = self.kind_combo.findData(kind)
        self.kind_combo.blockSignals(True)
        self.kind_combo.setCurrentIndex(max(idx, 0))
        self.kind_combo.blockSignals(False)
        if kind in ("count_max", "count_min"):
            args = args or {}
            self._symbols = list(args.get("symbols") or [])
            self.count_spin.blockSignals(True)
            self.count_spin.setValue(
                int(args.get("max" if kind == "count_max" else "min", 1)))
            self.count_spin.blockSignals(False)
            self.first_check.blockSignals(True)
            self.first_check.setChecked(bool(args.get("include_first")))
            self.first_check.blockSignals(False)
        else:
            self._symbols = list(args or [])
        self._update_symbols_text()
        self._update_visibility()

    def get_condition(self) -> dict:
        kind = self.kind_combo.currentData()
        if kind == "count_max":
            return {"count_max": {
                "symbols": list(self._symbols),
                "max": self.count_spin.value(),
                "include_first": self.first_check.isChecked(),
            }}
        if kind == "count_min":
            args: dict = {"symbols": list(self._symbols),
                          "min": self.count_spin.value()}
            if self.first_check.isChecked():
                args["include_first"] = True
            return {"count_min": args}
        return {kind: list(self._symbols)}

    # ── 内部 ──

    def _on_kind_changed(self):
        self._update_visibility()
        self.changed.emit()

    def _update_visibility(self):
        is_count = self.kind_combo.currentData() in ("count_max", "count_min")
        self.count_spin.setVisible(is_count)
        self.first_check.setVisible(is_count)

    def _pick_symbols(self):
        dlg = AffixPickerDialog(self._candidates, self._symbols,
                                "选择条件词条", self)
        if dlg.exec():
            self._symbols = dlg.selected()
            self._update_symbols_text()
            self.changed.emit()

    def _update_symbols_text(self):
        self.symbols_btn.setText(
            "/".join(self._symbols) if self._symbols else "（点击选择词条）")


class ConditionEditor(QWidget):
    """条件列表编辑器（行间 AND 语义，空列表 = 无附加条件）"""

    changed = pyqtSignal()

    def __init__(self, candidates: list[str],
                 label: str | None = "条件列表（全部满足方成立）：",
                 parent=None):
        super().__init__(parent)
        self._candidates = candidates
        self._kinds = ["not_contains", "contains_all", "not_together",
                       "count_max", "count_min"]
        self._rows: list[_ConditionRow] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        if label:
            layout.addWidget(QLabel(label))
        self._rows_layout = QVBoxLayout()
        self._rows_layout.setSpacing(2)
        layout.addLayout(self._rows_layout)

        btn_add = QPushButton("+ 添加条件")
        btn_add.clicked.connect(self._add_row_clicked)
        layout.addWidget(btn_add, alignment=Qt.AlignmentFlag.AlignLeft)

    # ── 数据往返 ──

    def set_conditions(self, raw_list: list[dict]):
        for row in self._rows:
            row.setParent(None)
            row.deleteLater()
        self._rows.clear()
        for raw in raw_list or []:
            if isinstance(raw, dict) and len(raw) == 1:
                self._append_row().set_condition(raw)

    def get_conditions(self) -> list[dict]:
        return [row.get_condition() for row in self._rows]

    # ── 内部 ──

    def _append_row(self) -> _ConditionRow:
        row = _ConditionRow(self._kinds, self._candidates)
        row.changed.connect(self.changed)
        row.remove_requested.connect(self._remove_row)
        self._rows.append(row)
        self._rows_layout.addWidget(row)
        return row

    def _add_row_clicked(self):
        self._append_row()
        # 新行词条为空，待用户选择后再触发 changed 保存

    def _remove_row(self, row: _ConditionRow):
        if row in self._rows:
            self._rows.remove(row)
            row.setParent(None)
            row.deleteLater()
            self.changed.emit()


class _ConditionGroupBox(QGroupBox):
    """单条件组：组内条件列表（AND）+ 删除组"""

    changed = pyqtSignal()
    remove_requested = pyqtSignal(object)

    def __init__(self, candidates: list[str], parent=None):
        super().__init__("条件组（组内全部满足方命中）", parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        self.editor = ConditionEditor(candidates, label=None)
        self.editor.changed.connect(self.changed)
        layout.addWidget(self.editor)
        btn_del = QPushButton("删除本组")
        btn_del.setFixedWidth(70)
        btn_del.clicked.connect(lambda: self.remove_requested.emit(self))
        layout.addWidget(btn_del, alignment=Qt.AlignmentFlag.AlignRight)


class ConditionGroupsEditor(QWidget):
    """条件组列表编辑器（组间 OR、组内 AND，空列表 = 该档不触发）

    与规则 YAML 三档条件语法往返：单键 dict 视作单条件组，
    list 为组内 AND；产出时单条件组压回单键 dict。
    """

    changed = pyqtSignal()

    def __init__(self, candidates: list[str], parent=None):
        super().__init__(parent)
        self._candidates = candidates
        self._groups: list[_ConditionGroupBox] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._groups_layout = QVBoxLayout()
        self._groups_layout.setSpacing(4)
        layout.addLayout(self._groups_layout)

        btn_add = QPushButton("+ 添加条件组")
        btn_add.clicked.connect(self._add_group_clicked)
        layout.addWidget(btn_add, alignment=Qt.AlignmentFlag.AlignLeft)

    # ── 数据往返 ──

    def set_groups(self, raw_list: list):
        for grp in self._groups:
            grp.setParent(None)
            grp.deleteLater()
        self._groups.clear()
        for raw in raw_list or []:
            if isinstance(raw, dict):
                raw = [raw]
            if isinstance(raw, list):
                self._append_group().editor.set_conditions(raw)

    def get_groups(self) -> list:
        result = []
        for grp in self._groups:
            conds = grp.editor.get_conditions()
            if not conds:
                continue  # 空组不产出（避免意外的永真/永假组）
            result.append(conds[0] if len(conds) == 1 else conds)
        return result

    # ── 内部 ──

    def _append_group(self) -> _ConditionGroupBox:
        grp = _ConditionGroupBox(self._candidates)
        grp.changed.connect(self.changed)
        grp.remove_requested.connect(self._remove_group)
        self._groups.append(grp)
        self._groups_layout.addWidget(grp)
        return grp

    def _add_group_clicked(self):
        self._append_group()
        # 新组无条件，待用户添加条件后再触发 changed 保存

    def _remove_group(self, grp: _ConditionGroupBox):
        if grp in self._groups:
            self._groups.remove(grp)
            grp.setParent(None)
            grp.deleteLater()
            self.changed.emit()
