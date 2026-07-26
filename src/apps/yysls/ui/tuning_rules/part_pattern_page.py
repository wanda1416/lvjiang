"""部位模式页（变体级，每部位一页同构）

自上而下：模式摘要行（00 文档语法实时渲染）、首词条符号行、
必选槽列表（双击弹符号选择对话框）、增伤要求、PVP 行（仅
keep_pvp 流派）、可选槽数、顶级判定条件。
编辑共享 raw dict 的 variants.<key>.patterns.<part> 子树。
"""

from __future__ import annotations

from typing import Callable

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QFormLayout, QGroupBox, QHBoxLayout, QLabel,
    QListWidget, QListWidgetItem, QPushButton, QVBoxLayout, QWidget,
)

from src.ui.widgets import NoWheelSpinBox

from .condition_editor import (
    DIVINE_CANDIDATES, DMG_PLACEHOLDER, SYMBOL_ORDER,
    ConditionEditor, SymbolPickerDialog,
)

# 增伤要求候选（无 / DMG 占位符 / 神力词条全称）
_DAMAGE_OPTIONS = ["（无）", DMG_PLACEHOLDER] + DIVINE_CANDIDATES

# 必选槽候选：符号 + DMG 占位符 + 神力词条
_SLOT_CANDIDATES = SYMBOL_ORDER + [DMG_PLACEHOLDER] + DIVINE_CANDIDATES


