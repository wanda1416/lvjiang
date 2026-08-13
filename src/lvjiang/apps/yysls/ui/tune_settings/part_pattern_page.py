"""部位模式编辑页（规则编辑器，每部位一页）

自上而下：首词条（提示 icon 点开说明 + 词条按钮点击编辑，
首词条为空 = 本部位不参与判定）、默认判定（空 = 跟随规则设置）、
判定条件 Tab：全部 / 垃圾 / 一般 / 优秀 / 顶级（「全部」按判定顺序
纵向铺开四档，各档带配色标头区分；各档为条件组列表，
组间 OR、组内 AND，组可绑定开关前提 when）。
判定顺序 junk → normal → excellent → top，全不命中取默认判定
（部位级优先，缺省跟随规则设置页）；槽位全推导，无必选/可选槽
声明。候选为当前可用词条库（首词条不豁免，池外词条无法参与
判定），选择对话框按归类顺序平铺。
编辑共享 raw dict 顶层的 patterns.<part> 子树。
"""

from __future__ import annotations

from typing import Callable

from PyQt6.QtGui import QCursor
from PyQt6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTabBar,
    QToolButton,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from lvjiang.apps.yysls.evaluator.tuning_rules import RATING_KEYS, RATING_LABELS
from .....i18n import tr

from .affix_picker import AffixSelectSortDialog
from .condition_editor import ConditionGroupsEditor

# 四档条件：(YAML 键, Tab 标题)，判定顺序即列表顺序
_TIERS: list[tuple[str, str]] = [
    ("junk_conditions", "垃圾"),
    ("normal_conditions", "一般"),
    ("excellent_conditions", "优秀"),
    ("top_conditions", "顶级"),
]  # runtime tr()

# 档位标头配色（浅色背景可见，与品质/词条分级配色一致）
_TIER_COLORS = {
    "junk_conditions": "#888888",       # 灰
    "normal_conditions": "#333333",     # 黑
    "excellent_conditions": "#2563EB",  # 蓝
    "top_conditions": "#B8860B",        # 暗金
}


class TierTabsWidget(QWidget):
    """四档判定条件 Tab：首页「全部」按判定顺序纵向铺开四档
    （各档带配色标头区分），其余 Tab 单档展示。单套编辑器实例，
    Tab 仅切换可见性，两种视图天然同步。"""

    def __init__(self, candidates: list[str], parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._bar = QTabBar()
        self._bar.setExpanding(False)
        self._bar.addTab(tr("全部"))
        for _tier_key, tier_name in _TIERS:
            self._bar.addTab(tr(tier_name))
        layout.addWidget(self._bar)

        body = QFrame()
        body.setFrameShape(QFrame.Shape.StyledPanel)
        body_layout = QVBoxLayout(body)
        #: 档位 key → 编辑器（页面方由此连接 changed 与数据往返）
        self.editors: dict[str, ConditionGroupsEditor] = {}
        self._sections: list[tuple[QLabel, ConditionGroupsEditor]] = []
        for tier_key, tier_name in _TIERS:
            header = QLabel(f"■ {tr(tier_name)}" + tr("条件"))
            header.setStyleSheet(
                f"font-weight: bold; color: {_TIER_COLORS[tier_key]};")
            editor = ConditionGroupsEditor(candidates)
            body_layout.addWidget(header)
            body_layout.addWidget(editor)
            self.editors[tier_key] = editor
            self._sections.append((header, editor))
        layout.addWidget(body)

        self._bar.currentChanged.connect(self._on_tab_changed)
        self._on_tab_changed(0)

    def _on_tab_changed(self, index: int):
        # index 0 = 全部：四档纵向铺开；其余只显示对应档
        for i, (header, editor) in enumerate(self._sections):
            visible = index in (0, i + 1)
            header.setVisible(visible)
            editor.setVisible(visible)


_FIRST_TIPS = (
    "首词条：装备第 1 条词条须在候选之内（任一符合即可），\n"
    "不符合 → 本部位跳过判定。\n"
    "首词条为空 = 本部位不定义模式，不参与判定。"
)  # runtime tr()


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
        first_row.addWidget(QLabel("<b>" + tr("首词条") + "</b>"))
        tips_btn = QToolButton()
        tips_btn.setText("ⓘ")
        tips_btn.setAutoRaise(True)
        tips_btn.setToolTip(tr(_FIRST_TIPS))
        tips_btn.clicked.connect(
            lambda: QToolTip.showText(QCursor.pos(), tr(_FIRST_TIPS)))
        first_row.addWidget(tips_btn)
        first_row.addStretch()
        layout.addLayout(first_row)

        self._first: list[str] = []
        self._first_btn = QPushButton(tr("（点击选择首词条）"))
        self._first_btn.clicked.connect(self._pick_first)
        layout.addWidget(self._first_btn)

        # ② 默认判定：全档不命中时的兜底档位（空 = 跟随规则设置页）
        rating_row = QHBoxLayout()
        rating_row.addWidget(QLabel("<b>" + tr("默认判定") + "</b>"))
        self._rating_combo = QComboBox()
        self._rating_combo.addItem(tr("（跟随规则设置）"), "")
        for rating_key in RATING_KEYS:
            self._rating_combo.addItem(RATING_LABELS[rating_key], rating_key)
        self._rating_combo.currentIndexChanged.connect(
            lambda _i: self._apply())
        rating_row.addWidget(self._rating_combo)
        rating_row.addStretch()
        layout.addLayout(rating_row)

        # ③ 判定条件 Tab（全部 + 四档，顺序 junk → … → top）
        layout.addWidget(QLabel("<b>" + tr("判定条件") + "</b>"))
        self._tier_tabs = TierTabsWidget(self._candidates)
        self._tier_editors = self._tier_tabs.editors
        for editor in self._tier_editors.values():
            editor.changed.connect(self._apply)
        layout.addWidget(self._tier_tabs)
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
                                    tr("选择首词条候选"), self, flat=True)
        if dlg.exec():
            self._first = dlg.selected()
            self._update_first_text()
            self._apply()

    def _update_first_text(self):
        self._first_btn.setText(
            "/".join(self._first) if self._first else tr("（点击选择首词条）"))

    # ── 其他 ──

    def _set_editing_enabled(self, enabled: bool):
        """首词条为空时锁定默认判定与条件编辑区"""
        self._rating_combo.setEnabled(enabled)
        self._tier_tabs.setEnabled(enabled)
