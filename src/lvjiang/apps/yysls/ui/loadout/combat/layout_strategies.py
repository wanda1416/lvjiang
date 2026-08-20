"""布局策略 —— 每种显示模式一个类，消除模式分支散落。

职责：
- CardLayoutStrategy：策略基类，定义卡片排列 / 配置栏重排 / resize 响应接口
- FullCardLayout：全屏 2×2 网格 + 配置栏 1×6
- HalfCardLayout：半屏垂直堆叠 + 配置栏 2×3 + resize 退化监听
- HalfCompactCardLayout：半屏退化（单列 + 零值行过滤 + 标签对齐）

每个策略类独立封装一种模式的全部布局逻辑，修改一个模式不影响其他模式。
"""
from __future__ import annotations

from PyQt6.QtWidgets import QGridLayout, QLabel, QSizePolicy, QVBoxLayout

# 显示模式常量
DISPLAY_MODE_FULL = "full"
DISPLAY_MODE_HALF = "half"
DISPLAY_MODE_HALF_COMPACT = "half_compact"

# 退化模式阈值
_COMPACT_THRESHOLD_PX = 532   # 38 字 × 14px
_COMPACT_HYSTERESIS_PX = 36   # 退出退化模式需多 36px

# 四张卡片的 grid/items 属性名（遍历用）
_GRID_ATTR_NAMES = (
    ('_attack_grid', '_attack_grid_items'),
    ('_judgment_grid', '_judgment_grid_items'),
    ('_gain_grid', '_gain_grid_items'),
    ('_damage_grid', '_damage_grid_items'),
)
_ITEMS_ONLY_NAMES = (
    '_attack_grid_items', '_judgment_grid_items',
    '_gain_grid_items', '_damage_grid_items',
)


class CardLayoutStrategy:
    """布局策略基类。

    所有策略方法接收 ``tab``（CombatAttrsTab 实例）作为上下文，
    通过 tab 访问卡片 widget、网格、标签等属性。
    """

    def arrange_cards(self, tab, cards) -> None:
        """将四张卡片排列到 tab._main_layout。"""
        raise NotImplementedError

    def arrange_config_bar(self, tab) -> None:
        """重排配置栏字段到 tab._select_layout。"""
        raise NotImplementedError

    def on_resize(self, tab) -> None:
        """resizeEvent 回调。默认 no-op。"""

    def on_refresh_display(self, tab) -> None:
        """_refresh_display 结束后的策略特定刷新。默认 no-op。"""

    # ── 公共工具 ──

    @staticmethod
    def drain_layout(layout) -> None:
        """递归清空布局中的所有子布局。"""
        while layout.count():
            item = layout.takeAt(0)
            if item.layout() is not None:
                CardLayoutStrategy.drain_layout(item.layout())


class FullCardLayout(CardLayoutStrategy):
    """全屏模式：2×2 网格 + 配置栏 1 行 6 列。"""

    def arrange_cards(self, tab, cards) -> None:
        self.drain_layout(tab._main_layout)
        for card in cards:
            card.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(12)
        grid.addWidget(tab._attack_card, 0, 0)
        grid.addWidget(tab._judgment_card, 1, 0)
        grid.addWidget(tab._gain_card, 0, 1)
        grid.addWidget(tab._damage_card, 1, 1)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        grid.setRowStretch(2, 1)
        tab._main_layout.addLayout(grid)

    def arrange_config_bar(self, tab) -> None:
        if not hasattr(tab, '_select_layout') or not hasattr(tab, '_config_fields'):
            return
        sl = tab._select_layout
        for widget in tab._config_fields:
            sl.removeWidget(widget)
        for col in range(sl.columnCount()):
            sl.setColumnStretch(col, 0)
        for col, widget in enumerate(tab._config_fields):
            sl.addWidget(widget, 0, col)
        for col in range(6):
            sl.setColumnStretch(col, 1)


class HalfCardLayout(CardLayoutStrategy):
    """半屏模式：垂直堆叠 + 配置栏 2 行 3 列 + resize 退化监听。"""

    def arrange_cards(self, tab, cards) -> None:
        self.drain_layout(tab._main_layout)
        for card in cards:
            card.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        flow = QVBoxLayout()
        flow.setContentsMargins(0, 0, 0, 0)
        flow.setSpacing(12)
        for card in cards:
            flow.addWidget(card)
        flow.addStretch(1)
        tab._main_layout.addLayout(flow)

    def arrange_config_bar(self, tab) -> None:
        if not hasattr(tab, '_select_layout') or not hasattr(tab, '_config_fields'):
            return
        sl = tab._select_layout
        for widget in tab._config_fields:
            sl.removeWidget(widget)
        for col in range(sl.columnCount()):
            sl.setColumnStretch(col, 0)
        positions = (
            (0, 0), (0, 1), (0, 2),
            (1, 0), (1, 1), (1, 2),
        )
        for widget, (row, col) in zip(tab._config_fields, positions, strict=True):
            sl.addWidget(widget, row, col)
        for col in range(3):
            sl.setColumnStretch(col, 1)

    def on_resize(self, tab) -> None:
        """检查宽度，决定是否切换到退化模式。"""
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(0, lambda: self._check_compact(tab))

    @staticmethod
    def _check_compact(tab) -> None:
        width = tab.width()
        if width < _COMPACT_THRESHOLD_PX and tab._display_mode == DISPLAY_MODE_HALF:
            tab._display_mode = DISPLAY_MODE_HALF_COMPACT
            tab._strategy = HalfCompactCardLayout()
            tab._strategy._apply(tab)


