"""词条培养分析页组：转律建议 / 培养建议 / 词条收益率。

三个页面由毕业率分析对话框作为页签承载；本模块只负责页面内容、按需
计算与后台转律搜索，计算假设由对话框的共享假设栏提供。"""
from __future__ import annotations

import copy
from collections.abc import Callable

from loguru import logger
from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QPalette
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .....i18n import tr
from .....ui.button_styles import apply_button_style
from ...config.equipment_slots import grid_layout
from ...config.tune_slots import SLOT_LABELS
from ...core.graduation.affix_impact import (
    AffixCombinationResult,
    AffixImpact,
    AffixImpactReport,
    AffixReplacementSuggestion,
)
from ...core.graduation.assumptions import Assumptions
from ...core.graduation.transmute_optimizer import TransmutePlanResult
from ...core.loadout.transmute import (
    REASON_FIRST_TRANSFERRED,
    REASON_ILLEGAL,
    REASON_MULTIPLE_TRANSFERRED,
    REASON_NO_LEVEL_CONFIG,
    REASON_NO_RETRANSFER,
    REASON_NO_RETRANSFER_AFTER_CHENGYIN,
    REASON_NO_SLOTS,
    REASON_UNKNOWN_ORIGINAL_LEVEL,
    TARGET_NAME_KEY,
    TARGET_VALUE_KEY,
    strip_transmute_targets,
)
from .background import JobController
from .equip.cards import _SlotCard

JointAnalyzer = Callable[[tuple[str, ...]], AffixCombinationResult]
ReportProvider = Callable[[], AffixImpactReport]
TransmuteRunner = Callable[[Callable[[], bool], Assumptions], TransmutePlanResult]
ApplyHandler = Callable[[TransmutePlanResult], bool]

#: 默认 4 列 × 2 行；与备战方案槽位布局同源（config.equipment_slots）。
DEFAULT_SLOT_LAYOUT: tuple[tuple[int, int, str], ...] = grid_layout()


def transmute_reason_text(code: str) -> str:
    return {
        REASON_UNKNOWN_ORIGINAL_LEVEL: tr("原始等级未知"),
        REASON_NO_RETRANSFER: tr("不支持无限转律"),
        REASON_NO_RETRANSFER_AFTER_CHENGYIN: tr("承音后不可转律"),
        REASON_ILLEGAL: tr("词条组合异常"),
        REASON_MULTIPLE_TRANSFERRED: tr("多个转律标记，请校正"),
        REASON_FIRST_TRANSFERRED: tr("首词条带转律标记，请校正"),
        REASON_NO_SLOTS: tr("没有可转词条"),
        REASON_NO_LEVEL_CONFIG: tr("缺少该等级配置"),
    }.get(code, code)


class _ProportionalTableWidget(QTableWidget):
    """随可用宽度按比例扩展全部列，而不是只拉伸某一列。"""

    def __init__(
        self,
        rows: int,
        weights: tuple[int, ...],
        minimums: tuple[int, ...],
    ) -> None:
        if len(weights) != len(minimums):
            raise ValueError("column weights and minimums must have equal length")
        super().__init__(rows, len(weights))
        self._column_weights = weights
        self._column_minimums = minimums
        header = self.horizontalHeader()
        assert header is not None
        header.setSectionResizeMode(QHeaderView.ResizeMode.Fixed)

    def _resize_columns(self) -> None:
        viewport = self.viewport()
        assert viewport is not None
        available = viewport.width()
        minimum_total = sum(self._column_minimums)
        extra = max(available - minimum_total, 0)
        weight_total = sum(self._column_weights)
        widths = [
            minimum + extra * weight // weight_total
            for minimum, weight in zip(
                self._column_minimums, self._column_weights, strict=True,
            )
        ]
        # 整数除法余量交给主要内容列，避免产生横向空隙。
        if available > minimum_total:
            widths[0] += available - sum(widths)
        for column, width in enumerate(widths):
            self.setColumnWidth(column, width)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._resize_columns()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._resize_columns()


