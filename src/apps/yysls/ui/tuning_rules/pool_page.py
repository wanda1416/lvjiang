"""转律与词条库页（规则顶层）

转律优先级（可排序列表）、可用词条库均为
「已选词条列表 + 添加（AffixPickerDialog）/移除」，候选为
标准词条全集。编辑共享 raw dict 顶层字段，变更即回调保存。
"""

from __future__ import annotations

from typing import Callable

from PyQt6.QtWidgets import (
    QGroupBox, QHBoxLayout, QListWidget,
    QPushButton, QVBoxLayout, QWidget,
)

from .condition_editor import AffixPickerDialog


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
        pool_box = QGroupBox("可用词条库（全局，各部位词条混放）")
        pool_layout = QVBoxLayout(pool_box)
        self._pool_list = _AffixListBox(
            self._candidates, self._apply, "可用词条库")
        pool_layout.addWidget(self._pool_list)
        layout.addWidget(pool_box)
        layout.addStretch()

    # ── 数据往返 ──

    def load(self, data: dict):
        """回填规则顶层 raw dict 引用"""
        self._loading = True
        self._data = data
        self._prio_list.set_names(list(data.get("transmute_priority") or []))
        self._pool_list.set_names(list(data.get("affix_pool") or []))
        self._loading = False

    def _apply(self):
        if self._loading:
            return
        d = self._data
        d["transmute_priority"] = self._prio_list.get_names()
        d["affix_pool"] = self._pool_list.get_names()
        self._on_changed()
