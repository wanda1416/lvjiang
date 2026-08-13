"""通用判定编辑页（规则编辑器）

规则级四档判定条件，对所有部位生效（判定时逐档并入各部位模式的
条件组、通用在前）。无首词条与默认判定，直接展示判定条件 Tab：
全部 / 垃圾 / 一般 / 优秀 / 顶级（「全部」按判定顺序纵向铺开四档，
各档带配色标头区分；各档为条件组列表，组间 OR、组内 AND，
组可绑定开关前提 when）。
编辑共享 raw dict 顶层的 common_conditions 子树。
"""

from __future__ import annotations

from typing import Callable

from PyQt6.QtWidgets import QLabel, QVBoxLayout, QWidget

from .....i18n import tr

from .condition_editor import ConditionGroupsEditor
from .part_pattern_page import TierTabsWidget


class CommonJudgePage(QWidget):
    """通用判定页：规则级四档条件，对所有部位生效"""

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
        layout.addWidget(QLabel(
            "<b>" + tr("判定条件") + "</b>（" + tr("通用判定：这里的条件对所有部位都生效") + "）"))

        # 判定条件 Tab（全部 + 四档，顺序 junk → … → top）
        self._tier_tabs = TierTabsWidget(self._candidates)
        self._tier_editors: dict[str, ConditionGroupsEditor] = \
            self._tier_tabs.editors
        for editor in self._tier_editors.values():
            editor.changed.connect(self._apply)
        layout.addWidget(self._tier_tabs)
        layout.addStretch()

    # ── 数据往返 ──

    def load(self, data: dict):
        """回填规则顶层 raw dict 引用"""
        self._loading = True
        self._data = data
        common = data.get("common_conditions") or {}
        for tier_key, editor in self._tier_editors.items():
            editor.set_groups(list(common.get(tier_key) or []))
        self._loading = False

    def _apply(self):
        if self._loading:
            return
        common: dict = {}
        for tier_key, editor in self._tier_editors.items():
            groups = editor.get_groups()
            if groups:
                common[tier_key] = groups
        if common:
            self._data["common_conditions"] = common
        else:
            # 四档全空 = 不定义通用判定
            self._data.pop("common_conditions", None)
        self._on_changed()
