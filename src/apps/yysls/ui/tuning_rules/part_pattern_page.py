"""部位模式页（规则顶层，每部位一页同构）

自上而下：模式摘要行（实时渲染）、首词条选择行、三档判定条件
（垃圾/能用/顶级，各为条件组列表，组间 OR、组内 AND）。
判定顺序 junk → usable → top，全不命中默认「优秀」；槽位全推导，
无必选/可选槽声明。候选为标准词条全集。
编辑共享 raw dict 顶层的 patterns.<part> 子树。
"""

from __future__ import annotations

from typing import Callable

from PyQt6.QtWidgets import (
    QCheckBox, QGroupBox, QHBoxLayout, QLabel, QPushButton, QVBoxLayout,
    QWidget,
)

from .condition_editor import AffixPickerDialog, ConditionGroupsEditor

# 三档条件：(YAML 键, 分组框标题)
_TIERS: list[tuple[str, str]] = [
    ("junk_conditions", "垃圾条件（任一组命中 → 垃圾）"),
    ("usable_conditions", "能用条件（任一组命中 → 能用）"),
    ("top_conditions", "顶级条件（任一组命中 → 顶级，全不命中 → 优秀）"),
]


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
        self._init_ui(title)
        self._loading = False

    def _init_ui(self, title: str):
        layout = QVBoxLayout(self)

        # ① 模式摘要行（只读，实时渲染）
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
        first_box = QGroupBox("首词条（任一符合即可，不符合 → 跳过判定）")
        first_layout = QHBoxLayout(first_box)
        self._first: list[str] = []
        self._first_btn = QPushButton("（点击选择）")
        self._first_btn.clicked.connect(self._pick_first)
        first_layout.addWidget(self._first_btn, 1)
        body_layout.addWidget(first_box)

        # ③ 三档判定条件（判定顺序 junk → usable → top）
        self._tier_editors: dict[str, ConditionGroupsEditor] = {}
        for tier_key, tier_title in _TIERS:
            box = QGroupBox(tier_title)
            box_layout = QVBoxLayout(box)
            editor = ConditionGroupsEditor(self._candidates)
            editor.changed.connect(self._apply)
            box_layout.addWidget(editor)
            body_layout.addWidget(box)
            self._tier_editors[tier_key] = editor
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

        for tier_key, editor in self._tier_editors.items():
            editor.set_groups(list(pattern.get(tier_key) or []))
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
        pattern: dict = {"first": list(self._first)}
        for tier_key, editor in self._tier_editors.items():
            groups = editor.get_groups()
            if groups:
                pattern[tier_key] = groups
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

    # ── 其他 ──

    def _on_enable_toggled(self):
        self._body.setEnabled(self._enable_check.isChecked())
        self._apply()

    def _update_summary(self):
        """渲染当前模式摘要，如
        首：最大外功攻击 | 垃圾×1 → 能用×2 → 顶级×1 组"""
        if not self._enable_check.isChecked():
            self._summary_label.setText("（该部位未定义模式，不参与判定）")
            return
        first = "/".join(self._first) or "?"
        counts = " → ".join(
            f"{name}×{len(self._tier_editors[key].get_groups())}"
            for key, name in (("junk_conditions", "垃圾"),
                              ("usable_conditions", "能用"),
                              ("top_conditions", "顶级")))
        self._summary_label.setText(f"首：{first} | 条件组 {counts}")
