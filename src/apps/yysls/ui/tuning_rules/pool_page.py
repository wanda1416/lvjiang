"""转律与词条库页（规则顶层）

转律优先级（可排序列表）、可用词条库、可选槽词条库均为
「已选词条列表 + 添加（AffixPickerDialog）/移除」，候选为
标准词条全集；流派附加垃圾规则（ConditionEditor）。
编辑共享 raw dict 顶层字段，变更即回调保存。
"""

from __future__ import annotations

from typing import Callable

from PyQt6.QtWidgets import (
    QCheckBox, QGroupBox, QHBoxLayout, QListWidget,
    QPushButton, QVBoxLayout, QWidget,
)

from .condition_editor import AffixPickerDialog, ConditionEditor


class _AffixListBox(QWidget):
    """已选词条列表 + 添加/移除（可选 上移/下移）"""

    def __init__(self, candidates: list[str],
                 on_changed: Callable[[], None],
                 title: str, sortable: bool = False, parent=None):
        super().__init__(parent)
        self._candidates = candidates
        self._on_changed = on_changed
        self._title = title

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._list = QListWidget()
        layout.addWidget(self._list, 1)

        btn_col = QVBoxLayout()
        actions = [("上移", self._move_up), ("下移", self._move_down)] \
            if sortable else []
        actions += [("添加", self._add), ("移除", self._remove)]
        for label, slot in actions:
            btn = QPushButton(label)
            btn.clicked.connect(slot)
            btn_col.addWidget(btn)
        btn_col.addStretch()
        layout.addLayout(btn_col)

    # ── 数据往返 ──

    def set_names(self, names: list[str]):
        self._list.clear()
        self._list.addItems(list(names or []))

    def get_names(self) -> list[str]:
        return [self._list.item(i).text()
                for i in range(self._list.count())]

    # ── 操作 ──

    def _add(self):
        current = self.get_names()
        dlg = AffixPickerDialog(self._candidates, current,
                                f"选择{self._title}词条", self)
        if dlg.exec():
            chosen = dlg.selected()
            # 保留原有顺序，新勾选的追加在末尾
            merged = [s for s in current if s in chosen]
            merged += [s for s in chosen if s not in merged]
            self._list.clear()
            self._list.addItems(merged)
            self._on_changed()

    def _remove(self):
        row = self._list.currentRow()
        if row >= 0:
            self._list.takeItem(row)
            self._on_changed()

    def _move_up(self):
        self._move(-1)

    def _move_down(self):
        self._move(1)

    def _move(self, delta: int):
        row = self._list.currentRow()
        target = row + delta
        if row < 0 or not (0 <= target < self._list.count()):
            return
        item = self._list.takeItem(row)
        self._list.insertItem(target, item)
        self._list.setCurrentRow(target)
        self._on_changed()


class PoolPage(QWidget):
    """转律与词条库页"""

    def __init__(self, candidates: list[str],
                 on_changed: Callable[[], None], parent=None):
        super().__init__(parent)
        self._candidates = candidates
        self._on_changed = on_changed
        self._data: dict = {}
        self._loading = True
        self._init_ui()
        self._loading = False

    def _init_ui(self):
        layout = QVBoxLayout(self)

        # ── 转律优先级 ──
        prio_box = QGroupBox("转律优先级（从高到低）")
        prio_layout = QVBoxLayout(prio_box)
        self._prio_list = _AffixListBox(
            self._candidates, self._apply, "转律优先级", sortable=True)
        prio_layout.addWidget(self._prio_list)
        layout.addWidget(prio_box)

        # ── 可用词条库 ──
        pool_box = QGroupBox("可用词条库")
        pool_layout = QVBoxLayout(pool_box)
        self._pool_list = _AffixListBox(
            self._candidates, self._apply, "可用词条库")
        pool_layout.addWidget(self._pool_list)
        layout.addWidget(pool_box)

        # ── 可选槽词条库（可选限定） ──
        opt_box = QGroupBox("可选槽词条库")
        opt_layout = QVBoxLayout(opt_box)
        self._opt_check = QCheckBox("限定可选槽候选（不勾选 = 同可用词条库）")
        self._opt_check.stateChanged.connect(self._on_opt_toggled)
        opt_layout.addWidget(self._opt_check)
        self._opt_list = _AffixListBox(
            self._candidates, self._apply, "可选槽词条库")
        opt_layout.addWidget(self._opt_list)
        layout.addWidget(opt_box)

        # ── 附加垃圾规则 ──
        junk_box = QGroupBox("流派附加垃圾规则（触发任一即判垃圾）")
        junk_layout = QVBoxLayout(junk_box)
        self._junk_editor = ConditionEditor(
            self._candidates, allow_count_min=True)
        self._junk_editor.changed.connect(self._apply)
        junk_layout.addWidget(self._junk_editor)
        layout.addWidget(junk_box)
        layout.addStretch()

    # ── 数据往返 ──

    def load(self, data: dict):
        """回填规则顶层 raw dict 引用"""
        self._loading = True
        self._data = data
        self._prio_list.set_names(list(data.get("transmute_priority") or []))
        self._pool_list.set_names(list(data.get("affix_pool") or []))
        optional_pool = data.get("optional_pool")
        self._opt_check.blockSignals(True)
        self._opt_check.setChecked(optional_pool is not None)
        self._opt_check.blockSignals(False)
        self._opt_list.setEnabled(optional_pool is not None)
        self._opt_list.set_names(list(optional_pool or []))
        self._junk_editor.set_conditions(list(data.get("junk_rules") or []))
        self._loading = False

    def _apply(self):
        if self._loading:
            return
        d = self._data
        d["transmute_priority"] = self._prio_list.get_names()
        d["affix_pool"] = self._pool_list.get_names()
        if self._opt_check.isChecked():
            d["optional_pool"] = self._opt_list.get_names()
        else:
            d.pop("optional_pool", None)
        d["junk_rules"] = self._junk_editor.get_conditions()
        self._on_changed()

    def _on_opt_toggled(self):
        self._opt_list.setEnabled(self._opt_check.isChecked())
        if self._opt_check.isChecked() and not self._opt_list.get_names():
            # 初次开启时默认继承可用词条库
            self._opt_list.set_names(self._pool_list.get_names())
        self._apply()
