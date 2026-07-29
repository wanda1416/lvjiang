"""词条库设置页（规则顶层）

转律词条库、可用词条库均为「已选词条纯展示 + 编辑
（AffixSelectSortDialog）」，候选为标准词条全集。选择与排序
均在对话框内完成。编辑共享 raw dict 顶层字段，变更即回调保存。
"""

from __future__ import annotations

from typing import Callable

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QAbstractItemView, QGroupBox, QHBoxLayout, QLabel, QListWidget,
    QPushButton, QVBoxLayout, QWidget,
)

from .affix_picker import AffixSelectSortDialog


class _AffixListBox(QWidget):
    """已选词条纯展示 + 编辑（选择与排序均在对话框内完成）"""

    def __init__(self, candidates: list[str],
                 on_changed: Callable[[], None],
                 title: str, rows: int = 7, parent=None):
        super().__init__(parent)
        self._candidates = candidates
        self._on_changed = on_changed
        self._title = title

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        # 纯展示：禁用选中/编辑交互
        self._list = QListWidget()
        self._list.setSelectionMode(
            QAbstractItemView.SelectionMode.NoSelection)
        self._list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        # 展示高度按行数折算固定，超出滚动
        row_h = self._list.fontMetrics().height() + 4
        self._list.setFixedHeight(
            rows * row_h + 2 * self._list.frameWidth())
        layout.addWidget(self._list, 1)

        btn_col = QVBoxLayout()
        btn_edit = QPushButton("编辑")
        btn_edit.clicked.connect(self._edit)
        btn_col.addWidget(btn_edit)
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

    def _edit(self):
        dlg = AffixSelectSortDialog(self._candidates, self.get_names(),
                                    f"选择{self._title}词条", self)
        if dlg.exec():
            # 对话框内已完成选择与拖拽排序，直接采用其返回顺序写回
            self._list.clear()
            self._list.addItems(dlg.selected())
            self._on_changed()


class PoolPage(QWidget):
    """词条库设置页"""

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

        # ── 转律词条库 ──
        prio_box = QGroupBox("转律词条库（全局，优先级从高到低）")
        prio_layout = QVBoxLayout(prio_box)
        self._prio_list = _AffixListBox(
            self._candidates, self._apply, "转律词条库", rows=7)
        prio_layout.addWidget(self._prio_list)
        layout.addWidget(prio_box)

        # ── 可用词条库 ──
        pool_box = QGroupBox("可用词条库（全局，各部位词条混放）")
        pool_layout = QVBoxLayout(pool_box)
        self._pool_list = _AffixListBox(
            self._candidates, self._apply, "可用词条库", rows=10)
        pool_layout.addWidget(self._pool_list)
        note = QLabel(
            "可用词条库为全局价值序（越靠前越优先保留与填充）；"
            "武学增伤不在此填写——玩法指定武器的武学增伤自动视为"
            "最高优先级，未指定时按垃圾词条处理。")
        note.setWordWrap(True)
        note.setStyleSheet("color: gray; font-size: 12px;")
        pool_layout.addWidget(note)
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
