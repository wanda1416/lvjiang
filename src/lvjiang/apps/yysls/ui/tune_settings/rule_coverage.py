"""三个调律处理规则表共用的部位 × 品阶覆盖检查与按需结果面板。"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from PyQt6.QtWidgets import QLabel, QPushButton, QWidget

from lvjiang.apps.yysls.core.tuning_rules import (
    QUALITY_PARTS,
    BehaviorRule,
    FoodRule,
)
from lvjiang.ui.button_styles import apply_button_style

from .....i18n import tr
from ..domain_labels import domain_label

CoverageRule = BehaviorRule | FoodRule


@dataclass(frozen=True)
class RuleCoverage:
    part: str
    quality: str
    rule_numbers: tuple[int, ...]


def rule_quality_coverage(rules: Sequence[CoverageRule]) -> list[RuleCoverage]:
    """列出每个部位/金紫品阶可能适用的启用规则，不猜测运行时评级。"""
    result: list[RuleCoverage] = []
    for part in QUALITY_PARTS:
        for quality in ("gold", "purple"):
            matching = []
            for number, rule in enumerate(rules, 1):
                if not rule.enabled:
                    continue
                rating = (rule.ratings[0] if rule.ratings
                          and rule.judge_scope != "affix" else None)
                affixes = rule.ratings if rule.judge_scope == "affix" else None
                # 其余条件选一个允许值，只检查部位/品阶是否有交集。
                if rule.matches(part, quality, float(rule.pct),
                                rating, affixes):
                    matching.append(number)
            result.append(RuleCoverage(part, quality, tuple(matching)))
    return result


class RuleCoverageCheck:
    """按钮与默认隐藏的结果标签；只在用户点击时计算。"""

    def __init__(self, rules: Callable[[], Sequence[CoverageRule]],
                 parent: QWidget):
        self._rules = rules
        self._showing = False
        self.button = QPushButton(tr("校验规则"), parent)
        self.button.setObjectName("check_rule_coverage")
        self.button.setToolTip(tr(
            "只检查部位和金/紫品阶是否有启用规则；"
            "判定结果和首词条条件需在实际运行时确定。"))
        apply_button_style(self.button, variant="neutral")
        self.button.clicked.connect(self._toggle)
        self.panel = QLabel(parent)
        self.panel.setObjectName("rule_coverage_result")
        self.panel.setWordWrap(True)
        self.panel.hide()

    def clear(self) -> None:
        self._showing = False
        self.panel.clear()
        self.panel.hide()
        self.button.setText(tr("校验规则"))

    def _toggle(self) -> None:
        if self._showing:
            self.clear()
            return
        missing = [cell for cell in rule_quality_coverage(self._rules())
                   if not cell.rule_numbers]
        if missing:
            rows = [f"{domain_label(cell.part)} · "
                    f"{tr('金装') if cell.quality == 'gold' else tr('紫装')}"
                    for cell in missing]
            self.panel.setText(tr("发现 {count} 处部位/品阶无启用规则覆盖：\n").format(
                count=len(missing)) + "\n".join(rows))
        else:
            self.panel.setText(tr(
                "部位与金/紫品阶均有候选规则。"
                "判定结果和首词条条件仍可能使规则不命中。"))
        self._showing = True
        self.panel.show()
        self.button.setText(tr("清理结果"))
