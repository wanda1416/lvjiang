"""自适应布局 Mix-in —— 响应窗口尺寸变化调整卡片排列。

职责：
- set_embedded_mode：设置显示模式 + 可见性控制
- _layout_attribute_cards：HALF 垂直堆叠 / FULL 2×2 网格
- resizeEvent：仅在半屏模式下触发紧凑适配
- _check_compact_mode：HALF ↔ HALF_COMPACT 切换
- _switch_to_compact_layout：半屏退化（内部网格→单列 + 过滤零值行）
- _switch_to_normal_layout：半屏恢复（内部网格→多列 + 恢复零值行）
- _config_bar_reconfigure：根据模式重排配置栏
- _rearrange_grid_compact：紧凑模式重排网格
- _restore_grid_normal：恢复标准网格
- _align_name_labels：对齐名称标签
- _reset_name_label_widths：重置标签宽度
- _filter_zero_attack_rows：隐藏零值行
- _restore_zero_attack_rows：恢复零值行
"""
from __future__ import annotations

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QGridLayout, QLabel, QSizePolicy, QVBoxLayout

# 显示模式常量
DISPLAY_MODE_FULL = "full"
DISPLAY_MODE_HALF = "half"
DISPLAY_MODE_HALF_COMPACT = "half_compact"

# 退化模式阈值
_COMPACT_THRESHOLD_PX = 532   # 38 字 × 14px
_COMPACT_HYSTERESIS_PX = 36   # 退出退化模式需多 36px


