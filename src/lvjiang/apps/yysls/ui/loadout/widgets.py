"""备战方案区共用的小控件与样式：页签、胶囊、指标卡、主题判断。

各页面自己拼的样式串只在这里维护一份；视觉调整改这里即生效。
"""
from __future__ import annotations

from PyQt6.QtWidgets import (
    QFrame,
    QLabel,
    QToolButton,
    QWidget,
)

from .....i18n import tr
from .....ui.presentation import (
    highlight_pill,
    is_dark_theme,
    make_pill,
    make_tag,
    muted_pill,
    style_document_tabs,
)
from .....ui.presentation import (
    metric_card as _metric_card,
)
from .....ui.presentation import (
    set_metric_value as _set_metric_value,
)

__all__ = [
    "HypothesisViewToggle",
    "assumption_pill",
    "highlight_pill",
    "is_dark_theme",
    "make_pill",
    "make_tag",
    "metric_card",
    "muted_pill",
    "set_metric_value",
    "style_document_tabs",
]


class HypothesisViewToggle(QToolButton):
    """切换装备卡片的真实数据与计算假设内存副本。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("hypothesisViewToggle")
        self.setText(tr("假设视图"))
        self.setCheckable(True)
        self.setToolTip(tr(
            "在原始装备与应用当前计算假设后的内存副本之间切换"))
        self.setStyleSheet(
            "QToolButton { border: 1px solid palette(mid); border-radius: 10px;"
            " padding: 3px 10px; color: palette(mid); }"
            "QToolButton:hover { background: palette(midlight); }"
            "QToolButton:checked { background: palette(highlight);"
            " color: palette(highlighted-text); border-color: palette(highlight); }"
        )


# ── 胶囊 ──

#: 计算假设胶囊（琥珀色）
ASSUMPTION_FG = "#B26A00"
ASSUMPTION_BG = "rgba(178, 106, 0, 0.13)"


def assumption_pill(text: str, parent: QWidget | None = None) -> QLabel:
    return make_pill(text, ASSUMPTION_FG, ASSUMPTION_BG, parent)


# ── 指标卡 ──


def metric_card(label: str, value: str, name: str, *,
                success: bool = False) -> QFrame:
    """「标题 …… 大数字」指标卡；``name`` 决定 objectName 供测试与回填定位。"""
    return _metric_card(
        label, value, name, success=success, object_prefix="affixMetric"
    )


def set_metric_value(card: QFrame, value: str) -> None:
    _set_metric_value(card, value, object_prefix="affixMetric")
