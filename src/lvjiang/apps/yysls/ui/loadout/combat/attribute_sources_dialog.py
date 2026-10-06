"""当前战斗属性来源：只读、按属性展开的计算快照。"""
from __future__ import annotations

from collections.abc import Callable

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHeaderView,
    QLabel,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)

from lvjiang.ui.button_styles import apply_button_style, apply_dialog_button_box_style

from ......i18n import tr
from ....core.combat.attribute_sources import SOURCE_CATEGORIES, AttributeSourceReport
from ....core.combat.combat_attrs import COMBAT_ATTR_FIELDS, CombatAttributes

_CATEGORY_LABELS = {
    "base": "基础属性", "gongjue": "弓玦套装", "equipment_set": "装备套装",
    "affix": "装备词条", "dingyin": "装备定音", "equipment_base": "装备固有数值",
}


def _value(attrs: CombatAttributes, field: str, extra: bool) -> float:
    return attrs.extra_attrs.get(field, 0.0) if extra else getattr(attrs, field, 0.0)


def _format(value: float, percent: bool) -> str:
    shown = round(value * 100 if percent else value, 2 if percent else 1)
    shown = shown if shown else 0.0
    return f"{shown:.2f}%" if percent else f"{shown:.1f}"


class AttributeSourcesDialog(QDialog):
    def __init__(
        self, provider: Callable[[], tuple[AttributeSourceReport, str]], parent=None,
    ):
        super().__init__(parent)
        self._provider = provider
        self.setWindowTitle(tr("属性来源"))
        self.resize(1120, 700)
        layout = QVBoxLayout(self)
        self._caption = QLabel()
        self._caption.setWordWrap(True)
        layout.addWidget(self._caption)
        self._tree = QTreeWidget()
        self._tree.setHeaderLabels(
            [tr("属性 / 来源")]
            + [tr(_CATEGORY_LABELS[key]) for key in SOURCE_CATEGORIES]
            + [tr("合计（白字）"), tr("生效（黄字）")])
        self._tree.setAlternatingRowColors(True)
        header = self._tree.header()
        if header is not None:
            header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
            header.setStretchLastSection(False)
        layout.addWidget(self._tree, 1)
        self._notes = QLabel()
        self._notes.setWordWrap(True)
        self._notes.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self._notes)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        apply_dialog_button_box_style(buttons)
        refresh = QPushButton(tr("刷新"))
        apply_button_style(refresh, variant="neutral")
        refresh.clicked.connect(self.refresh)
        buttons.addButton(refresh, QDialogButtonBox.ButtonRole.ActionRole)
        buttons.rejected.connect(self.close)
        layout.addWidget(buttons)
        self.refresh()

    def refresh(self) -> None:
        report, caption = self._provider()
        self._caption.setText(caption)
        self._tree.clear()
        totals = {key: report.category_attrs(key) for key in SOURCE_CATEGORIES}
        rows = [(field, tr(label), unit == "%", False)
                for field, label, unit, _ in COMBAT_ATTR_FIELDS]
        rows.extend((field, field, True, True) for field in sorted(report.raw.extra_attrs))
        for field, label, percent, extra in rows:
            texts = [label] + [_format(_value(totals[key], field, extra), percent)
                               for key in SOURCE_CATEGORIES]
            texts += [_format(_value(attrs, field, extra), percent)
                      for attrs in (report.raw, report.effective)]
            item = QTreeWidgetItem(texts)
            self._tree.addTopLevelItem(item)
            for contribution in report.contributions:
                value = _value(contribution.attrs, field, extra)
                if not value or not contribution.label:
                    continue
                detail = QTreeWidgetItem([contribution.label])
                column = SOURCE_CATEGORIES.index(contribution.category) + 1
                detail.setText(column, _format(value, percent))
                item.addChild(detail)
            for column in range(1, len(texts)):
                item.setTextAlignment(column, Qt.AlignmentFlag.AlignRight)
        notes = [tr("基础属性使用当前所选的已存数值，尚未拆分的内容仍归这一列。五维转换已计入对应词条，不重复相加。")]
        notes.append(tr("判定抗性 {judge:.1f}%，增益抗性 {buff:.1f}%；黄字按公共规则折算与封顶。").format(
            judge=report.context.judge_resistance, buff=report.context.buff_resistance))
        if report.context.target_pen_field:
            notes.append(tr("装备无相穿透已折算至当前流派属攻穿透。"))
        if report.excluded:
            notes.append(tr("以下词条未计入（按部位规则或同名只取最高处理）：")
                         + "、".join(report.excluded))
        self._notes.setText("\n".join(notes))