class CombatLayoutMixin:
    """自适应布局 Mix-in。

    需要主类提供：
    - self._display_mode: str
    - self._main_layout: QHBoxLayout
    - self._attack_card, self._judgment_card, self._gain_card, self._damage_card
    - self._attack_grid_items, self._judgment_grid_items, etc.
    - self._attack_grid, self._judgment_grid, etc.
    - self._attr_labels: dict[str, QLabel]
    - self._toolbar_widget, self._attrs_scroll（可见性控制）
    """

    # ── 模式切换 ─────────────────────────────────────────────

    def set_embedded_mode(self, mode: str) -> None:
        """Adapt the reusable content for loadout sidebar/half/full modes.

        Args:
            mode: "sidebar" / "half" / "full"
        """
        # sidebar 仅控制可见性，不改变 _display_mode 也不重排布局
        self._toolbar_widget.setVisible(False)
        collapsed = mode == "sidebar"
        self._select_group.setVisible(not collapsed)
        self._attrs_scroll.setVisible(not collapsed)
        self.setMinimumWidth(0)
        self.setMinimumHeight(0)

        if collapsed:
            return

        old_mode = self._display_mode
        # full → DISPLAY_MODE_FULL, half → DISPLAY_MODE_HALF
        self._display_mode = DISPLAY_MODE_FULL if mode == "full" else DISPLAY_MODE_HALF
        self._layout_attribute_cards(mode)
        self._config_bar_reconfigure()

        # 从退化模式恢复时，重建内部网格并恢复隐藏行
        if old_mode == DISPLAY_MODE_HALF_COMPACT:
            self._restore_zero_attack_rows()
            self._restore_grid_normal()
            self._reset_name_label_widths()

    # ── 卡片外布局 ───────────────────────────────────────────

    @staticmethod
    def _drain_layout(layout) -> None:
        """递归清空布局中的所有子布局。"""
        while layout.count():
            item = layout.takeAt(0)
            if item.layout() is not None:
                CombatLayoutMixin._drain_layout(item.layout())

    def _layout_attribute_cards(self, mode: str) -> None:
        """Half screen is a single ordered flow; full screen is a compact grid."""
        self._drain_layout(self._main_layout)
        cards = (
            self._attack_card, self._judgment_card,
            self._gain_card, self._damage_card,
        )
        for card in cards:
            card.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)

        if mode == "half":
            flow = QVBoxLayout()
            flow.setContentsMargins(0, 0, 0, 0)
            flow.setSpacing(12)
            for card in cards:
                flow.addWidget(card)
            flow.addStretch(1)
            self._main_layout.addLayout(flow)
            return

        # full 模式：2×2 网格
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(12)
        grid.addWidget(self._attack_card, 0, 0)
        grid.addWidget(self._judgment_card, 1, 0)
        grid.addWidget(self._gain_card, 0, 1)
        grid.addWidget(self._damage_card, 1, 1)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        grid.setRowStretch(2, 1)
        self._main_layout.addLayout(grid)

    # ── resizeEvent：仅半屏模式触发紧凑适配 ──────────────────

    def resizeEvent(self, event) -> None:
        """响应宽度变化，在半屏模式下触发紧凑布局适配。"""
        super().resizeEvent(event)
        # 仅在半屏模式下启用自适应（包括完整模式和退化模式）
        if self._display_mode in (DISPLAY_MODE_HALF, DISPLAY_MODE_HALF_COMPACT):
            QTimer.singleShot(0, self._check_compact_mode)

    def _check_compact_mode(self) -> None:
        """检查是否应该切换到退化模式（延迟执行，确保布局稳定）。"""
        if self._display_mode not in (DISPLAY_MODE_HALF, DISPLAY_MODE_HALF_COMPACT):
            return
        width = self.width()
        should_compact = width < _COMPACT_THRESHOLD_PX
        should_normal = width >= _COMPACT_THRESHOLD_PX + _COMPACT_HYSTERESIS_PX
        if should_compact and self._display_mode == DISPLAY_MODE_HALF:
            self._switch_to_compact_layout()
        elif should_normal and self._display_mode == DISPLAY_MODE_HALF_COMPACT:
            self._switch_to_normal_layout()

    # ── HALF ↔ HALF_COMPACT：仅操作卡片内部网格 ──────────────

    def _switch_to_compact_layout(self) -> None:
        """切换到半屏退化模式：过滤零值行 + 内部网格重排为单列 + 右侧对齐。"""
        if self._display_mode == DISPLAY_MODE_HALF_COMPACT:
            return
        self._display_mode = DISPLAY_MODE_HALF_COMPACT
        self._filter_zero_attack_rows()
        self._rearrange_grid_compact()
        self._align_name_labels()

    def _switch_to_normal_layout(self) -> None:
        """从半屏退化模式恢复到半屏完整模式。"""
        if self._display_mode != DISPLAY_MODE_HALF_COMPACT:
            return
        self._display_mode = DISPLAY_MODE_HALF
        self._restore_zero_attack_rows()
        self._restore_grid_normal()
        self._reset_name_label_widths()

    # ── 配置栏重排 ───────────────────────────────────────────

    def _config_bar_reconfigure(self) -> None:
        """根据显示模式重排当前配置区域的字段布局。

        全屏模式：1 行 6 列（流派/基础属性/编辑/弓玦/计算方案/新建）
        半屏模式：2 行 3 列（流派/基础属性/编辑 + 弓玦/计算方案/新建）
        """
        if not hasattr(self, '_select_layout') or not hasattr(self, '_config_fields'):
            return
        sl = self._select_layout
        for widget in self._config_fields:
            sl.removeWidget(widget)
        for col in range(sl.columnCount()):
            sl.setColumnStretch(col, 0)

        if self._display_mode == DISPLAY_MODE_HALF:
            # 半屏：2 行 3 列
            positions = (
                (0, 0), (0, 1), (0, 2),
                (1, 0), (1, 1), (1, 2),
            )
            for widget, (row, col) in zip(self._config_fields, positions, strict=True):
                sl.addWidget(widget, row, col)
            for col in range(3):
                sl.setColumnStretch(col, 1)
        else:
            # 全屏：1 行 6 列
            for col, widget in enumerate(self._config_fields):
                sl.addWidget(widget, 0, col)
            for col in range(6):
                sl.setColumnStretch(col, 1)

    # ── 内部网格重排 ─────────────────────────────────────────

    def _rearrange_grid_compact(self) -> None:
        """将所有网格项重排为单列（跳过不可见 widget）。"""
        for grid_name, items_name in (
            ('_attack_grid', '_attack_grid_items'),
            ('_judgment_grid', '_judgment_grid_items'),
            ('_gain_grid', '_gain_grid_items'),
            ('_damage_grid', '_damage_grid_items'),
        ):
            grid = getattr(self, grid_name, None)
            items = getattr(self, items_name, None)
            if grid is None or items is None:
                continue
            for widget, _, _ in items:
                grid.removeWidget(widget)
            grid.setColumnStretch(0, 0)
            grid.setColumnStretch(1, 0)
            new_row = 0
            for widget, _, _ in items:
                if not widget.isVisible():
                    continue
                grid.addWidget(widget, new_row, 0)
                new_row += 1
            grid.setColumnStretch(0, 1)

    def _restore_grid_normal(self) -> None:
        """恢复所有网格到原始多列布局。"""
        for grid_name, items_name in (
            ('_attack_grid', '_attack_grid_items'),
            ('_judgment_grid', '_judgment_grid_items'),
            ('_gain_grid', '_gain_grid_items'),
            ('_damage_grid', '_damage_grid_items'),
        ):
            grid = getattr(self, grid_name, None)
            items = getattr(self, items_name, None)
            if grid is None or items is None:
                continue
            for widget, _, _ in items:
                grid.removeWidget(widget)
            for widget, row, col in items:
                grid.addWidget(widget, row, col)
            grid.setColumnStretch(0, 1)
            grid.setColumnStretch(1, 1)

    # ── 标签对齐 ─────────────────────────────────────────────

    def _align_name_labels(self) -> None:
        """对齐所有属性名称标签的宽度以实现右侧数值对齐。"""
        name_labels: list[QLabel] = []
        for items_name in (
            '_attack_grid_items', '_judgment_grid_items',
            '_gain_grid_items', '_damage_grid_items',
        ):
            items = getattr(self, items_name, None)
            if items is None:
                continue
            for widget, _, _ in items:
                name_label = widget.findChild(QLabel, "attrName")
                if name_label is not None:
                    name_labels.append(name_label)
        if not name_labels:
            return
        max_width = max(label.sizeHint().width() for label in name_labels)
        for label in name_labels:
            label.setFixedWidth(max_width)

    def _reset_name_label_widths(self) -> None:
        """恢复所有属性名称标签的自动宽度。"""
        for items_name in (
            '_attack_grid_items', '_judgment_grid_items',
            '_gain_grid_items', '_damage_grid_items',
        ):
            items = getattr(self, items_name, None)
            if items is None:
                continue
            for widget, _, _ in items:
                name_label = widget.findChild(QLabel, "attrName")
                if name_label is not None:
                    name_label.setMinimumWidth(0)
                    name_label.setMaximumWidth(16777215)

    # ── 零值行过滤 ───────────────────────────────────────────

    def _filter_zero_attack_rows(self) -> None:
        """半屏退化模式：隐藏攻击属性中 min 和 max 值都为 0 的行。"""
        if not hasattr(self, '_attack_grid_items'):
            return
        attack_pairs = [
            ("min_outer", "max_outer"),
            ("min_mingjin", "max_mingjin"),
            ("min_lieshi", "max_lieshi"),
            ("min_pozhu", "max_pozhu"),
            ("min_qiansi", "max_qiansi"),
            ("min_wuxiang", "max_wuxiang"),
        ]
        for i, (min_field, max_field) in enumerate(attack_pairs):
            min_idx = i * 2
            max_idx = i * 2 + 1
            if min_idx >= len(self._attack_grid_items) or max_idx >= len(self._attack_grid_items):
                break
            min_widget = self._attack_grid_items[min_idx][0]
            max_widget = self._attack_grid_items[max_idx][0]
            min_val = self._attr_labels.get(min_field)
            max_val = self._attr_labels.get(max_field)
            if min_val and max_val:
                try:
                    if float(min_val.text().replace('%', '').replace(',', '')) == 0 and \
                       float(max_val.text().replace('%', '').replace(',', '')) == 0:
                        min_widget.setVisible(False)
                        max_widget.setVisible(False)
                except (ValueError, AttributeError):
                    pass

    def _restore_zero_attack_rows(self) -> None:
        """恢复被隐藏的攻击属性行。"""
        if hasattr(self, '_attack_grid_items'):
            for widget, _, _ in self._attack_grid_items:
                widget.setVisible(True)
