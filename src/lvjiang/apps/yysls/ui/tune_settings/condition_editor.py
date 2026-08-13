"""条件受限构造器与词条选择对话框

ConditionEditor：垂直行式条件列表（行间 AND 语义），每行由
原语类型下拉（4 原语）+ 词条 tag 区 + 计数参数 + 含首词条勾选
组成，产出/回填规则 YAML 的条件原语原始 dict。

ConditionGroupsEditor：条件组列表（组间 OR、组内 AND，组可绑定
开关前提 when），四档判定条件（junk/normal/excellent/top）共用；
无 when 时单条件组产出单键 dict、多条件组产出原语 dict 列表，
带 when 时产出 {when: {开关key: bool}, all: [...]}
（与 parsing._parse_condition_groups 三种形态对应）。
候选词条为标准词条全集，由构造方注入；开关候选来自
 tune_config 开关注册表。
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from .....i18n import tr
from .affix_picker import AffixSelectSortDialog

# 原语类型 → 显示名（4 原语，include_first 全原语可勾）
_KIND_NAMES = {
    "contains_all": tr("全部同时出现"),
    "not_together": tr("没有全部出现"),
    "count_max": tr("计数小于等于"),
    "count_min": tr("计数大于等于"),
}  # runtime tr()


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
            self.kind_combo.addItem(tr(_KIND_NAMES[kind]), kind)
        self.kind_combo.currentIndexChanged.connect(self._on_kind_changed)
        layout.addWidget(self.kind_combo)

        self.symbols_btn = QPushButton(tr("（点击选择词条）"))
        self.symbols_btn.clicked.connect(self._pick_symbols)
        layout.addWidget(self.symbols_btn, 1)

        self.count_spin = QSpinBox()
        self.count_spin.setRange(0, 5)
        self.count_spin.valueChanged.connect(self.changed)
        layout.addWidget(self.count_spin)

        self.first_check = QCheckBox(tr("含首词条"))
        self.first_check.stateChanged.connect(self.changed)
        layout.addWidget(self.first_check)

        btn_del = QPushButton(tr("删除"))
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
        include_first = False
        if kind in ("count_max", "count_min"):
            args = args or {}
            self._symbols = list(args.get("symbols") or [])
            include_first = bool(args.get("include_first"))
            self.count_spin.blockSignals(True)
            self.count_spin.setValue(
                int(args.get("max" if kind == "count_max" else "min", 1)))
            self.count_spin.blockSignals(False)
        elif isinstance(args, dict):
            # 集合式原语的 dict 形态（symbols + include_first）
            self._symbols = list(args.get("symbols") or [])
            include_first = bool(args.get("include_first"))
        else:
            self._symbols = list(args or [])
        self.first_check.blockSignals(True)
        self.first_check.setChecked(include_first)
        self.first_check.blockSignals(False)
        self._update_symbols_text()
        self._update_visibility()

    def get_condition(self) -> dict:
        kind = self.kind_combo.currentData()
        if kind in ("count_max", "count_min"):
            args: dict = {"symbols": list(self._symbols),
                          ("max" if kind == "count_max" else "min"):
                              self.count_spin.value()}
            if self.first_check.isChecked():
                args["include_first"] = True
            return {kind: args}
        # 集合式原语：含首词条时用 dict 形态，否则保持简洁 list
        if self.first_check.isChecked():
            return {kind: {"symbols": list(self._symbols),
                           "include_first": True}}
        return {kind: list(self._symbols)}

    # ── 内部 ──

    def _on_kind_changed(self):
        self._update_visibility()
        self.changed.emit()

    def _update_visibility(self):
        # 计数参数仅计数原语可见；含首词条全原语可勾
        is_count = self.kind_combo.currentData() in ("count_max", "count_min")
        self.count_spin.setVisible(is_count)

    def _pick_symbols(self):
        dlg = AffixSelectSortDialog(self._candidates, self._symbols,
                                    tr("选择条件词条"), self, flat=True)
        if dlg.exec():
            self._symbols = dlg.selected()
            self._update_symbols_text()
            self.changed.emit()

    def _update_symbols_text(self):
        self.symbols_btn.setText(
            "/".join(self._symbols) if self._symbols else tr("（点击选择词条）"))


class ConditionEditor(QWidget):
    """条件列表编辑器（行间 AND 语义，空列表 = 无附加条件）"""

    changed = pyqtSignal()

    def __init__(self, candidates: list[str],
                 label: str | None = None,
                 parent=None):
        super().__init__(parent)
        self._candidates = candidates
        self._kinds = ["contains_all", "not_together",
                       "count_max", "count_min"]
        self._rows: list[_ConditionRow] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        if label:
            layout.addWidget(QLabel(label))
        self._rows_layout = QVBoxLayout()
        self._rows_layout.setSpacing(2)
        layout.addLayout(self._rows_layout)

        btn_add = QPushButton("+ " + tr("添加条件"))
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
    """单条件组：开关前提（when，可空 = 恒生效）+ 组内条件列表（AND）
    + 删除组"""

    changed = pyqtSignal()
    remove_requested = pyqtSignal(object)

    def __init__(self, candidates: list[str], switch_keys: list[str],
                 parent=None):
        super().__init__(tr("条件组（组内全部满足方命中）"), parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        # 开关前提：绑定开关 key + 期望值，不绑定 = 恒生效
        when_row = QHBoxLayout()
        when_row.addWidget(QLabel(tr("开关前提：")))
        self.when_combo = QComboBox()
        self.when_combo.addItem(tr("（无，恒生效）"), "")
        for key in switch_keys:
            self.when_combo.addItem(key, key)
        self.when_combo.currentIndexChanged.connect(self._on_when_changed)
        when_row.addWidget(self.when_combo)
        self.expect_combo = QComboBox()
        self.expect_combo.addItem(tr("开启时生效"), True)
        self.expect_combo.addItem(tr("关闭时生效"), False)
        self.expect_combo.currentIndexChanged.connect(
            lambda _i: self.changed.emit())
        self.expect_combo.setVisible(False)
        when_row.addWidget(self.expect_combo)
        when_row.addStretch()
        layout.addLayout(when_row)
        self.editor = ConditionEditor(candidates, label=None)
        self.editor.changed.connect(self.changed)
        layout.addWidget(self.editor)
        btn_del = QPushButton(tr("删除本组"))
        btn_del.setFixedWidth(70)
        btn_del.clicked.connect(lambda: self.remove_requested.emit(self))
        layout.addWidget(btn_del, alignment=Qt.AlignmentFlag.AlignRight)

    # ── when 往返（UI 约束单开关绑定，与现有规则形态一致）──

    def set_when(self, raw_when: dict):
        key, expected = "", True
        if raw_when:
            key = str(next(iter(raw_when)))
            expected = bool(raw_when[key])
        idx = self.when_combo.findData(key)
        if idx < 0:  # 注册表缺失的 key 保留展示便于改正
            self.when_combo.addItem(key, key)
            idx = self.when_combo.count() - 1
        self.when_combo.blockSignals(True)
        self.when_combo.setCurrentIndex(idx)
        self.when_combo.blockSignals(False)
        self.expect_combo.blockSignals(True)
        self.expect_combo.setCurrentIndex(0 if expected else 1)
        self.expect_combo.blockSignals(False)
        self.expect_combo.setVisible(bool(key))

    def get_when(self) -> dict:
        key = self.when_combo.currentData()
        if not key:
            return {}
        return {key: bool(self.expect_combo.currentData())}

    def _on_when_changed(self):
        self.expect_combo.setVisible(bool(self.when_combo.currentData()))
        self.changed.emit()


class ConditionGroupsEditor(QWidget):
    """条件组列表编辑器（组间 OR、组内 AND，空列表 = 该档不触发）

    与规则 YAML 四档条件语法往返：单键 dict 视作单条件组，
    list 为组内 AND，{when, all} 为带开关前提组；产出时无 when 的
    单条件组压回单键 dict、带 when 的组始终产出 {when, all}。
    """

    changed = pyqtSignal()

    def __init__(self, candidates: list[str], parent=None):
        super().__init__(parent)
        self._candidates = candidates
        # 开关候选来自注册表（加载失败时退化为无候选，仅影响新绑）
        try:
            from lvjiang.apps.yysls.evaluator.tuning_rules import get_tune_config
            self._switch_keys = list(get_tune_config().switches)
        except Exception:  # noqa: BLE001
            self._switch_keys = []
        self._groups: list[_ConditionGroupBox] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._groups_layout = QVBoxLayout()
        self._groups_layout.setSpacing(4)
        layout.addLayout(self._groups_layout)

        btn_add = QPushButton("+ " + tr("添加条件组"))
        btn_add.clicked.connect(self._add_group_clicked)
        layout.addWidget(btn_add, alignment=Qt.AlignmentFlag.AlignLeft)

    # ── 数据往返 ──

    def set_groups(self, raw_list: list):
        for grp in self._groups:
            grp.setParent(None)
            grp.deleteLater()
        self._groups.clear()
        for raw in raw_list or []:
            when: dict = {}
            if isinstance(raw, dict) and ("when" in raw or "all" in raw):
                when = dict(raw.get("when") or {})
                raw = list(raw.get("all") or [])
            elif isinstance(raw, dict):
                raw = [raw]
            if isinstance(raw, list):
                grp = self._append_group()
                grp.set_when(when)
                grp.editor.set_conditions(raw)

    def get_groups(self) -> list:
        result = []
        for grp in self._groups:
            conds = grp.editor.get_conditions()
            if not conds:
                continue  # 空组不产出（避免意外的永真/永假组）
            when = grp.get_when()
            if when:
                result.append({"when": when, "all": conds})
            else:
                result.append(conds[0] if len(conds) == 1 else conds)  # type: ignore[arg-type]
        return result

    # ── 内部 ──

    def _append_group(self) -> _ConditionGroupBox:
        grp = _ConditionGroupBox(self._candidates, self._switch_keys)
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