class HalfCompactCardLayout(CardLayoutStrategy):
    """半屏退化模式：单列网格 + 零值行过滤 + 名称标签对齐。

    仅在 tab._display_mode == "half_compact" 时激活，
    由 HalfCardLayout.on_resize 切换而来。
    """

    def arrange_cards(self, tab, cards) -> None:
        # 退化模式不重建卡片外布局，由 HalfCardLayout 负责
        pass

    def arrange_config_bar(self, tab) -> None:
        # 配置栏布局与 HalfCardLayout 相同
        HalfCardLayout().arrange_config_bar(tab)

    def on_resize(self, tab) -> None:
        """检查宽度，决定是否从退化模式恢复到半屏完整模式。"""
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(0, lambda: self._check_recover(tab))

    def on_refresh_display(self, tab) -> None:
        """刷新显示后重新应用退化布局（恢复 + 重应用）。"""
        HalfCompactCardLayout._restore_zero_attack_rows(tab)
        HalfCompactCardLayout._restore_grid_normal(tab)
        HalfCompactCardLayout._reset_name_label_widths(tab)
        HalfCompactCardLayout._filter_zero_attack_rows(tab)
        HalfCompactCardLayout._rearrange_grid_compact(tab)
        HalfCompactCardLayout._align_name_labels(tab)

    @staticmethod
    def _check_recover(tab) -> None:
        width = tab.width()
        if width >= _COMPACT_THRESHOLD_PX + _COMPACT_HYSTERESIS_PX:
            tab._display_mode = DISPLAY_MODE_HALF
            tab._strategy = HalfCardLayout()
            HalfCompactCardLayout._restore_zero_attack_rows(tab)
            HalfCompactCardLayout._restore_grid_normal(tab)
            HalfCompactCardLayout._reset_name_label_widths(tab)

    # ── 退化模式专属操作 ──

    def _apply(self, tab) -> None:
        """应用退化布局：过滤零值 + 网格单列 + 标签对齐。"""
        self._filter_zero_attack_rows(tab)
        self._rearrange_grid_compact(tab)
        self._align_name_labels(tab)

    def undo(self, tab) -> None:
        """恢复标准布局：恢复零值行 + 网格多列 + 重置标签。"""
        self._restore_zero_attack_rows(tab)
        self._restore_grid_normal(tab)
        self._reset_name_label_widths(tab)

    # ── 内部网格重排 ──

    @staticmethod
    def _rearrange_grid_compact(tab) -> None:
        """将所有网格项重排为单列（跳过不可见 widget）。"""
        for grid_name, items_name in _GRID_ATTR_NAMES:
            grid = getattr(tab, grid_name, None)
            items = getattr(tab, items_name, None)
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

    @staticmethod
    def _restore_grid_normal(tab) -> None:
        """恢复所有网格到原始多列布局。"""
        for grid_name, items_name in _GRID_ATTR_NAMES:
            grid = getattr(tab, grid_name, None)
            items = getattr(tab, items_name, None)
            if grid is None or items is None:
                continue
            for widget, _, _ in items:
                grid.removeWidget(widget)
            for widget, row, col in items:
                grid.addWidget(widget, row, col)
            grid.setColumnStretch(0, 1)
            grid.setColumnStretch(1, 1)

    # ── 标签对齐 ──

    @staticmethod
    def _align_name_labels(tab) -> None:
        """对齐所有属性名称标签的宽度以实现右侧数值对齐。"""
        name_labels: list[QLabel] = []
        for items_name in _ITEMS_ONLY_NAMES:
            items = getattr(tab, items_name, None)
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

    @staticmethod
    def _reset_name_label_widths(tab) -> None:
        """恢复所有属性名称标签的自动宽度。"""
        for items_name in _ITEMS_ONLY_NAMES:
            items = getattr(tab, items_name, None)
            if items is None:
                continue
            for widget, _, _ in items:
                name_label = widget.findChild(QLabel, "attrName")
                if name_label is not None:
                    name_label.setMinimumWidth(0)
                    name_label.setMaximumWidth(16777215)

    # ── 零值行过滤 ──

    @staticmethod
    def _filter_zero_attack_rows(tab) -> None:
        """隐藏攻击属性中 min 和 max 值都为 0 的行。"""
        if not hasattr(tab, '_attack_grid_items'):
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
            if min_idx >= len(tab._attack_grid_items) or max_idx >= len(tab._attack_grid_items):
                break
            min_widget = tab._attack_grid_items[min_idx][0]
            max_widget = tab._attack_grid_items[max_idx][0]
            min_val = tab._attr_labels.get(min_field)
            max_val = tab._attr_labels.get(max_field)
            if min_val and max_val:
                try:
                    if float(min_val.text().replace('%', '').replace(',', '')) == 0 and \
                       float(max_val.text().replace('%', '').replace(',', '')) == 0:
                        min_widget.setVisible(False)
                        max_widget.setVisible(False)
                except (ValueError, AttributeError):
                    pass

    @staticmethod
    def _restore_zero_attack_rows(tab) -> None:
        """恢复被隐藏的攻击属性行。"""
        if hasattr(tab, '_attack_grid_items'):
            for widget, _, _ in tab._attack_grid_items:
                widget.setVisible(True)
