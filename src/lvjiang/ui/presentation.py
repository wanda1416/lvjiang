"""轻量展示控件与主题辅助函数。"""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPalette
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QTabWidget, QWidget

PILL_STYLE = (
    "border-radius: 9px; padding: 1px 8px; font-size: 11px; font-weight: 600;"
)
TAG_STYLE = (
    "color: white; border-radius: 8px; "
    "font-size: 11px; font-weight: 600; padding: 2px 7px;"
)


def is_dark_theme(widget: QWidget) -> bool:
    """按窗口背景亮度判断深色主题。"""
    return widget.palette().color(QPalette.ColorRole.Window).lightness() < 128


def style_document_tabs(tabs: QTabWidget, object_name: str) -> None:
    """应用圆角面板和加宽标签的文档页签样式。"""
    tabs.setObjectName(object_name)
    tabs.setDocumentMode(True)
    tabs.setStyleSheet(
        f"QTabWidget#{object_name}::pane {{"
        " border: 1px solid palette(midlight); border-radius: 7px; }"
        f"QTabWidget#{object_name} QTabBar::tab {{"
        " padding: 9px 18px; min-width: 120px; }"
    )


def make_pill(text: str, fg: str, bg: str, parent: QWidget | None = None) -> QLabel:
    """创建用于短状态文本的胶囊标签。"""
    label = QLabel(text, parent)
    label.setStyleSheet(f"color: {fg}; background: {bg}; {PILL_STYLE}")
    return label


def highlight_pill(text: str, parent: QWidget | None = None) -> QLabel:
    """创建使用主题强调色的胶囊标签。"""
    return make_pill(
        text, "palette(highlighted-text)", "palette(highlight)", parent
    )


def muted_pill(text: str, parent: QWidget | None = None) -> QLabel:
    """创建使用主题弱化色的胶囊标签。"""
    return make_pill(text, "palette(mid)", "palette(alternate-base)", parent)


def make_tag(text: str, bg: str = "#607D8B", parent: QWidget | None = None) -> QLabel:
    """创建不接收鼠标事件的状态标签。"""
    label = QLabel(text, parent)
    label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
    label.setStyleSheet(f"background-color: {bg}; {TAG_STYLE}")
    return label


def metric_card(
    label: str,
    value: str,
    name: str,
    *,
    success: bool = False,
    object_prefix: str = "metric",
) -> QFrame:
    """创建“标题 + 数值”的指标卡。"""
    card = QFrame()
    card.setObjectName(f"{object_prefix}_{name}")
    card.setProperty("surface", "card")
    row = QHBoxLayout(card)
    row.setContentsMargins(14, 9, 14, 9)
    row.setSpacing(10)
    caption = QLabel(label)
    caption.setProperty("tone", "muted")
    number = QLabel(value)
    number.setObjectName(f"{object_prefix}Value_{name}")
    if success:
        number.setProperty("status", "success")
    number.setStyleSheet("font-size: 17px; font-weight: 700; padding: 2px 6px;")
    row.addWidget(caption)
    row.addStretch()
    row.addWidget(number)
    return card


def set_metric_value(card: QFrame, value: str, *, object_prefix: str = "metric") -> None:
    """更新 :func:`metric_card` 创建的数值标签。"""
    name = card.objectName().removeprefix(f"{object_prefix}_")
    label = card.findChild(QLabel, f"{object_prefix}Value_{name}")
    if label is not None:
        label.setText(value)
