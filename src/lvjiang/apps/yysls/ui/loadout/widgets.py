"""备战方案区共用的小控件与样式：页签、胶囊、指标卡、主题判断。

各页面自己拼的样式串只在这里维护一份；视觉调整改这里即生效。
"""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPalette
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QTabWidget, QWidget

# ── 主题 ──


def is_dark_theme(widget: QWidget) -> bool:
    """按窗口背景亮度判断深色主题。"""
    return widget.palette().color(QPalette.ColorRole.Window).lightness() < 128


# ── 页签 ──


def style_document_tabs(tabs: QTabWidget, object_name: str) -> None:
    """文档模式页签的统一外观：圆角面板边框 + 加宽的标签。"""
    tabs.setObjectName(object_name)
    tabs.setDocumentMode(True)
    tabs.setStyleSheet(
        f"QTabWidget#{object_name}::pane {{"
        " border: 1px solid palette(midlight); border-radius: 7px; }"
        f"QTabWidget#{object_name} QTabBar::tab {{"
        " padding: 9px 18px; min-width: 120px; }"
    )


# ── 胶囊 ──

PILL_STYLE = (
    "border-radius: 9px; padding: 1px 8px; font-size: 11px; font-weight: 600;"
)
#: 计算假设胶囊（琥珀色）
ASSUMPTION_FG = "#B26A00"
ASSUMPTION_BG = "rgba(178, 106, 0, 0.13)"
#: 卡片头部状态标签（白字色块）
TAG_STYLE = (
    "color: white; border-radius: 8px; "
    "font-size: 11px; font-weight: 600; padding: 2px 7px;"
)


def make_pill(text: str, fg: str, bg: str, parent: QWidget | None = None) -> QLabel:
    """胶囊标签：一个短语一个色块，比整行加粗的提示更容易一眼定位。"""
    label = QLabel(text, parent)
    label.setStyleSheet(f"color: {fg}; background: {bg}; {PILL_STYLE}")
    return label


def assumption_pill(text: str, parent: QWidget | None = None) -> QLabel:
    return make_pill(text, ASSUMPTION_FG, ASSUMPTION_BG, parent)


def highlight_pill(text: str, parent: QWidget | None = None) -> QLabel:
    """强调胶囊（需更换 / 新穿戴 / 需更换 N 件）。"""
    return make_pill(
        text, "palette(highlighted-text)", "palette(highlight)", parent)


def muted_pill(text: str, parent: QWidget | None = None) -> QLabel:
    """弱化胶囊（已穿戴 / 与备战方案一致）。"""
    return make_pill(text, "palette(mid)", "palette(alternate-base)", parent)


def make_tag(text: str, bg: str = "#607D8B", parent: QWidget | None = None) -> QLabel:
    """卡片头部的状态标签（模拟 / 筛选 / 方案），对鼠标透明。"""
    label = QLabel(text, parent)
    label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
    label.setStyleSheet(f"background-color: {bg}; {TAG_STYLE}")
    return label


# ── 指标卡 ──


def metric_card(label: str, value: str, name: str, *,
                success: bool = False) -> QFrame:
    """「标题 …… 大数字」指标卡；``name`` 决定 objectName 供测试与回填定位。"""
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
    if success:
        number.setProperty("status", "success")
    number.setStyleSheet("font-size: 17px; font-weight: 700; padding: 2px 6px;")
    row.addWidget(caption)
    row.addStretch()
    row.addWidget(number)
    return card


def set_metric_value(card: QFrame, value: str) -> None:
    label = card.findChild(
        QLabel, "affixMetricValue_" + card.objectName().removeprefix("affixMetric_"))
    if label is not None:
        label.setText(value)
