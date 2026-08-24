"""当前配装词条边际收益对话框。"""
from __future__ import annotations

from collections.abc import Callable

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor, QPalette
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .....i18n import tr
from ...config.tune_slots import SLOT_LABELS
from ...core.graduation.affix_impact import (
    AffixCombinationResult,
    AffixImpact,
    AffixImpactReport,
    AffixReplacementSuggestion,
)

JointAnalyzer = Callable[[tuple[str, ...]], AffixCombinationResult]

_JOINT_BUTTON_STYLE = (
    "QPushButton { background: palette(highlight); color: palette(highlighted-text); "
    "border: 1px solid palette(highlight); border-radius: 5px; padding: 6px 13px; "
    "font-weight: 700; }"
    "QPushButton:hover { border-color: palette(text); }"
    "QPushButton:pressed { background: palette(dark); }"
    "QPushButton:disabled { background: palette(midlight); color: palette(mid); "
    "border-color: palette(midlight); }"
)


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


class AffixImpactDialog(QDialog):
    """展示可执行的培养建议和理论词条敏感度。"""

    def __init__(
        self,
        report: AffixImpactReport,
        school: str,
        scheme: str,
        parent: QWidget | None = None,
        *,
        joint_analyzer: JointAnalyzer | None = None,
    ) -> None:
        super().__init__(parent)
        self._joint_analyzer = joint_analyzer
        self._slot_checkboxes: dict[str, QCheckBox] = {}
        self._joint_timer = QTimer(self)
        self._joint_timer.setSingleShot(True)
        self._joint_timer.setInterval(120)
        self._joint_timer.timeout.connect(self._calculate_joint)
        self._actionable_suggestions = tuple(
            item for item in report.suggestions
            if item.graduation_delta > 1e-9
        )
        self._blocked_equipment = report.blocked_equipment
        self.setWindowTitle(tr("词条培养分析"))
        self.setMinimumSize(900, 600)
        self.resize(1040, 700)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(12)

        context = QLabel(
            tr("{school}  ·  {scheme}  ·  基于当前备战方案").format(
                school=school, scheme=scheme,
            )
        )
        context.setProperty("tone", "muted")
        context.setStyleSheet("font-size: 12px;")
        layout.addWidget(context)

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

        tabs = QTabWidget()
        tabs.setObjectName("affixAnalysisTabs")
        tabs.setDocumentMode(True)
        tabs.setStyleSheet(
            "QTabWidget#affixAnalysisTabs::pane {"
            " border: 1px solid palette(midlight); border-radius: 7px; }"
            "QTabWidget#affixAnalysisTabs QTabBar::tab {"
            " padding: 9px 18px; min-width: 120px; }"
        )
        tabs.addTab(
            self._suggestion_tab(),
            tr("培养建议  {count}").format(
                count=len(self._actionable_suggestions),
            ),
        )
        tabs.addTab(self._sensitivity_tab(report), tr("理论敏感度"))
        layout.addWidget(tabs, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close_button = buttons.button(QDialogButtonBox.StandardButton.Close)
        if close_button is not None:
            close_button.setText(tr("完成"))
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

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

    def _suggestion_tab(self) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(14, 14, 14, 14)
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
        self._joint_button.setStyleSheet(_JOINT_BUTTON_STYLE)
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
            tr("以下是理论敏感度，不代表游戏中可以单独获得或失去一条词条。"
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
            tr("理论新增一条词条的收益"), report.additions, positive=True,
        ), 1)
        columns.addWidget(self._section(
            tr("理论扣除一条当前词条的损失"), report.removals, positive=False,
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
