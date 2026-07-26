"""转律与词条库页（变体级）

转律优先级（可排序列表）、可用词条库（符号 checkbox 网格）、
可选槽词条库（可选限定）、流派附加垃圾规则（ConditionEditor）。
编辑共享 raw dict 的 variants.<key> 子树，变更即回调保存。
"""

from __future__ import annotations

from typing import Callable

from PyQt6.QtWidgets import (
    QCheckBox, QGridLayout, QGroupBox, QHBoxLayout, QListWidget,
    QPushButton, QVBoxLayout, QWidget,
)

from .condition_editor import SYMBOL_ORDER, ConditionEditor, SymbolPickerDialog


class _SymbolGrid(QWidget):
    """符号 checkbox 网格（词汇表全集）"""

    def __init__(self, on_changed: Callable[[], None], parent=None):
        super().__init__(parent)
        self._on_changed = on_changed
        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        self._checks: dict[str, QCheckBox] = {}
        for i, name in enumerate(SYMBOL_ORDER):
            cb = QCheckBox(name)
            cb.stateChanged.connect(lambda *_: self._on_changed())
            grid.addWidget(cb, i // 6, i % 6)
            self._checks[name] = cb

    def set_symbols(self, symbols: list[str]):
        chosen = set(symbols or [])
        for name, cb in self._checks.items():
            cb.blockSignals(True)
            cb.setChecked(name in chosen)
            cb.blockSignals(False)

    def get_symbols(self) -> list[str]:
        """按词汇表顺序返回勾选符号"""
        return [n for n, cb in self._checks.items() if cb.isChecked()]


class VariantPoolPage(QWidget):
    """转律与词条库页"""

    def __init__(self, on_changed: Callable[[], None], parent=None):
        super().__init__(parent)
        self._on_changed = on_changed
        self._variant: dict = {}
        self._loading = True
        self._init_ui()
        self._loading = False

    def _init_ui(self):
        layout = QVBoxLayout(self)

        # ── 转律优先级 ──
        prio_box = QGroupBox("转律优先级（从高到低）")
        prio_layout = QHBoxLayout(prio_box)
        self._prio_list = QListWidget()
        prio_layout.addWidget(self._prio_list, 1)
        btn_col = QVBoxLayout()
        for label, slot in [("上移", self._move_up), ("下移", self._move_down),
                            ("添加", self._add_symbol), ("移除", self._remove_symbol)]:
            btn = QPushButton(label)
            btn.clicked.connect(slot)
            btn_col.addWidget(btn)
        btn_col.addStretch()
        prio_layout.addLayout(btn_col)
        layout.addWidget(prio_box)

        # ── 可用词条库 ──
        pool_box = QGroupBox("可用词条库")
        pool_layout = QVBoxLayout(pool_box)
        self._pool_grid = _SymbolGrid(self._apply)
        pool_layout.addWidget(self._pool_grid)
        layout.addWidget(pool_box)

        # ── 可选槽词条库（可选限定） ──
        opt_box = QGroupBox("可选槽词条库")
        opt_layout = QVBoxLayout(opt_box)
        self._opt_check = QCheckBox("限定可选槽候选（不勾选 = 同可用词条库）")
        self._opt_check.stateChanged.connect(self._on_opt_toggled)
        opt_layout.addWidget(self._opt_check)
        self._opt_grid = _SymbolGrid(self._apply)
        opt_layout.addWidget(self._opt_grid)
        layout.addWidget(opt_box)

        # ── 附加垃圾规则 ──
        junk_box = QGroupBox("流派附加垃圾规则（触发任一即判垃圾）")
        junk_layout = QVBoxLayout(junk_box)
        self._junk_editor = ConditionEditor(allow_count_min=True)
        self._junk_editor.changed.connect(self._apply)
        junk_layout.addWidget(self._junk_editor)
        layout.addWidget(junk_box)
        layout.addStretch()

    # ── 数据往返 ──

    def load(self, variant: dict):
        """回填变体子树（variants.<key> 的 raw dict 引用）"""
        self._loading = True
        self._variant = variant
        self._prio_list.clear()
        self._prio_list.addItems(list(variant.get("transmute_priority") or []))
        self._pool_grid.set_symbols(list(variant.get("affix_pool") or []))
        optional_pool = variant.get("optional_pool")
        self._opt_check.blockSignals(True)
        self._opt_check.setChecked(optional_pool is not None)
        self._opt_check.blockSignals(False)
        self._opt_grid.setEnabled(optional_pool is not None)
        self._opt_grid.set_symbols(list(optional_pool or []))
        self._junk_editor.set_conditions(list(variant.get("junk_rules") or []))
        self._loading = False

    def _apply(self):
        if self._loading:
            return
        v = self._variant
        v["transmute_priority"] = [
            self._prio_list.item(i).text()
            for i in range(self._prio_list.count())]
        v["affix_pool"] = self._pool_grid.get_symbols()
        if self._opt_check.isChecked():
            v["optional_pool"] = self._opt_grid.get_symbols()
        else:
            v.pop("optional_pool", None)
        v["junk_rules"] = self._junk_editor.get_conditions()
        self._on_changed()

    # ── 优先级列表操作 ──

    def _move_up(self):
        self._move(-1)

    def _move_down(self):
        self._move(1)

    def _move(self, delta: int):
        row = self._prio_list.currentRow()
        target = row + delta
        if row < 0 or not (0 <= target < self._prio_list.count()):
            return
        item = self._prio_list.takeItem(row)
        self._prio_list.insertItem(target, item)
        self._prio_list.setCurrentRow(target)
        self._apply()

    def _add_symbol(self):
        current = [self._prio_list.item(i).text()
                   for i in range(self._prio_list.count())]
        dlg = SymbolPickerDialog(SYMBOL_ORDER, current, "选择转律优先级符号", self)
        if dlg.exec():
            chosen = dlg.selected()
            # 保留原有顺序，新勾选的追加在末尾
            merged = [s for s in current if s in chosen]
            merged += [s for s in chosen if s not in merged]
            self._prio_list.clear()
            self._prio_list.addItems(merged)
            self._apply()

    def _remove_symbol(self):
        row = self._prio_list.currentRow()
        if row >= 0:
            self._prio_list.takeItem(row)
            self._apply()

    def _on_opt_toggled(self):
        self._opt_grid.setEnabled(self._opt_check.isChecked())
        if self._opt_check.isChecked() and not self._opt_grid.get_symbols():
            # 初次开启时默认继承可用词条库
            self._opt_grid.set_symbols(self._pool_grid.get_symbols())
        self._apply()
