"""配置界面共用的尺寸与布局辅助函数。"""

from __future__ import annotations

from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QSizePolicy,
    QWidget,
)

from .combo_box import (
    combo_contents_width,
    fit_combo_popup_to_contents,
    fit_combo_to_contents,
)

__all__ = [
    "combo_contents_width",
    "fit_combo_popup_to_contents",
    "fit_combo_to_contents",
    "mark_navigation_list",
    "configure_navigation_list",
    "config_field_card",
    "navigation_width_for_chars",
]


def mark_navigation_list(nav: QListWidget) -> None:
    """打上 navigation 标记，并立即让样式表对它生效。

    主题样式表里有 ``QListWidget[navigation="true"] { font-size: 14px }``，
    这条规则要等两件事都成立才落到控件上：``navigation`` 属性已设、控件已被
    polish。在那之前 ``fontMetrics()`` 返回的还是应用默认字体，比最终字体小
    一号——拿它算出来的宽度会偏窄（8 个汉字：90px vs 99px）。

    所以凡是要按字数算宽度的导航列表，都得先过这里再量。
    """
    nav.setProperty("navigation", True)
    nav.ensurePolished()


def configure_navigation_list(
    nav: QListWidget, *, minimum_width: int
) -> None:
    """Apply the shared spacious navigation-list presentation."""
    mark_navigation_list(nav)
    nav.setMinimumWidth(minimum_width)
    nav.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
    nav.setSpacing(2)


def config_field_card(
    label: str,
    editor: QWidget,
    *,
    label_width: int = 150,
) -> QFrame:
    """Create the spacious single-setting row used by configuration panels.

    A plain ``QFormLayout`` visually collapses several unrelated settings into
    one dense block.  The established game-config presentation gives every
    setting its own alternate-surface region, so labels remain easy to scan
    even when the editor contains many similarly named fields.
    """
    frame = QFrame()
    frame.setProperty("configFieldCard", True)
    frame.setStyleSheet(
        'QFrame[configFieldCard="true"] {'
        ' background-color: palette(alternate-base);'
        ' border-radius: 6px;'
        '}'
    )
    row = QHBoxLayout(frame)
    row.setContentsMargins(14, 9, 14, 9)
    row.setSpacing(18)

    title = QLabel(label)
    title.setMinimumWidth(label_width)
    row.addWidget(title)

    editor.setSizePolicy(
        QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    row.addWidget(editor, stretch=1)
    return frame


def navigation_width_for_chars(nav: QListWidget, count: int) -> int:
    """按全角汉字数量计算导航栏宽度，并计入条目内边距与边框。

    量的是控件**当前**字体，所以必须在 :func:`mark_navigation_list` 之后调用，
    否则量到的是默认字体，理由见那边的说明。
    """
    return nav.fontMetrics().horizontalAdvance("汉" * count) + 32
