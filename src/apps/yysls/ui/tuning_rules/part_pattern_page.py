"""部位模式页（规则顶层，每部位一页同构）

自上而下：模式摘要行（00 文档语法实时渲染）、首词条选择行、
必选槽列表（双击弹词条选择对话框）、增伤要求、PVP 行（仅
keep_pvp 规则）、可选槽数、顶级判定条件。候选为标准词条全集
（槽位与增伤处额外提供 DMG 占位符）。
编辑共享 raw dict 顶层的 patterns.<part> 子树。
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
    DMG_PLACEHOLDER, AffixPickerDialog, ConditionEditor,
)


class PartPatternPage(QWidget):
    """单部位模式页"""

    def __init__(self, part_key: str, title: str, candidates: list[str],
                 on_changed: Callable[[], None], has_keep_pvp: bool,
                 parent=None):
        super().__init__(parent)
        self._part_key = part_key
        self._candidates = candidates
        # 必选槽候选：DMG 占位符 + 标准词条全集
        self._slot_candidates = [DMG_PLACEHOLDER] + candidates
        self._on_changed = on_changed
        self._has_keep_pvp = has_keep_pvp
        self._data: dict = {}
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

        # ② 首词条（点击选择，候选为标准词条全集）
        first_box = QGroupBox("首词条（任一符合即可）")
        first_layout = QHBoxLayout(first_box)
        self._first: list[str] = []
        self._first_btn = QPushButton("（点击选择）")
        self._first_btn.clicked.connect(self._pick_first)
        first_layout.addWidget(self._first_btn, 1)
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
        self._damage_combo.addItems(
            ["（无）", DMG_PLACEHOLDER] + self._candidates)
        self._damage_combo.currentIndexChanged.connect(self._apply)
        form.addRow("增伤要求（缺失即垃圾）：", self._damage_combo)

        # ⑤ PVP 行（仅 keep_pvp 规则显示）
        self._pvp_sub_combo = QComboBox()
        self._pvp_sub_combo.addItems(["（无）"] + self._candidates)
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
        self._top_editor = ConditionEditor(
            self._candidates, allow_count_min=False)
        self._top_editor.changed.connect(self._apply)
        top_layout.addWidget(self._top_editor)
        body_layout.addWidget(top_box)
        layout.addStretch()

    # ── 数据往返 ──

    def load(self, data: dict):
        """回填规则顶层 raw dict 引用"""
        self._loading = True
        self._data = data
        pattern = (data.get("patterns") or {}).get(self._part_key)
        enabled = pattern is not None
        self._enable_check.blockSignals(True)
        self._enable_check.setChecked(enabled)
        self._enable_check.blockSignals(False)
        self._body.setEnabled(enabled)
        pattern = pattern or {}

        self._first = list(pattern.get("first") or [])
        self._update_first_text()

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
        patterns = self._data.setdefault("patterns", {})
        if not self._enable_check.isChecked():
            patterns.pop(self._part_key, None)
            self._update_summary()
            self._on_changed()
            return
        pattern: dict = {
            "first": list(self._first),
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

    # ── 首词条 ──

    def _pick_first(self):
        dlg = AffixPickerDialog(self._candidates, self._first,
                                "选择首词条候选", self)
        if dlg.exec():
            self._first = dlg.selected()
            self._update_first_text()
            self._apply()

    def _update_first_text(self):
        self._first_btn.setText(
            "/".join(self._first) if self._first else "（点击选择）")

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
        dlg = AffixPickerDialog(self._slot_candidates, current,
                                "选择槽位候选", self)
        if dlg.exec():
            chosen = dlg.selected()
            item.setData(Qt.ItemDataRole.UserRole, chosen)
            item.setText("/".join(chosen) if chosen else "（空槽）")
            self._apply()

    def _add_slot(self):
        dlg = AffixPickerDialog(self._slot_candidates, [],
                                "选择槽位候选", self)
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
        dlg = AffixPickerDialog(self._candidates, self._pvp_divine,
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
        [最大外功攻击(首) + DMG + 最大外功攻击] + 可用词条库 * 2"""
        if not self._enable_check.isChecked():
            self._summary_label.setText("（该部位未定义模式，不参与判定）")
            return
        first = "/".join(self._first) or "?"
        parts = [f"{first}(首)"]
        for slot in self._collect_slots():
            parts.append("/".join(slot) if slot else "?")
        summary = f"[{' + '.join(parts)}]"
        n = self._optional_spin.value()
        if n:
            summary += f" + 可用词条库 * {n}"
        self._summary_label.setText(summary)
