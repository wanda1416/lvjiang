"""自动调律装备总览的独立非模态窗口。"""
from __future__ import annotations

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .....i18n import tr
from ...config.tune_slots import SLOT_LABELS
from ...core.tuning_rules import RATING_LABELS
from .result_card import TuningResultCard
from .result_store import (
    RESULT_RECYCLED,
    RESULT_RESET,
    RESULT_SKIPPED,
    RESULT_TUNED,
    RESULT_TUNED_RECYCLED,
    TuningEquipmentResult,
    TuningResultStore,
)

_NAV_SLOTS = (
    ("weapon", tr("武器")),
    ("ring", tr("环")),
    ("pendant", tr("佩")),
    ("head", tr("冠胄")),
    ("chest", tr("胸甲")),
    ("leg", tr("胫甲")),
    ("wrist", tr("腕甲")),
)

_RESULT_FILTERS = (
    ("all", tr("全部结果")),
    ("tuned", tr("已调律")),
    ("recycled", tr("已回收")),
    ("skipped", tr("已跳过")),
    ("reset", tr("重置相关")),
)

_RATING_FILTERS = (
    ("all", tr("全部评级")),
    ("unrated", tr("未评级")),
    ("junk", RATING_LABELS["junk"]),
    ("normal", RATING_LABELS["normal"]),
    ("excellent", RATING_LABELS["excellent"]),
    ("top", RATING_LABELS["top"]),
)

_FILTER_BUTTON_STYLE = (
    "QToolButton { border: 1px solid palette(midlight);"
    " border-radius: 11px; padding: 4px 10px; }"
    "QToolButton:checked { background: palette(highlight);"
    " color: palette(highlighted-text);"
    " border-color: palette(highlight); }"
)