class PartPatternPage(QWidget):
    """单部位模式页"""

    def __init__(self, part_key: str, title: str,
                 on_changed: Callable[[], None], has_keep_pvp: bool,
                 parent=None):
        super().__init__(parent)
        self._part_key = part_key
        self._on_changed = on_changed
        self._has_keep_pvp = has_keep_pvp
        self._variant: dict = {}
        self._loading = True
        self._init_ui(title)
        self._loading = False

    def _init_ui(self, title: str):
        layout = QVBoxLayout(self)

        # ① 模式摘要行（只读，实时渲染，便于与 01-05 文档对照）
        self._summary_label = QLabel()
        self._summary_label.setStyleSheet(
            "background: #f0f0f0; padding: 6px; font-weight: bold;")
        self._summary_label.setWordWrap(True)
        layout.addWidget(self._summary_label)

        # 部位未定义模式时的启用开关
        self._enable_check = QCheckBox(f"启用「{title}」模式")
        self._enable_check.stateChanged.connect(self._on_enable_toggled)
        layout.addWidget(self._enable_check)

        self._body = QWidget()
        body_layout = QVBoxLayout(self._body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._body)

        # ② 首词条
        first_box = QGroupBox("首词条（任一符合即可）")
        first_layout = QHBoxLayout(first_box)
        self._first_checks: dict[str, QCheckBox] = {}
        for name in SYMBOL_ORDER:
            cb = QCheckBox(name)
            cb.stateChanged.connect(lambda *_: self._apply())
            first_layout.addWidget(cb)
            self._first_checks[name] = cb
        body_layout.addWidget(first_box)

        # ③ 必选槽列表
        slots_box = QGroupBox("必选槽（每槽候选任一，双击编辑）")
        slots_layout = QHBoxLayout(slots_box)
        self._slots_list = QListWidget()
        self._slots_list.itemDoubleClicked.connect(self._edit_slot)
        slots_layout.addWidget(self._slots_list, 1)
        slot_btns = QVBoxLayout()
        btn_add = QPushButton("添加槽位")
        btn_add.clicked.connect(self._add_slot)
        btn_del = QPushButton("删除槽位")
        btn_del.clicked.connect(self._remove_slot)
        slot_btns.addWidget(btn_add)
        slot_btns.addWidget(btn_del)
        slot_btns.addStretch()
        slots_layout.addLayout(slot_btns)
        body_layout.addWidget(slots_box)

        form = QFormLayout()

        # ④ 增伤要求
        self._damage_combo = QComboBox()
        self._damage_combo.addItems(_DAMAGE_OPTIONS)
        self._damage_combo.currentIndexChanged.connect(self._apply)
        form.addRow("增伤要求（缺失即垃圾）：", self._damage_combo)

        # ⑤ PVP 行（仅 keep_pvp 流派显示）
        self._pvp_sub_combo = QComboBox()
        self._pvp_sub_combo.addItems(["（无）"] + DIVINE_CANDIDATES)
        self._pvp_sub_combo.currentIndexChanged.connect(self._apply)
        self._pvp_divine_btn = QPushButton("（点击选择）")
        self._pvp_divine_btn.clicked.connect(self._pick_pvp_divine)
        self._pvp_divine: list[str] = []
        if self._has_keep_pvp:
            form.addRow("PVP 增伤顶替词条：", self._pvp_sub_combo)
            form.addRow("PVP 允许神力词条：", self._pvp_divine_btn)
        else:
            self._pvp_sub_combo.setVisible(False)
            self._pvp_divine_btn.setVisible(False)

        # ⑥ 可选槽数
        self._optional_spin = NoWheelSpinBox()
        self._optional_spin.setRange(0, 4)
        self._optional_spin.valueChanged.connect(self._apply)
        form.addRow("可选槽数（必选+可选+首 = 5）：", self._optional_spin)
        body_layout.addLayout(form)

        # ⑦ 顶级判定条件
        top_box = QGroupBox("顶级判定条件（命中模式后全部满足 → 顶级，否则优秀）")
        top_layout = QVBoxLayout(top_box)
        self._top_editor = ConditionEditor(allow_count_min=False)
        self._top_editor.changed.connect(self._apply)
        top_layout.addWidget(self._top_editor)
        body_layout.addWidget(top_box)
        layout.addStretch()

    # ── 数据往返 ──

    def load(self, variant: dict):
        """回填变体子树（variants.<key> 的 raw dict 引用）"""
        self._loading = True
        self._variant = variant
        pattern = (variant.get("patterns") or {}).get(self._part_key)
        enabled = pattern is not None
        self._enable_check.blockSignals(True)
        self._enable_check.setChecked(enabled)
        self._enable_check.blockSignals(False)
        self._body.setEnabled(enabled)
        pattern = pattern or {}

        first = set(pattern.get("first") or [])
        for name, cb in self._first_checks.items():
            cb.blockSignals(True)
            cb.setChecked(name in first)
            cb.blockSignals(False)

        self._slots_list.clear()
        for slot in pattern.get("required") or []:
            self._append_slot_item(list(slot))

        damage = pattern.get("required_damage") or "（无）"
        idx = self._damage_combo.findText(damage)
        self._damage_combo.blockSignals(True)
        self._damage_combo.setCurrentIndex(max(idx, 0))
        self._damage_combo.blockSignals(False)

        sub = pattern.get("damage_pvp_substitute") or "（无）"
        idx = self._pvp_sub_combo.findText(sub)
        self._pvp_sub_combo.blockSignals(True)
        self._pvp_sub_combo.setCurrentIndex(max(idx, 0))
        self._pvp_sub_combo.blockSignals(False)
        self._pvp_divine = list(pattern.get("allowed_divine_pvp") or [])
        self._pvp_divine_btn.setText(
            "、".join(self._pvp_divine) if self._pvp_divine else "（点击选择）")

        self._optional_spin.blockSignals(True)
        self._optional_spin.setValue(int(pattern.get("optional_n", 0)))
        self._optional_spin.blockSignals(False)

        self._top_editor.set_conditions(list(pattern.get("top") or []))
        self._update_summary()
        self._loading = False

    def _apply(self):
        if self._loading:
            return
        patterns = self._variant.setdefault("patterns", {})
        if not self._enable_check.isChecked():
            patterns.pop(self._part_key, None)
            self._update_summary()
            self._on_changed()
            return
        pattern: dict = {
            "first": [n for n, cb in self._first_checks.items()
                      if cb.isChecked()],
            "required": self._collect_slots(),
        }
        damage = self._damage_combo.currentText()
        pattern["required_damage"] = None if damage == "（无）" else damage
        if self._has_keep_pvp:
            sub = self._pvp_sub_combo.currentText()
            if sub != "（无）":
                pattern["damage_pvp_substitute"] = sub
            if self._pvp_divine:
                pattern["allowed_divine_pvp"] = list(self._pvp_divine)
        pattern["optional_n"] = self._optional_spin.value()
        pattern["top"] = self._top_editor.get_conditions()
        patterns[self._part_key] = pattern
        self._update_summary()
        self._on_changed()

    # ── 必选槽 ──

    def _append_slot_item(self, symbols: list[str]):
        item = QListWidgetItem("/".join(symbols) if symbols else "（空槽）")
        item.setData(Qt.ItemDataRole.UserRole, symbols)
        self._slots_list.addItem(item)

    def _collect_slots(self) -> list[list[str]]:
        return [self._slots_list.item(i).data(Qt.ItemDataRole.UserRole) or []
                for i in range(self._slots_list.count())]

    def _edit_slot(self, item: QListWidgetItem):
        current = item.data(Qt.ItemDataRole.UserRole) or []
        dlg = SymbolPickerDialog(_SLOT_CANDIDATES, current, "选择槽位候选", self)
        if dlg.exec():
            chosen = dlg.selected()
            item.setData(Qt.ItemDataRole.UserRole, chosen)
            item.setText("/".join(chosen) if chosen else "（空槽）")
            self._apply()

    def _add_slot(self):
        dlg = SymbolPickerDialog(_SLOT_CANDIDATES, [], "选择槽位候选", self)
        if dlg.exec() and dlg.selected():
            self._append_slot_item(dlg.selected())
            self._apply()

    def _remove_slot(self):
        row = self._slots_list.currentRow()
        if row >= 0:
            self._slots_list.takeItem(row)
            self._apply()

    # ── PVP 神力 ──

    def _pick_pvp_divine(self):
        dlg = SymbolPickerDialog(DIVINE_CANDIDATES, self._pvp_divine,
                                 "选择 PVP 允许神力词条", self)
        if dlg.exec():
            self._pvp_divine = dlg.selected()
            self._pvp_divine_btn.setText(
                "、".join(self._pvp_divine) if self._pvp_divine
                else "（点击选择）")
            self._apply()

    # ── 其他 ──

    def _on_enable_toggled(self):
        self._body.setEnabled(self._enable_check.isChecked())
        self._apply()

    def _update_summary(self):
        """用 00 文档语法渲染当前模式，如
        [大外(首) + DMG + 大外] + 可用词条库 * 2"""
        if not self._enable_check.isChecked():
            self._summary_label.setText("（该部位未定义模式，不参与判定）")
            return
        first = "/".join(n for n, cb in self._first_checks.items()
                         if cb.isChecked()) or "?"
        parts = [f"{first}(首)"]
        for slot in self._collect_slots():
            parts.append("/".join(slot) if slot else "?")
        summary = f"[{' + '.join(parts)}]"
        n = self._optional_spin.value()
        if n:
            summary += f" + 可用词条库 * {n}"
        self._summary_label.setText(summary)
