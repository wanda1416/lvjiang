"""自适应布局 Mix-in —— 委托策略对象处理模式差异。

职责：
- 持有当前 CardLayoutStrategy 实例（self._strategy）
- set_embedded_mode：创建对应策略 + 可见性控制
- resizeEvent：委托策略处理
- 对外暴露的方法全部委托给 self._strategy

模式分支集中在策略类中，修改一个模式不影响其他模式。
"""
from __future__ import annotations

from .layout_strategies import (
    DISPLAY_MODE_FULL,
    DISPLAY_MODE_HALF,
    DISPLAY_MODE_HALF_COMPACT,
    CardLayoutStrategy,
    FullCardLayout,
    HalfCardLayout,
    HalfCompactCardLayout,
)

__all__ = [
    "CombatLayoutMixin",
    "DISPLAY_MODE_FULL",
    "DISPLAY_MODE_HALF",
    "DISPLAY_MODE_HALF_COMPACT",
]


class CombatLayoutMixin:
    """自适应布局 Mix-in（薄委托层）。

    需要主类提供：
    - self._display_mode: str
    - self._strategy: CardLayoutStrategy
    - self._main_layout: QHBoxLayout
    - self._attack_card, self._judgment_card, self._gain_card, self._damage_card
    - self._attack_grid_items, self._judgment_grid_items, etc.
    - self._attr_labels: dict[str, QLabel]
    - self._toolbar_widget, self._attrs_scroll, self._select_group（可见性控制）
    """

    _strategy: CardLayoutStrategy  # 主类 __init__ 中初始化

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

        old_strategy = self._strategy
        old_mode = self._display_mode

        # 创建新策略
        if mode == "full":
            self._display_mode = DISPLAY_MODE_FULL
            self._strategy = FullCardLayout()
        else:
            self._display_mode = DISPLAY_MODE_HALF
            self._strategy = HalfCardLayout()

        # 从退化模式恢复时，先还原内部网格再构建新布局
        if isinstance(old_strategy, HalfCompactCardLayout) and old_mode == DISPLAY_MODE_HALF_COMPACT:
            old_strategy.undo(self)

        # 排列卡片 + 重排配置栏
        cards = (
            self._attack_card, self._judgment_card,
            self._gain_card, self._damage_card,
        )
        self._strategy.arrange_cards(self, cards)
        self._strategy.arrange_config_bar(self)

    # ── resizeEvent：委托策略 ────────────────────────────────

    def resizeEvent(self, event) -> None:
        """响应宽度变化，委托策略处理。"""
        super().resizeEvent(event)  # type: ignore[misc]
        self._strategy.on_resize(self)

    # ── 配置栏重排（委托） ───────────────────────────────────

    def _config_bar_reconfigure(self) -> None:
        """根据当前策略重排配置栏。"""
        self._strategy.arrange_config_bar(self)

    # ── 退化模式操作（委托给 HalfCompactCardLayout） ─────────

    def _rearrange_grid_compact(self) -> None:
        """将所有网格项重排为单列。"""
        HalfCompactCardLayout._rearrange_grid_compact(self)

    def _restore_grid_normal(self) -> None:
        """恢复所有网格到原始多列布局。"""
        HalfCompactCardLayout._restore_grid_normal(self)

    def _align_name_labels(self) -> None:
        """对齐所有属性名称标签宽度。"""
        HalfCompactCardLayout._align_name_labels(self)

    def _reset_name_label_widths(self) -> None:
        """恢复属性名称标签自动宽度。"""
        HalfCompactCardLayout._reset_name_label_widths(self)

    def _filter_zero_attack_rows(self) -> None:
        """隐藏零值攻击行。"""
        HalfCompactCardLayout._filter_zero_attack_rows(self)

    def _restore_zero_attack_rows(self) -> None:
        """恢复零值攻击行。"""
        HalfCompactCardLayout._restore_zero_attack_rows(self)