class TuningResultsDialog(QDialog):
    """按处理顺序查看当前运行的装备处理结果。"""

    MIN_COLUMNS = 2
    MAX_COLUMNS = 4
    MIN_CARD_WIDTH = 225

    def __init__(self, store: TuningResultStore, parent=None):
        super().__init__(parent)
        self._store = store
        self._selected_slot: str | None = None
        self._result_filter = "all"
        self._rating_filter = "all"
        self._column_count = self.MAX_COLUMNS
        self._slot_buttons: dict[str, QToolButton] = {}
        self._filter_buttons: dict[str, QToolButton] = {}
        self._rating_filter_buttons: dict[str, QToolButton] = {}
        self._cards: list[TuningResultCard] = []
        self.setWindowTitle(tr("调律装备总览"))
        self.setWindowModality(Qt.WindowModality.NonModal)
        self.resize(1180, 760)
        self.setMinimumSize(820, 520)
        self._build_ui()
        store.result_added.connect(self._on_result_added)
        store.reset.connect(self._on_store_reset)
        self._refresh_counts()
        self._rebuild_cards()

    @property
    def selected_slot(self) -> str | None:
        return self._selected_slot

    @property
    def result_filter(self) -> str:
        return self._result_filter

    @property
    def rating_filter(self) -> str:
        return self._rating_filter

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(12)

        header = QHBoxLayout()
        title = QLabel(tr("调律装备总览"))
        title.setStyleSheet("font-size: 18px; font-weight: 700;")
        header.addWidget(title)
        header.addStretch(1)
        self._stat_labels: dict[str, QLabel] = {}
        for key in ("total", "tuned", "recycled", "skipped", "reset"):
            stat_label = QLabel()
            stat_label.setObjectName(f"resultStat_{key}")
            stat_label.setStyleSheet(
                "background: palette(alternate-base); border-radius: 10px;"
                "padding: 4px 10px; color: palette(mid);"
            )
            self._stat_labels[key] = stat_label
            header.addWidget(stat_label)
        root.addLayout(header)

        body = QHBoxLayout()
        body.setSpacing(12)
        root.addLayout(body, 1)

        nav = QFrame()
        nav.setObjectName("resultNav")
        nav.setFixedWidth(184)
        nav.setStyleSheet(
            "QFrame#resultNav { background: palette(alternate-base);"
            " border-radius: 8px; }"
            "QToolButton { border: none; border-radius: 6px; padding: 8px 10px;"
            " text-align: left; min-height: 24px; }"
            "QToolButton:hover { background: palette(midlight); }"
            "QToolButton:checked { background: palette(highlight);"
            " color: palette(highlighted-text); font-weight: 600; }"
        )
        nav_layout = QVBoxLayout(nav)
        nav_layout.setContentsMargins(8, 10, 8, 10)
        nav_layout.setSpacing(3)
        nav_title = QLabel(tr("装备部位"))
        nav_title.setStyleSheet(
            "font-size: 11px; color: palette(mid); padding: 4px;")
        nav_layout.addWidget(nav_title)

        self._slot_group = QButtonGroup(self)
        self._slot_group.setExclusive(True)
        self._all_button = self._make_nav_button("all", tr("全部"))
        self._all_button.setChecked(True)
        nav_layout.addWidget(self._all_button)
        for key, slot_label in _NAV_SLOTS[:1]:
            button = self._make_nav_button(key, slot_label)
            self._slot_buttons[key] = button
            nav_layout.addWidget(button)
        nav_layout.addWidget(self._nav_section_label(tr("首饰")))
        for key, slot_label in _NAV_SLOTS[1:3]:
            button = self._make_nav_button(key, slot_label)
            self._slot_buttons[key] = button
            nav_layout.addWidget(button)
        nav_layout.addWidget(self._nav_section_label(tr("防具")))
        for key, slot_label in _NAV_SLOTS[3:]:
            button = self._make_nav_button(key, slot_label)
            self._slot_buttons[key] = button
            nav_layout.addWidget(button)
        nav_layout.addStretch(1)
        order_hint = QLabel(tr("卡片始终按处理顺序展示"))
        order_hint.setWordWrap(True)
        order_hint.setStyleSheet(
            "font-size: 10px; color: palette(mid); padding: 4px;")
        nav_layout.addWidget(order_hint)
        body.addWidget(nav)

        main = QVBoxLayout()
        main.setSpacing(10)
        body.addLayout(main, 1)

        filters = QVBoxLayout()
        filters.setSpacing(6)

        controls = QHBoxLayout()
        controls.setSpacing(5)
        result_filter_label = QLabel(tr("处理结果"))
        result_filter_label.setStyleSheet(
            "font-size: 11px; color: palette(mid); margin-right: 3px;")
        controls.addWidget(result_filter_label)
        self._filter_group = QButtonGroup(self)
        self._filter_group.setExclusive(True)
        for key, filter_label in _RESULT_FILTERS:
            button = QToolButton()
            button.setText(filter_label)
            button.setCheckable(True)
            button.setProperty("resultFilter", True)
            button.setStyleSheet(_FILTER_BUTTON_STYLE)
            button.clicked.connect(
                lambda _checked=False, value=key:
                self._select_result_filter(value)
            )
            self._filter_group.addButton(button)
            self._filter_buttons[key] = button
            controls.addWidget(button)
        self._filter_buttons["all"].setChecked(True)
        controls.addStretch(1)
        self._search = QLineEdit()
        self._search.setClearButtonEnabled(True)
        self._search.setPlaceholderText(tr("搜索装备、类型或词条"))
        self._search.setMaximumWidth(280)
        self._search.textChanged.connect(self._on_search_changed)
        controls.addWidget(self._search)
        filters.addLayout(controls)

        rating_controls = QHBoxLayout()
        rating_controls.setSpacing(5)
        rating_filter_label = QLabel(tr("装备评级"))
        rating_filter_label.setStyleSheet(
            "font-size: 11px; color: palette(mid); margin-right: 3px;")
        rating_controls.addWidget(rating_filter_label)
        self._rating_filter_group = QButtonGroup(self)
        self._rating_filter_group.setExclusive(True)
        for key, filter_label in _RATING_FILTERS:
            button = QToolButton()
            button.setText(filter_label)
            button.setCheckable(True)
            button.setProperty("ratingFilter", True)
            button.setStyleSheet(_FILTER_BUTTON_STYLE)
            button.clicked.connect(
                lambda _checked=False, value=key:
                self._select_rating_filter(value)
            )
            self._rating_filter_group.addButton(button)
            self._rating_filter_buttons[key] = button
            rating_controls.addWidget(button)
        self._rating_filter_buttons["all"].setChecked(True)
        rating_controls.addStretch(1)
        filters.addLayout(rating_controls)
        main.addLayout(filters)

        content = QHBoxLayout()
        content.setSpacing(10)
        main.addLayout(content, 1)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self._container = QWidget()
        self._grid = QGridLayout(self._container)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setHorizontalSpacing(10)
        self._grid.setVerticalSpacing(10)
        self._grid.setRowStretch(9999, 1)
        self._empty_label = QLabel(tr("本轮还没有可展示的装备处理结果"))
        self._empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_label.setStyleSheet(
            "font-size: 13px; color: palette(mid); padding: 40px;")
        self._scroll.setWidget(self._container)
        content.addWidget(self._scroll, 1)

        self._detail_panel = self._build_detail_panel()
        self._detail_panel.hide()
        content.addWidget(self._detail_panel)

    def _make_nav_button(self, key: str, label: str) -> QToolButton:
        button = QToolButton()
        button.setText(label)
        button.setCheckable(True)
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        value = None if key == "all" else key
        button.clicked.connect(
            lambda _checked=False, slot=value: self._select_slot(slot))
        self._slot_group.addButton(button)
        return button

    @staticmethod
    def _nav_section_label(text: str) -> QLabel:
        label = QLabel(text)
        label.setStyleSheet(
            "font-size: 10px; color: palette(mid); padding: 9px 4px 2px;")
        return label

    def _build_detail_panel(self) -> QFrame:
        panel = QFrame()
        panel.setObjectName("resultDetail")
        panel.setFixedWidth(300)
        panel.setStyleSheet(
            "QFrame#resultDetail { background: palette(alternate-base);"
            " border-radius: 8px; }"
        )
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(14, 12, 14, 12)
        header = QHBoxLayout()
        title = QLabel(tr("装备详情"))
        title.setStyleSheet("font-size: 14px; font-weight: 700;")
        header.addWidget(title)
        header.addStretch(1)
        close = QPushButton("×")
        close.setFixedSize(26, 26)
        close.setStyleSheet(
            "QPushButton { background: transparent; border: none;"
            " border-radius: 5px; padding: 0; font-size: 17px; }"
            "QPushButton:hover { background: palette(midlight); }"
            "QPushButton:pressed { background: palette(mid); }"
        )
        close.clicked.connect(self._close_detail)
        header.addWidget(close)
        layout.addLayout(header)
        self._detail_title = QLabel()
        self._detail_title.setWordWrap(True)
        self._detail_title.setStyleSheet("font-size: 15px; font-weight: 700;")
        layout.addWidget(self._detail_title)
        self._detail_meta = QLabel()
        self._detail_meta.setWordWrap(True)
        self._detail_meta.setStyleSheet(
            "font-size: 11px; color: palette(mid);")
        layout.addWidget(self._detail_meta)
        self._detail_affixes = QLabel()
        self._detail_affixes.setWordWrap(True)
        self._detail_affixes.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self._detail_affixes)
        self._detail_rounds_title = QLabel(tr("逐轮狗粮决策"))
        self._detail_rounds_title.setStyleSheet(
            "font-size: 11px; font-weight: 700;")
        layout.addWidget(self._detail_rounds_title)
        self._detail_rounds = QPlainTextEdit()
        self._detail_rounds.setReadOnly(True)
        self._detail_rounds.setMaximumHeight(190)
        self._detail_rounds.setFrameShape(QFrame.Shape.NoFrame)
        self._detail_rounds.setStyleSheet(
            "font-size: 11px; background: palette(base); border-radius: 5px;")
        layout.addWidget(self._detail_rounds)
        layout.addStretch(1)
        reason_title = QLabel(tr("处理意见"))
        reason_title.setStyleSheet("font-size: 11px; font-weight: 700;")
        layout.addWidget(reason_title)
        self._detail_reason = QLabel()
        self._detail_reason.setWordWrap(True)
        self._detail_reason.setMaximumWidth(272)
        self._detail_reason.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self._detail_reason)
        return panel

    def _select_slot(self, slot_key: str | None) -> None:
        self._selected_slot = slot_key
        self._all_button.setChecked(slot_key is None)
        for key, button in self._slot_buttons.items():
            button.setChecked(key == slot_key)
        self._detail_panel.hide()
        self._rebuild_cards()
        QTimer.singleShot(0, self._update_column_count)

    def _select_result_filter(self, value: str) -> None:
        self._result_filter = value
        for key, button in self._filter_buttons.items():
            button.setChecked(key == value)
        self._detail_panel.hide()
        self._rebuild_cards()
        QTimer.singleShot(0, self._update_column_count)

    def _select_rating_filter(self, value: str) -> None:
        self._rating_filter = value
        for key, button in self._rating_filter_buttons.items():
            button.setChecked(key == value)
        self._detail_panel.hide()
        self._rebuild_cards()
        QTimer.singleShot(0, self._update_column_count)

    def _on_search_changed(self, _text: str) -> None:
        self._detail_panel.hide()
        self._rebuild_cards()
        QTimer.singleShot(0, self._update_column_count)

    @staticmethod
    def _slot_matches(item: TuningEquipmentResult, slot: str | None) -> bool:
        if slot is None:
            return True
        if slot == "weapon":
            return item.slot_key in {"main_weapon", "sub_weapon"}
        return item.slot_key == slot

    def _result_matches(self, item: TuningEquipmentResult) -> bool:
        if self._result_filter == "tuned":
            return item.result in {RESULT_TUNED, RESULT_TUNED_RECYCLED}
        if self._result_filter == "recycled":
            return item.result in {RESULT_RECYCLED, RESULT_TUNED_RECYCLED}
        if self._result_filter == "skipped":
            return item.result == RESULT_SKIPPED
        if self._result_filter == "reset":
            return bool(item.reset_outcome) or item.result == RESULT_RESET
        return True

    def _rating_matches(self, item: TuningEquipmentResult) -> bool:
        if self._rating_filter == "all":
            return True
        rating = str(item.final_rating or "").strip()
        if self._rating_filter == "unrated":
            return not rating
        # 历史数据通常保存稳定的英文 key；同时兼容曾保存显示文本的记录。
        rating = {label: key for key, label in RATING_LABELS.items()}.get(
            rating, rating)
        return rating == self._rating_filter

    def _search_matches(self, item: TuningEquipmentResult) -> bool:
        query = self._search.text().strip().casefold()
        if not query:
            return True
        fields = [item.name, item.type, item.reason]
        fields.extend(
            str(affix.get("name") or "") for affix in item.final_affixes)
        return any(query in value.casefold() for value in fields)

    def _visible_results(self) -> tuple[TuningEquipmentResult, ...]:
        # 只做稳定过滤，不调用 sorted；任何筛选结果均保持
        # store.results 的 equipment_id 处理顺序。
        return tuple(
            item for item in self._store.results
            if self._slot_matches(item, self._selected_slot)
            and self._result_matches(item)
            and self._rating_matches(item)
            and self._search_matches(item)
        )

    def _refresh_counts(self) -> None:
        results = self._store.results
        tuned = sum(
            item.result in {RESULT_TUNED, RESULT_TUNED_RECYCLED}
            for item in results)
        recycled = sum(
            item.result in {RESULT_RECYCLED, RESULT_TUNED_RECYCLED}
            for item in results)
        skipped = sum(item.result == RESULT_SKIPPED for item in results)
        reset = sum(bool(item.reset_outcome) for item in results)
        self._stat_labels["total"].setText(
            tr("共 {count} 件").format(count=len(results)))
        self._stat_labels["tuned"].setText(
            tr("调律 {count}").format(count=tuned))
        self._stat_labels["recycled"].setText(
            tr("回收 {count}").format(count=recycled))
        self._stat_labels["skipped"].setText(
            tr("跳过 {count}").format(count=skipped))
        self._stat_labels["reset"].setText(
            tr("重置 {count}").format(count=reset))

        self._all_button.setText(f"{tr('全部')}   {len(results)}")
        for key, slot_label in _NAV_SLOTS:
            count = sum(self._slot_matches(item, key) for item in results)
            self._slot_buttons[key].setText(f"{slot_label}   {count}")

    def _on_result_added(self, _result: TuningEquipmentResult) -> None:
        self._refresh_counts()
        self._rebuild_cards()

    def _on_store_reset(self) -> None:
        self._selected_slot = None
        self._result_filter = "all"
        self._rating_filter = "all"
        self._search.clear()
        self._all_button.setChecked(True)
        self._filter_buttons["all"].setChecked(True)
        self._rating_filter_buttons["all"].setChecked(True)
        self._detail_panel.hide()
        self._refresh_counts()
        self._rebuild_cards()

    def _clear_cards(self) -> None:
        for card in self._cards:
            self._grid.removeWidget(card)
            card.deleteLater()
        self._cards.clear()
        self._grid.removeWidget(self._empty_label)

    def _append_card(self, result: TuningEquipmentResult) -> None:
        index = len(self._cards)
        row, column = divmod(index, self._column_count)
        card = TuningResultCard(result, self._container)
        card.clicked.connect(self._show_detail)
        self._cards.append(card)
        self._grid.addWidget(card, row, column)

    def _rebuild_cards(self) -> None:
        self._clear_cards()
        results = self._visible_results()
        for result in results:
            self._append_card(result)
        for column in range(self.MAX_COLUMNS):
            self._grid.setColumnStretch(
                column, 1 if column < self._column_count else 0)
        if results:
            self._empty_label.hide()
        else:
            self._grid.addWidget(
                self._empty_label, 0, 0, 1, self._column_count)
            self._empty_label.show()

    def _show_detail(self, result: TuningEquipmentResult) -> None:
        slot = SLOT_LABELS.get(result.slot_key, result.slot_key)
        self._detail_title.setText(f"#{result.equipment_id:04d}  {result.name}")
        self._detail_meta.setText(
            f"{slot} · {result.type} · Lv{result.level or '-'} · "
            f"{result.rounds} {tr('轮')}"
        )
        affixes = [
            f"{affix.get('name') or '?'}  {affix.get('value', '')}"
            f"{affix.get('unit') or ''}"
            for affix in result.final_affixes
        ]
        self._detail_affixes.setText(
            "\n".join(affixes) or tr("无可展示词条"))
        rounds = self._format_round_details(result)
        self._detail_rounds_title.setVisible(bool(rounds))
        self._detail_rounds.setVisible(bool(rounds))
        self._detail_rounds.setPlainText(rounds)
        self._detail_reason.setText(result.reason)
        self._detail_panel.show()
        QTimer.singleShot(0, self._update_column_count)

    @staticmethod
    def _format_round_details(result: TuningEquipmentResult) -> str:
        lines: list[str] = []
        for detail in result.round_details:
            round_no = detail.get("round_no", "-")
            food = detail.get("food_used") or tr("不添加狗粮")
            completed = detail.get("completed", True) is not False
            new_affix = detail.get("new_affix_data") or {}
            if completed and new_affix.get("name"):
                value = new_affix.get("value", "")
                unit = new_affix.get("unit", "")
                outcome = f" → {new_affix['name']} {value}{unit}".rstrip()
            elif completed:
                outcome = ""
            else:
                outcome = f" · {tr('本轮未执行调律')}"
            lines.append(
                tr("第 {n} 轮：{food}{outcome}").format(
                    n=round_no, food=food, outcome=outcome))
            reason = str(detail.get("food_reason") or "").strip()
            if reason:
                lines.append(f"  {reason}")
        return "\n".join(lines)

    def _close_detail(self) -> None:
        self._detail_panel.hide()
        QTimer.singleShot(0, self._update_column_count)

    def _update_column_count(self) -> None:
        viewport = self._scroll.viewport()
        width = max(1, viewport.width() if viewport is not None else 1)
        spacing = self._grid.horizontalSpacing()
        columns = max(self.MIN_COLUMNS, min(
            self.MAX_COLUMNS,
            (width + spacing) // (self.MIN_CARD_WIDTH + spacing),
        ))
        if columns != self._column_count:
            self._column_count = columns
            self._rebuild_cards()

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().resizeEvent(event)
        self._update_column_count()

    def showEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().showEvent(event)
        QTimer.singleShot(0, self._update_column_count)
