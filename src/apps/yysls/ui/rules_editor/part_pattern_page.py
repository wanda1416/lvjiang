"""部位模式编辑页（规则编辑器，每部位一页）

自上而下：首词条（提示 icon 点开说明 + 词条按钮点击编辑，
首词条为空 = 本部位不参与判定）、默认判定（空 = 跟随规则设置）、
四档判定条件 Tab：垃圾 / 一般 / 优秀 / 顶级（各为条件组列表，
组间 OR、组内 AND，组可绑定开关前提 when）。
判定顺序 junk → normal → excellent → top，全不命中取默认判定
（部位级优先，缺省跟随规则设置页）；槽位全推导，无必选/可选槽
声明。候选为标准词条全集。
编辑共享 raw dict 顶层的 patterns.<part> 子树。
"""

from __future__ import annotations

from typing import Callable

from PyQt6.QtGui import QCursor
from PyQt6.QtWidgets import (
    QHBoxLayout, QLabel, QPushButton, QTabWidget, QToolButton, QToolTip,
    QVBoxLayout, QWidget,
)

from src.apps.yysls.evaluator.tuning_rules import RATING_KEYS, RATING_LABELS
from src.ui.widgets import NoWheelComboBox

from .affix_picker import AffixSelectSortDialog
from .condition_editor import ConditionGroupsEditor

# 四档条件：(YAML 键, Tab 标题)，判定顺序即列表顺序
_TIERS: list[tuple[str, str]] = [
    ("junk_conditions", "垃圾"),
    ("normal_conditions", "一般"),
    ("excellent_conditions", "优秀"),
    ("top_conditions", "顶级"),
]

_FIRST_TIPS = (
    "首词条：装备第 1 条词条须在候选之内（任一符合即可），\n"
    "不符合 → 本部位跳过判定。\n"
    "首词条为空 = 本部位不定义模式，不参与判定。"
)


class PartPatternPage(QWidget):
    """单部位模式页"""

    def __init__(self, part_key: str, title: str, candidates: list[str],
                 on_changed: Callable[[], None], parent=None):
        super().__init__(parent)
        self._part_key = part_key
        self._candidates = candidates
        self._on_changed = on_changed
        self._data: dict = {}
        self._loading = True
        self._init_ui()
        self._loading = False

    def _init_ui(self):
        layout = QVBoxLayout(self)

        # ① 首词条：提示 icon 点开说明，词条按钮点击进入编辑
        first_row = QHBoxLayout()
        first_row.addWidget(QLabel("<b>首词条</b>"))
        tips_btn = QToolButton()
        tips_btn.setText("ⓘ")
        tips_btn.setAutoRaise(True)
        tips_btn.setToolTip(_FIRST_TIPS)
        tips_btn.clicked.connect(
            lambda: QToolTip.showText(QCursor.pos(), _FIRST_TIPS))
        first_row.addWidget(tips_btn)
        first_row.addStretch()
        layout.addLayout(first_row)

        self._first: list[str] = []
        self._first_btn = QPushButton("（点击选择首词条）")
        self._first_btn.clicked.connect(self._pick_first)
        layout.addWidget(self._first_btn)

        # ② 默认判定：全档不命中时的兜底档位（空 = 跟随规则设置页）
        rating_row = QHBoxLayout()
        rating_row.addWidget(QLabel("默认判定："))
        self._rating_combo = NoWheelComboBox()
        self._rating_combo.addItem("（跟随规则设置）", "")
        for rating_key in RATING_KEYS:
            self._rating_combo.addItem(RATING_LABELS[rating_key], rating_key)
        self._rating_combo.currentIndexChanged.connect(
            lambda _i: self._apply())
        rating_row.addWidget(self._rating_combo)
        rating_row.addStretch()
        layout.addLayout(rating_row)

        # ③ 四档判定条件 Tab（顺序 junk → … → top）
        layout.addWidget(QLabel("<b>判定条件</b>"))
        self._tabs = QTabWidget()
        layout.addWidget(self._tabs)
        self._tier_editors: dict[str, ConditionGroupsEditor] = {}
        for tier_key, tier_name in _TIERS:
            page = QWidget()
            page_layout = QVBoxLayout(page)
            editor = ConditionGroupsEditor(self._candidates)
            editor.changed.connect(self._apply)
            page_layout.addWidget(editor)
            page_layout.addStretch()
            self._tabs.addTab(page, tier_name)
            self._tier_editors[tier_key] = editor
        layout.addStretch()

    # ── 数据往返 ──

    def load(self, data: dict):
        """回填规则顶层 raw dict 引用"""
        self._loading = True
        self._data = data
        pattern = (data.get("patterns") or {}).get(self._part_key) or {}

        self._first = list(pattern.get("first") or [])
        self._update_first_text()

        idx = self._rating_combo.findData(
            str(pattern.get("default_rating") or ""))
        self._rating_combo.setCurrentIndex(max(idx, 0))

        for tier_key, editor in self._tier_editors.items():
            editor.set_groups(list(pattern.get(tier_key) or []))
        self._set_editing_enabled(bool(self._first))
        self._loading = False

    def _apply(self):
        if self._loading:
            return
        patterns = self._data.setdefault("patterns", {})
        if not self._first:
            # 首词条为空 = 本部位不定义模式，不参与判定
            patterns.pop(self._part_key, None)
            self._set_editing_enabled(False)
            self._on_changed()
            return
        self._set_editing_enabled(True)
        pattern: dict = {"first": list(self._first)}
        rating = self._rating_combo.currentData()
        if rating:
            pattern["default_rating"] = rating
        for tier_key, editor in self._tier_editors.items():
            groups = editor.get_groups()
            if groups:
                pattern[tier_key] = groups
        patterns[self._part_key] = pattern
        self._on_changed()

    # ── 首词条 ──

    def _pick_first(self):
        dlg = AffixSelectSortDialog(self._candidates, self._first,
                                    "选择首词条候选", self)
        if dlg.exec():
            self._first = dlg.selected()
            self._update_first_text()
            self._apply()

    def _update_first_text(self):
        self._first_btn.setText(
            "/".join(self._first) if self._first else "（点击选择首词条）")

    # ── 其他 ──

    def _set_editing_enabled(self, enabled: bool):
        """首词条为空时锁定默认判定与条件编辑区"""
        self._rating_combo.setEnabled(enabled)
        self._tabs.setEnabled(enabled)