class AffixAnalysisPages(QWidget):
    """转律建议、等品质培养建议与词条收益率三个页面。

    转律建议只在点击“计算”时后台搜索；培养建议与词条收益率首次切换到
    对应页面时才计算并缓存。本对象本身不显示，只持有页面与共享状态。
    """

    #: 转律建议结果变化（完成），供对话框写入按用户+方案隔离的缓存
    transmute_result_changed = pyqtSignal(object)

    def __init__(
        self,
        school: str,
        scheme: str,
        parent: QWidget | None = None,
        *,
        equipped: dict[str, dict] | None = None,
        report_provider: ReportProvider | None = None,
        joint_analyzer: JointAnalyzer | None = None,
        transmute_runner: TransmuteRunner | None = None,
        apply_handler: ApplyHandler | None = None,
        assumptions_provider: Callable[[], Assumptions] | None = None,
        slot_layout: tuple[tuple[int, int, str], ...] = DEFAULT_SLOT_LAYOUT,
        display_params: dict | None = None,
    ) -> None:
        super().__init__(parent)
        self._school = school
        self._scheme = scheme
        self._equipped = copy.deepcopy(equipped or {})
        self._report_provider = report_provider
        self._joint_analyzer = joint_analyzer
        self._transmute_runner = transmute_runner
        self._apply_handler = apply_handler
        self._assumptions_provider = assumptions_provider or Assumptions
        self._slot_layout = slot_layout
        self._display_params = dict(display_params or {})
        self._report: AffixImpactReport | None = None
        self._actionable_suggestions: tuple[AffixReplacementSuggestion, ...] = ()
        self._blocked_equipment: tuple = ()
        self._slot_checkboxes: dict[str, QCheckBox] = {}
        self._joint_timer = QTimer(self)
        self._joint_timer.setSingleShot(True)
        self._joint_timer.setInterval(120)
        self._joint_timer.timeout.connect(self._calculate_joint)
        self._transmute_jobs = JobController(self)
        self._transmute_jobs.finished.connect(self._on_transmute_finished)
        self._transmute_jobs.failed.connect(self._on_transmute_failed)
        self._transmute_jobs.cancelled.connect(self._on_transmute_cancelled)
        self._transmute_result: TransmutePlanResult | None = None
        self._transmute_applied = False
        self._transmute_cards: dict[str, _SlotCard] = {}
        self._tab_built = {"suggestion": False, "sensitivity": False}

        # 页面先挂在本对象名下（findChild 可达），加入页签时由 QTabWidget 接管
        self.transmute_page = self._transmute_tab()
        self.transmute_page.setParent(self)
        self.suggestion_page = QWidget(self)
        QVBoxLayout(self.suggestion_page).setContentsMargins(0, 0, 0, 0)
        self.yield_page = QWidget(self)
        QVBoxLayout(self.yield_page).setContentsMargins(0, 0, 0, 0)

    def pages(self) -> tuple[tuple[str, QWidget], ...]:
        """(页签标题, 页面) 序列，按展示顺序。"""
        return (
            (tr("转律建议"), self.transmute_page),
            (tr("培养建议"), self.suggestion_page),
            (tr("词条收益率"), self.yield_page),
        )

    def mark_stale(self) -> None:
        """假设栏改动后提示转律建议已过期，不自动重算、不清空。"""
        if self._transmute_result is not None and self._transmute_run.isEnabled():
            self._transmute_status.setText(
                tr("计算假设已变化，当前结果按旧假设得出；请重新计算。"))

    # ── 按需构建 ──────────────────────────────────────────

    def _ensure_report(self) -> AffixImpactReport | None:
        if self._report is None and self._report_provider is not None:
            try:
                self._report = self._report_provider()
            except Exception as exc:  # noqa: BLE001 - 页签内提示，不关对话框
                logger.error(f"词条分析失败: {exc}")
                QMessageBox.critical(self.window(), tr("分析失败"), str(exc))
                return None
            self._actionable_suggestions = tuple(
                item for item in self._report.suggestions
                if item.graduation_delta > 1e-9
            )
            self._blocked_equipment = self._report.blocked_equipment
        return self._report

    def ensure_built(self, widget: QWidget) -> None:
        """页面首次显示时才计算培养建议 / 词条收益率。"""
        if widget is self.suggestion_page and not self._tab_built["suggestion"]:
            report = self._ensure_report()
            if report is None:
                return
            self._tab_built["suggestion"] = True
            container_layout = self.suggestion_page.layout()
            assert container_layout is not None
            container_layout.addWidget(self._suggestion_page(report))
        elif widget is self.yield_page and not self._tab_built["sensitivity"]:
            report = self._ensure_report()
            if report is None:
                return
            self._tab_built["sensitivity"] = True
            container_layout = self.yield_page.layout()
            assert container_layout is not None
            container_layout.addWidget(self._sensitivity_tab(report))

    def _suggestion_page(self, report: AffixImpactReport) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        # 与转律建议页同一套外边距，三张指标卡在两个页签里顶部对齐
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)
        metrics = QHBoxLayout()
        metrics.setSpacing(10)
        metrics.addWidget(self._metric_card(
            tr("当前毕业率"),
            (tr("需先校正") if self._blocked_equipment
             else f"{report.baseline_rate * 100:.2f}%"),
            "rate",
        ), 1)
        metrics.addWidget(self._metric_card(
            tr("可培养词条"), tr("{count} 条").format(
                count=len(self._actionable_suggestions),
            ),
            "count",
        ), 1)
        best_delta = max(
            (item.graduation_delta for item in self._actionable_suggestions),
            default=0.0,
        )
        metrics.addWidget(self._metric_card(
            tr("最高单项提升"), f"{best_delta * 100:+.2f}%" if best_delta else "—",
            "gain",
        ), 1)
        layout.addLayout(metrics)
        layout.addWidget(self._suggestion_tab(embedded=True), 1)
        return page

    # ── 转律建议 ──────────────────────────────────────────

    def _transmute_tab(self) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        metrics = QHBoxLayout()
        metrics.setSpacing(10)
        self._transmute_baseline = self._metric_card(
            tr("原词条毕业率"), "—", "transmuteBaseline")
        self._transmute_saved = self._metric_card(
            tr("已保存目标毕业率"), "—", "transmuteSaved")
        self._transmute_final = self._metric_card(
            tr("推荐毕业率"), "—", "transmuteFinal")
        for card in (self._transmute_baseline, self._transmute_saved,
                     self._transmute_final):
            metrics.addWidget(card, 1)
        layout.addLayout(metrics)

        self._transmute_note = QLabel()
        self._transmute_note.setWordWrap(True)
        self._transmute_note.setProperty("tone", "muted")
        self._set_transmute_basis(())
        layout.addWidget(self._transmute_note)

        self._transmute_status = QLabel(tr("点击「计算」开始搜索；不会自动运行。"))
        self._transmute_status.setObjectName("transmuteStatus")
        self._transmute_status.setWordWrap(True)
        layout.addWidget(self._transmute_status)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        content = QWidget()
        grid = QGridLayout(content)
        grid.setSpacing(8)
        grid.setContentsMargins(0, 0, 0, 0)
        params = {**self._display_params, "card_min_height": max(
            int(self._display_params.get("card_min_height", 160) or 160), 190)}
        for row, col, slot_key in self._slot_layout:
            card = _SlotCard(
                slot_key, SLOT_LABELS.get(slot_key, slot_key), "",
                params, read_only=True)
            card.setObjectName(f"transmuteCard_{slot_key}")
            equip = self._equipped.get(slot_key)
            if isinstance(equip, dict):
                card.set_equip(copy.deepcopy(equip))
            else:
                card.set_empty()
                card.set_note(tr("缺装备，补齐后计算"))
            grid.addWidget(card, row, col)
            self._transmute_cards[slot_key] = card
        for col in range(4):
            grid.setColumnStretch(col, 1)
        scroll.setWidget(content)
        layout.addWidget(scroll, 1)

        buttons = QHBoxLayout()
        buttons.addStretch()
        self._transmute_run = QPushButton(tr("计算"))
        self._transmute_run.setObjectName("transmuteRunButton")
        self._transmute_run.setEnabled(self._transmute_runner is not None)
        self._transmute_run.clicked.connect(self._start_transmute)
        self._transmute_cancel = QPushButton(tr("取消计算"))
        self._transmute_cancel.setObjectName("transmuteCancelButton")
        self._transmute_cancel.setEnabled(False)
        self._transmute_cancel.clicked.connect(self._cancel_transmute)
        self._transmute_apply = QPushButton(tr("应用"))
        self._transmute_apply.setObjectName("transmuteApplyButton")
        self._transmute_apply.setToolTip(
            tr("把推荐目标写入备战方案的装备卡片；目标属于公共装备，"
               "会影响所有引用这些装备的方案"))
        self._transmute_apply.setEnabled(False)
        self._transmute_apply.clicked.connect(self._apply_transmute)
        # 三个按钮同一套几何与语义配色：主动作、中性、主动作
        apply_button_style(self._transmute_run, self._transmute_apply,
                           variant="action")
        apply_button_style(self._transmute_cancel, variant="neutral")
        for button in (self._transmute_run, self._transmute_cancel,
                       self._transmute_apply):
            button.setMinimumWidth(100)
            buttons.addWidget(button)
        layout.addLayout(buttons)
        return container

    def _set_transmute_basis(self, labels: tuple[str, ...]) -> None:
        basis = "、".join(labels) if labels else tr("实际数值")
        self._transmute_note.setText(tr(
            "八件装备各至多一次转律，目标按转律词条库并集选取；未承音按普通上限"
            "（彩狗粮 100%），承音按承音上限。基于：{basis}。").format(basis=basis))

    def _start_transmute(self) -> None:
        if self._transmute_runner is None:
            return
        self._transmute_run.setEnabled(False)
        self._transmute_cancel.setEnabled(True)
        self._transmute_apply.setEnabled(False)
        self._transmute_status.setText(tr("正在搜索转律组合…"))
        # 假设在点击时定格；转律建议不使用“模拟转律”本身（它就是在算目标）
        assumptions = self._assumptions_provider()
        assumptions = Assumptions(**{**assumptions.to_flags(),
                                     "simulate_transmute": False})
        self._set_transmute_basis(assumptions.labels())
        runner = self._transmute_runner
        self._transmute_jobs.start(
            lambda ctx: runner(ctx.is_cancelled, assumptions))

    def _cancel_transmute(self) -> None:
        self._transmute_jobs.cancel()

    def _transmute_idle(self) -> None:
        self._transmute_cancel.setEnabled(False)
        self._transmute_run.setEnabled(True)
        self._transmute_run.setText(tr("重新计算"))

    def _on_transmute_cancelled(self) -> None:
        self._transmute_idle()
        self._transmute_status.setText(tr("已取消；保留上一次完成的结果。"))

    def _on_transmute_failed(self, message: str) -> None:
        self._transmute_idle()
        self._transmute_status.setText(
            tr("计算失败：{error}").format(error=message or "—"))

    def _on_transmute_finished(self, result) -> None:
        self._transmute_idle()
        if not isinstance(result, TransmutePlanResult):
            self._transmute_status.setText(
                tr("计算失败：{error}").format(error="—"))
            return
        self.render_transmute_result(result)

    def restore_transmute_result(self, result: TransmutePlanResult) -> None:
        """回填缓存的转律建议（不触发搜索，不写缓存）。"""
        self.render_transmute_result(result, emit=False)
        self._transmute_run.setText(tr("重新计算"))

    def render_transmute_result(
        self, result: TransmutePlanResult, *, emit: bool = True,
    ) -> None:
        """把搜索结果画到八张卡片上，并决定“应用”是否可用。"""
        self._transmute_result = result
        self._transmute_applied = False
        if emit:
            self.transmute_result_changed.emit(result)
        self._set_metric(self._transmute_baseline,
                         f"{result.baseline_rate * 100:.2f}%")
        self._set_metric(
            self._transmute_saved,
            f"{result.saved_rate * 100:.2f}%" if result.saved_rate is not None
            else "—")
        self._set_metric(
            self._transmute_final,
            f"{result.final_rate * 100:.2f}%  ({result.gain * 100:+.2f})"
            if result.moves else f"{result.final_rate * 100:.2f}%")
        for slot_key, card in self._transmute_cards.items():
            status = next(
                (item for item in result.slots if item.slot_key == slot_key),
                None)
            equip = self._equipped.get(slot_key)
            if status is None or not isinstance(equip, dict):
                continue
            shown = strip_transmute_targets(copy.deepcopy(equip))
            move = status.move
            if move is not None:
                affix = shown.get(f"affix_{move.affix_index}")
                if isinstance(affix, dict):
                    affix[TARGET_NAME_KEY] = move.to_name
                    affix[TARGET_VALUE_KEY] = move.to_value
            card.set_equip(shown)
            if not status.eligible:
                card.set_note(tr("不参与：{reason}").format(
                    reason=transmute_reason_text(status.reason)))
            elif move is None:
                card.set_note(tr("无需转律"))
            else:
                card.set_note(tr("推荐 {gain:+.2f}%（换词条净收益 {swap:+.2f}%）").format(
                    gain=move.marginal_gain * 100, swap=move.swap_gain * 100))
        parts: list[str] = []
        if result.missing_slots:
            parts.append(tr("缺 {count} 件装备，请补齐后重新计算").format(
                count=len(result.missing_slots)))
        if not result.trusted:
            parts.append(tr("存在词条组合异常的装备，结果不可信，禁止应用"))
        if not result.exhausted:
            parts.append(tr("搜索未穷尽（预算耗尽），以下是当前搜索最好结果"))
        if result.moves:
            ordered = sorted(result.moves, key=lambda m: -m.marginal_gain)
            parts.append(tr("建议顺序：{order}").format(order=" → ".join(
                f"{SLOT_LABELS.get(m.slot_key, m.slot_key)}"
                f"({m.marginal_gain * 100:+.2f}%)" for m in ordered)))
            parts.append(tr("共求值 {count} 次").format(count=result.evaluated))
        else:
            parts.append(tr("没有找到超过阈值的转律提升（不代表不存在）"))
        self._transmute_status.setText("；".join(parts))
        self._transmute_apply.setEnabled(
            result.applicable and self._apply_handler is not None)

    def _apply_transmute(self) -> None:
        result = self._transmute_result
        if result is None or self._apply_handler is None or not result.applicable:
            return
        self._transmute_apply.setEnabled(False)
        try:
            ok = self._apply_handler(result)
        except Exception as exc:  # noqa: BLE001 - 失败提示后允许重试
            logger.error(f"应用转律目标失败: {exc}")
            QMessageBox.critical(self.window(), tr("应用失败"), str(exc))
            self._transmute_apply.setEnabled(True)
            return
        if not ok:
            self._transmute_apply.setEnabled(True)
            return
        self._transmute_applied = True
        self._transmute_status.setText(
            tr("已写入 {count} 件装备的转律目标；勾选备战方案的「模拟转律」查看效果。")
            .format(count=len(result.moves)))

    @staticmethod
    def _set_metric(card: QFrame, value: str) -> None:
        label = card.findChild(QLabel, "affixMetricValue_" + str(
            card.objectName()).removeprefix("affixMetric_"))
        if label is not None:
            label.setText(value)

    @staticmethod
    def _metric_card(label: str, value: str, name: str) -> QFrame:
        card = QFrame()
        card.setObjectName(f"affixMetric_{name}")
        card.setProperty("surface", "card")
        row = QHBoxLayout(card)
        row.setContentsMargins(14, 9, 14, 9)
        row.setSpacing(10)
        caption = QLabel(label)
        caption.setProperty("tone", "muted")
        number = QLabel(value)
        number.setObjectName(f"affixMetricValue_{name}")
        if name == "gain" and value != "—":
            number.setProperty("status", "success")
        number.setStyleSheet(
            "font-size: 17px; font-weight: 700; padding: 2px 6px;"
        )
        row.addWidget(caption)
        row.addStretch()
        row.addWidget(number)
        return card

    def _suggestion_tab(self, *, embedded: bool = False) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        # 嵌在带外边距的页面里时不再叠加自己的边距
        layout.setContentsMargins(*((0, 0, 0, 0) if embedded else (14, 14, 14, 14)))
        layout.setSpacing(10)
        banner = QFrame()
        banner.setProperty("status", "info")
        banner.setStyleSheet("QFrame { border-radius: 6px; }")
        banner_layout = QHBoxLayout(banner)
        banner_layout.setContentsMargins(12, 8, 12, 8)
        note = QLabel(tr(
            "建议按装备部位分组。可同时勾选 1-3 件装备进行联合计算；系统会为每个词条位置"
            "比较多个候选目标，并重新计算整套毕业率，不会把单项提升直接相加。"
        ))
        note.setWordWrap(True)
        banner_layout.addWidget(note)
        layout.addWidget(banner)

        if self._blocked_equipment:
            blocked = QFrame()
            blocked.setProperty("status", "warning")
            blocked_layout = QVBoxLayout(blocked)
            blocked_layout.setContentsMargins(12, 8, 12, 8)
            title = QLabel(tr("以下装备存在词条组合异常，已排除培养计算；请先校正："))
            title.setStyleSheet("font-weight: 700;")
            title.setWordWrap(True)
            blocked_layout.addWidget(title)
            for item in self._blocked_equipment:
                detail = QLabel(
                    tr("{slot} · {name}：{reasons}").format(
                        slot=SLOT_LABELS.get(item.slot_key, item.slot_key),
                        name=item.equipment_name,
                        reasons="；".join(item.reasons),
                    )
                )
                detail.setWordWrap(True)
                blocked_layout.addWidget(detail)
            layout.addWidget(blocked)

        result_card = QFrame()
        result_card.setProperty("surface", "card")
        result_layout = QVBoxLayout(result_card)
        result_layout.setContentsMargins(12, 9, 12, 9)
        result_layout.setSpacing(5)
        result_top = QHBoxLayout()
        result_title = QLabel(tr("联合培养结果"))
        result_title.setStyleSheet("font-weight: 700; font-size: 13px;")
        result_top.addWidget(result_title)
        result_top.addStretch()
        self._joint_rate = QLabel("—")
        self._joint_rate.setObjectName("jointGraduationRate")
        self._joint_rate.setStyleSheet("font-size: 15px; font-weight: 700;")
        result_top.addWidget(self._joint_rate)
        self._joint_gain = QLabel("—")
        self._joint_gain.setObjectName("jointGraduationGain")
        self._joint_gain.setStyleSheet(
            "font-size: 15px; font-weight: 700; padding: 2px 6px;"
        )
        result_top.addWidget(self._joint_gain)
        self._joint_button = QPushButton(tr("计算联合提升"))
        self._joint_button.setObjectName("calculateJointAffixButton")
        apply_button_style(self._joint_button, variant="action")
        self._joint_button.setMinimumWidth(112)
        self._joint_button.setEnabled(False)
        self._joint_button.clicked.connect(self._calculate_joint)
        result_top.addWidget(self._joint_button)
        result_layout.addLayout(result_top)
        self._joint_detail = QLabel(tr("勾选装备后计算；最多选择 3 件。"))
        self._joint_detail.setObjectName("jointAffixDetail")
        self._joint_detail.setProperty("tone", "muted")
        self._joint_detail.setWordWrap(True)
        result_layout.addWidget(self._joint_detail)
        layout.addWidget(result_card)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        content = QWidget()
        cards = QVBoxLayout(content)
        cards.setContentsMargins(0, 0, 0, 0)
        cards.setSpacing(8)
        grouped = self._group_suggestions(self._actionable_suggestions)
        for slot_key, suggestions in grouped:
            cards.addWidget(self._equipment_card(slot_key, suggestions))
        if not grouped:
            empty = QLabel(tr("当前配装没有符合规则且能提高毕业率的词条替换建议"))
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty.setProperty("tone", "muted")
            cards.addWidget(empty, 1)
        cards.addStretch()
        scroll.setWidget(content)
        layout.addWidget(scroll, 1)
        return container

    @staticmethod
    def _group_suggestions(
        suggestions: tuple[AffixReplacementSuggestion, ...],
    ) -> list[tuple[str, tuple[AffixReplacementSuggestion, ...]]]:
        by_slot: dict[str, list[AffixReplacementSuggestion]] = {}
        for suggestion in suggestions:
            by_slot.setdefault(suggestion.slot_key, []).append(suggestion)
        return [
            (slot, tuple(sorted(items, key=lambda item: item.affix_index)))
            for slot in SLOT_LABELS
            if (items := by_slot.get(slot))
        ]

    def _equipment_card(
        self,
        slot_key: str,
        suggestions: tuple[AffixReplacementSuggestion, ...],
    ) -> QFrame:
        card = QFrame()
        card.setProperty("surface", "card")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(12, 8, 12, 9)
        card_layout.setSpacing(6)

        header = QHBoxLayout()
        equipment_name = suggestions[0].equipment_name
        checkbox = QCheckBox(
            tr("{slot} · {name}").format(
                slot=SLOT_LABELS.get(slot_key, slot_key), name=equipment_name,
            )
        )
        checkbox.setObjectName(f"affixSlotCheck_{slot_key}")
        checkbox.setStyleSheet("font-size: 13px; font-weight: 700;")
        checkbox.stateChanged.connect(
            lambda state, key=slot_key: self._on_slot_checked(key, state),
        )
        self._slot_checkboxes[slot_key] = checkbox
        header.addWidget(checkbox)
        header.addStretch()
        count = QLabel(tr("{count} 个可提升词条").format(count=len(suggestions)))
        count.setProperty("status", "info")
        count.setStyleSheet("padding: 2px 7px; border-radius: 7px;")
        header.addWidget(count)
        card_layout.addLayout(header)

        grid = QGridLayout()
        grid.setContentsMargins(22, 0, 0, 0)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(5)
        for column, heading in enumerate((
            tr("位置"), tr("当前词条"), "", tr("建议转为"), tr("单项提升"),
        )):
            label = QLabel(heading)
            label.setProperty("tone", "muted")
            label.setStyleSheet("font-size: 11px;")
            grid.addWidget(label, 0, column)
        affix_labels = {2: tr("商"), 3: tr("角"), 4: tr("徵"), 5: tr("羽")}
        for row, suggestion in enumerate(suggestions, start=1):
            position = QLabel(affix_labels.get(
                suggestion.affix_index, str(suggestion.affix_index),
            ))
            source = QLabel(f"{suggestion.from_name}  {suggestion.from_value:g}")
            arrow = QLabel("→")
            arrow.setProperty("tone", "muted")
            target = QLabel(f"{suggestion.to_name}  {suggestion.to_value:g}")
            target.setStyleSheet("font-weight: 600;")
            gain = QLabel(f"{suggestion.graduation_delta * 100:+.2f}%")
            gain.setStyleSheet("font-weight: 700; color: palette(highlight);")
            gain.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            for column, widget in enumerate((position, source, arrow, target, gain)):
                grid.addWidget(widget, row, column)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)
        card_layout.addLayout(grid)
        return card

    def _on_slot_checked(self, slot_key: str, state: int) -> None:
        selected = [
            key for key, checkbox in self._slot_checkboxes.items()
            if checkbox.isChecked()
        ]
        if state and len(selected) > 3:
            checkbox = self._slot_checkboxes[slot_key]
            checkbox.blockSignals(True)
            checkbox.setChecked(False)
            checkbox.blockSignals(False)
            self._joint_detail.setText(tr("最多同时选择 3 件装备。"))
            selected.remove(slot_key)
            self._joint_button.setEnabled(bool(selected))
            # 勾选被拒等于什么都没变，已算好的结果要留着——清空会让用户误点
            # 一下就得重算一遍，而且看不出是被拒还是算失败。
            return
        self._joint_button.setEnabled(bool(selected))
        self._joint_rate.setText("—")
        self._joint_gain.setText("—")
        if selected:
            self._joint_detail.setText(
                tr("已选择 {count} 件装备，正在计算联合提升…").format(
                    count=len(selected),
                )
            )
            if self._joint_analyzer is not None:
                self._joint_timer.start()
        else:
            self._joint_timer.stop()
            self._joint_detail.setText(tr("勾选装备后计算；最多选择 3 件。"))

    def _calculate_joint(self) -> None:
        self._joint_timer.stop()
        if self._joint_analyzer is None:
            self._joint_detail.setText(tr(
                "联合计算器未初始化，请关闭窗口后重新打开词条培养分析。"
            ))
            return
        slots = tuple(
            key for key, checkbox in self._slot_checkboxes.items()
            if checkbox.isChecked()
        )
        if not slots:
            return
        self._joint_button.setEnabled(False)
        self._joint_button.setText(tr("计算中…"))
        try:
            result = self._joint_analyzer(slots)
            self._render_joint_result(result)
        except Exception as exc:
            self._joint_rate.setText("—")
            self._joint_gain.setText("—")
            self._joint_detail.setText(
                tr("联合计算失败：{error}").format(error=str(exc)),
            )
        finally:
            self._joint_button.setText(tr("重新计算"))
            self._joint_button.setEnabled(True)

    def _render_joint_result(self, result: AffixCombinationResult) -> None:
        self._joint_rate.setText(f"{result.graduation_rate * 100:.2f}%")
        self._joint_gain.setText(f"{result.graduation_delta * 100:+.2f}%")
        self._joint_gain.setProperty("status", "success")
        style = self._joint_gain.style()
        assert style is not None
        style.unpolish(self._joint_gain)
        style.polish(self._joint_gain)
        if not result.replacements:
            self._joint_detail.setText(tr("所选装备没有能进一步提高毕业率的合法联合方案。"))
            return
        affix_labels = {2: tr("商"), 3: tr("角"), 4: tr("徵"), 5: tr("羽")}
        details = [
            tr("{slot}·{position}：{source} → {target}").format(
                slot=SLOT_LABELS.get(item.slot_key, item.slot_key),
                position=affix_labels.get(item.affix_index, str(item.affix_index)),
                source=item.from_name,
                target=item.to_name,
            )
            for item in result.replacements
        ]
        details.append(tr("已校验 {count} 种合法组合").format(
            count=result.evaluated_combinations,
        ))
        self._joint_detail.setText("  ·  ".join(details))

    def _sensitivity_tab(self, report: AffixImpactReport) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)
        banner = QFrame()
        banner.setProperty("status", "warning")
        banner.setStyleSheet("QFrame { border-radius: 6px; }")
        banner_layout = QHBoxLayout(banner)
        banner_layout.setContentsMargins(12, 8, 12, 8)
        note = QLabel(
            tr("以下是词条收益率（理论值），不代表游戏中可以单独获得或失去一条词条。"
               "新增按 Lv{level} 普通词条满值，扣除按当前装备实际值计算。").format(
                level=report.affix_level,
            )
        )
        note.setWordWrap(True)
        banner_layout.addWidget(note)
        layout.addWidget(banner)
        columns = QHBoxLayout()
        columns.setSpacing(12)
        columns.addWidget(self._section(
            tr("新增一条词条的收益率"), report.additions, positive=True,
        ), 1)
        columns.addWidget(self._section(
            tr("扣除一条当前词条的损失率"), report.removals, positive=False,
        ), 1)
        layout.addLayout(columns, 1)
        return container

    @staticmethod
    def _prepare_table(table: QTableWidget) -> None:
        # QTableWidgetItem 的右对齐文本默认会紧贴单元格边界。统一给内容留出
        # 呼吸空间；数字列保留右对齐，名称列也获得一致的左右起始位置。
        table.setStyleSheet(
            "QTableWidget::item { padding-left: 7px; padding-right: 12px; }"
        )
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        table.setAlternatingRowColors(True)
        table.setShowGrid(False)
        table.setCornerButtonEnabled(False)
        table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        vertical_header = table.verticalHeader()
        assert vertical_header is not None
        vertical_header.setVisible(False)
        vertical_header.setDefaultSectionSize(38)
        horizontal_header = table.horizontalHeader()
        assert horizontal_header is not None
        horizontal_header.setMinimumHeight(34)

    def _section(
        self,
        heading: str,
        impacts: tuple[AffixImpact, ...],
        *,
        positive: bool,
    ) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        label = QLabel(heading)
        label.setStyleSheet("font-size: 13px; font-weight: 600;")
        layout.addWidget(label)

        table = _ProportionalTableWidget(
            len(impacts),
            weights=(60, 23, 17),
            minimums=(150, 100, 70),
        )
        table.setObjectName(
            "affixImpactAdditionTable" if positive
            else "affixImpactRemovalTable"
        )
        table.setHorizontalHeaderLabels([
            tr("词条"), tr("毕业率变化"), tr("词条值"),
        ])
        self._prepare_table(table)
        header = table.horizontalHeader()
        assert header is not None
        header.setStretchLastSection(False)

        dark_theme = table.palette().color(QPalette.ColorRole.Window).lightness() < 128
        if positive:
            color = QColor("#66BB6A" if dark_theme else "#2E7D32")
        else:
            color = QColor("#EF5350" if dark_theme else "#C62828")
        for row, impact in enumerate(impacts):
            name = impact.name
            if not positive and impact.occurrence_count > 1:
                name = tr("{name}（共{count}条，按损失最大一条）").format(
                    name=name, count=impact.occurrence_count,
                )
            name_item = QTableWidgetItem(name)
            delta_item = QTableWidgetItem(
                f"{impact.graduation_delta * 100:+.2f}%")
            delta_item.setTextAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            delta_item.setForeground(color)
            value_item = QTableWidgetItem(f"{impact.affix_value:g}")
            value_item.setTextAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            table.setItem(row, 0, name_item)
            table.setItem(row, 1, delta_item)
            table.setItem(row, 2, value_item)

        if not impacts:
            table.setRowCount(1)
            empty = QTableWidgetItem(tr("暂无符合条件的词条"))
            empty.setFlags(Qt.ItemFlag.NoItemFlags)
            empty.setForeground(QColor("#777777"))
            table.setItem(0, 0, empty)

        layout.addWidget(table, 1)
        return container
