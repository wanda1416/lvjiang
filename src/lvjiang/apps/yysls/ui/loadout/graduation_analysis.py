"""毕业率分析对话框：最优组合 / 转律建议 / 培养建议 / 词条收益率。

备战方案页的「最优组合」「培养建议」按钮只是入口，打开的是同一个对话框的
不同页签。计算假设集中在顶部的共享假设栏：它是打开时从备战方案面板复制
的副本，对话框内的改动关闭即弃、不持久化。各页只在点击本页按钮时计算；
假设栏改动后已有结果打“已过期”标记，不自动重算、不清空。

搜索结果按“用户 + 备战方案”缓存一份，再次打开回填，重新计算时覆盖。
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .....i18n import tr
from .....ui.button_styles import (
    apply_compact_button_style,
    apply_dialog_button_box_style,
)
from ...core.graduation.assumptions import Assumptions
from ...core.graduation.transmute_optimizer import TransmutePlanResult
from .affix_analysis_pages import AffixAnalysisPages
from .optimal_combo import OptimalComboPage

TAB_OPTIMAL = 0
TAB_TRANSMUTE = 1
TAB_SUGGESTION = 2
TAB_YIELD = 3


@dataclass
class AnalysisCache:
    """一个用户 + 一个备战方案的上次计算结果；重新计算时整体覆盖。"""

    optimal_results: list[dict[str, Any]] = field(default_factory=list)
    transmute_result: TransmutePlanResult | None = None


class AssumptionBar(QWidget):
    """共享假设栏：备战方案面板假设的工作副本（满等级 / 满承音 / 满定音 / 模拟转律）。

    「赛季装备假设承音」不在这里：它是最优组合的搜索空间选项（额外派生承音
    候选分支），放在该页的计算设置里；智能调律固定按承音看待赛季原生装备。
    """

    changed = pyqtSignal()

    def __init__(
        self,
        initial: Assumptions,
        *,
        season_level: int,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._season_level = season_level
        self._playstyle = initial.playstyle
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)
        caption = QLabel(tr("计算假设"))
        caption.setProperty("tone", "muted")
        layout.addWidget(caption)

        self.chk_full_level = QCheckBox(tr("满等级"))
        self.chk_full_level.setToolTip(
            tr("将低于最高等级的装备视为最高等级参与计算"))
        self.chk_full_level.setChecked(bool(initial.full_level))
        self.chk_full_chengyin = QCheckBox(tr("满承音"))
        self.chk_full_chengyin.setToolTip(
            tr("将承音装备的普通词条数值视为承音上限参与计算"))
        self.chk_full_chengyin.setChecked(initial.full_chengyin)
        self.chk_full_dingyin = QCheckBox(tr("满定音"))
        self.chk_full_dingyin.setToolTip(
            tr("按当前备战方案玩法将定音视为目标满值参与计算"))
        self.chk_full_dingyin.setChecked(initial.full_dingyin)
        self.chk_simulate_transmute = QCheckBox(tr("模拟转律"))
        self.chk_simulate_transmute.setToolTip(
            tr("按装备上已保存的转律目标计算；转律建议页本身不受此项影响"))
        self.chk_simulate_transmute.setChecked(initial.simulate_transmute)
        for box in self.checkboxes():
            box.setObjectName(f"assumption_{box.text()}")
            box.toggled.connect(lambda _checked: self.changed.emit())
            layout.addWidget(box)
        layout.addStretch()
        hint = QLabel(tr("对话框内的改动不会写回备战方案"))
        hint.setProperty("tone", "muted")
        hint.setStyleSheet("font-size: 11px;")
        layout.addWidget(hint)

    def checkboxes(self) -> tuple[QCheckBox, ...]:
        return (
            self.chk_full_level, self.chk_full_chengyin,
            self.chk_full_dingyin, self.chk_simulate_transmute,
        )

    def value(self) -> Assumptions:
        return Assumptions(
            full_level=self._season_level if self.chk_full_level.isChecked() else 0,
            full_chengyin=self.chk_full_chengyin.isChecked(),
            full_dingyin=self.chk_full_dingyin.isChecked(),
            simulate_transmute=self.chk_simulate_transmute.isChecked(),
            playstyle=self._playstyle,
        )


class GraduationAnalysisDialog(QDialog):
    """四个页签共用一条假设栏的毕业率分析对话框。"""

    def __init__(
        self,
        parent: QWidget | None,
        *,
        school: str,
        scheme: str,
        plan_name: str,
        assumption_bar: AssumptionBar,
        optimal_page: OptimalComboPage,
        affix_pages: AffixAnalysisPages,
        cache: AnalysisCache,
        initial_tab: int = TAB_OPTIMAL,
    ) -> None:
        super().__init__(parent)
        self._bar = assumption_bar
        self._optimal = optimal_page
        self._affix = affix_pages
        self._cache = cache
        self.setWindowTitle(tr("毕业率分析"))
        self.setMinimumSize(940, 640)
        self.resize(1120, 760)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(10)

        context = QLabel(
            tr("{school}  ·  {scheme}  ·  {plan}").format(
                school=school, scheme=scheme, plan=plan_name))
        context.setProperty("tone", "muted")
        context.setStyleSheet("font-size: 12px;")
        # 首行最右侧放技能轴入口。它是实验性功能：需要用户自备毕业率计算器
        # Excel，与任何一页的计算流程无关，因此放在对话框右上角而不是某页内。
        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.addWidget(context, stretch=1)
        self._btn_rotation = QPushButton(tr("技能轴"))
        self._btn_rotation.setObjectName("rotationButton")
        self._btn_rotation.setToolTip(
            tr("实验性：导入毕业率计算器 Excel，查看竞速轴与伤害来源"))
        apply_compact_button_style(self._btn_rotation, variant="neutral")
        self._btn_rotation.clicked.connect(self._on_rotation)
        header.addWidget(self._btn_rotation)
        layout.addLayout(header)
        layout.addWidget(self._bar)

        self._tabs = QTabWidget()
        self._tabs.setObjectName("graduationAnalysisTabs")
        self._tabs.setDocumentMode(True)
        self._tabs.setStyleSheet(
            "QTabWidget#graduationAnalysisTabs::pane {"
            " border: 1px solid palette(midlight); border-radius: 7px; }"
            "QTabWidget#graduationAnalysisTabs QTabBar::tab {"
            " padding: 9px 18px; min-width: 120px; }"
        )
        self._tabs.addTab(self._optimal, tr("最优组合"))
        for title, page in self._affix.pages():
            self._tabs.addTab(page, title)
        self._tabs.currentChanged.connect(self._on_tab_changed)
        layout.addWidget(self._tabs, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        apply_dialog_button_box_style(buttons)
        close_button = buttons.button(QDialogButtonBox.StandardButton.Close)
        if close_button is not None:
            close_button.setText(tr("完成"))
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        # 回填缓存；之后的计算结果写回缓存
        if cache.optimal_results:
            self._optimal.restore_results(cache.optimal_results)
        if cache.transmute_result is not None:
            self._affix.restore_transmute_result(cache.transmute_result)
        self._optimal.results_changed.connect(self._on_optimal_results)
        self._affix.transmute_result_changed.connect(self._on_transmute_result)
        self._bar.changed.connect(self._on_assumptions_changed)

        self._tabs.setCurrentIndex(initial_tab)
        self._on_tab_changed(initial_tab)

    def assumptions(self) -> Assumptions:
        return self._bar.value()

    def _on_rotation(self) -> None:
        """打开技能轴查看器（实验性）：轴数据只在毕业率计算器 Excel 里。"""
        from .rotation_dialog import RotationDialog

        RotationDialog(parent=self).exec()

    def _on_tab_changed(self, index: int) -> None:
        widget = self._tabs.widget(index)
        if widget is not None:
            self._affix.ensure_built(widget)

    def _on_assumptions_changed(self) -> None:
        self._optimal.mark_stale()
        self._affix.mark_stale()

    def _on_optimal_results(self, results: list) -> None:
        self._cache.optimal_results = list(results)

    def _on_transmute_result(self, result: object) -> None:
        if isinstance(result, TransmutePlanResult):
            self._cache.transmute_result = result


def make_assumptions_provider(bar: AssumptionBar) -> Callable[[], Assumptions]:
    return bar.value
