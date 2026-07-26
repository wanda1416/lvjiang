"""条件受限构造器与符号选择对话框

ConditionEditor：垂直行式条件列表（行间 AND 语义），每行由
原语类型下拉 + 符号 tag 区 + 计数参数组成，产出/回填规则 YAML
的条件原语原始 dict。顶级判定条件与流派附加垃圾规则共用。

SymbolPickerDialog：符号多选对话框，包内复用（必选槽候选、
条件符号、允许神力等场景）。
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QGridLayout,
    QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget,
)

from src.ui.widgets import NoWheelSpinBox

# 符号词汇表（展示顺序固定，与 rules.SYMBOL_VOCAB 一致）
SYMBOL_ORDER = [
    "大外", "小外", "劲", "势", "敏", "会意", "会心", "精准",
    "大无相", "小无相", "小外属",
]

# 神力词条候选（必选槽 / 增伤要求 / 允许神力）
DIVINE_CANDIDATES = [
    "全武学增效", "扇武学增效", "对首领单位增伤",
    "对玩家单位增效", "单体类奇术增伤",
]

# DMG 占位符（由流派设置页的 weapons 表按武器角色解析）
DMG_PLACEHOLDER = "DMG"

# 原语类型 → 显示名
_KIND_NAMES = {
    "not_contains": "未出现任一",
    "contains_all": "必须同时出现",
    "not_together": "不同时出现",
    "count_max": "计数上限",
    "count_min": "计数下限",
}


class SymbolPickerDialog(QDialog):
    """符号多选对话框（候选按传入顺序排列，复选框网格）"""

    def __init__(self, candidates: list[str], selected: list[str],
                 title: str = "选择符号", parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(360)
        layout = QVBoxLayout(self)

        grid = QGridLayout()
        grid.setSpacing(6)
        self._checks: dict[str, QCheckBox] = {}
        chosen = set(selected)
        for i, name in enumerate(candidates):
            cb = QCheckBox(name)
            cb.setChecked(name in chosen)
            grid.addWidget(cb, i // 3, i % 3)
            self._checks[name] = cb
        layout.addLayout(grid)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def selected(self) -> list[str]:
        """按候选顺序返回勾选的符号"""
        return [n for n, cb in self._checks.items() if cb.isChecked()]


class _ConditionRow(QWidget):
    """单条条件行：原语类型 + 符号 tag + 计数参数 + 删除"""

    changed = pyqtSignal()
    remove_requested = pyqtSignal(object)

    def __init__(self, kinds: list[str], parent=None):
        super().__init__(parent)
        self._symbols: list[str] = []
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.kind_combo = QComboBox()
        for kind in kinds:
            self.kind_combo.addItem(_KIND_NAMES[kind], kind)
        self.kind_combo.currentIndexChanged.connect(self._on_kind_changed)
        layout.addWidget(self.kind_combo)

        self.symbols_btn = QPushButton("（点击选择符号）")
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
        dlg = SymbolPickerDialog(SYMBOL_ORDER, self._symbols,
                                 "选择条件符号", self)
        if dlg.exec():
            self._symbols = dlg.selected()
            self._update_symbols_text()
            self.changed.emit()

    def _update_symbols_text(self):
        self.symbols_btn.setText(
            "/".join(self._symbols) if self._symbols else "（点击选择符号）")


class ConditionEditor(QWidget):
    """条件列表编辑器（行间 AND 语义，空列表 = 无附加条件）"""

    changed = pyqtSignal()

    def __init__(self, allow_count_min: bool = False, parent=None):
        super().__init__(parent)
        # 顶级条件不提供 count_min；junk_rules 处额外提供
        self._kinds = ["not_contains", "contains_all", "not_together",
                       "count_max"]
        if allow_count_min:
            self._kinds.append("count_min")
        self._rows: list[_ConditionRow] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(QLabel("条件列表（全部满足方成立）："))
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
        row = _ConditionRow(self._kinds)
        row.changed.connect(self.changed)
        row.remove_requested.connect(self._remove_row)
        self._rows.append(row)
        self._rows_layout.addWidget(row)
        return row

    def _add_row_clicked(self):
        self._append_row()
        # 新行符号为空，待用户选择后再触发 changed 保存

    def _remove_row(self, row: _ConditionRow):
        if row in self._rows:
            self._rows.remove(row)
            row.setParent(None)
            row.deleteLater()
            self.changed.emit()
