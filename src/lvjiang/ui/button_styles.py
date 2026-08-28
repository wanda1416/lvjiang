"""Shared button styles for top toolbars and configuration dialogs."""

from __future__ import annotations

from PyQt6.QtWidgets import QPushButton

ACTION_BUTTON_STYLE = (
    "QPushButton { border: 1px solid palette(highlight); "
    "color: palette(highlight); border-radius: 5px; padding: 5px 11px; "
    "font-weight: 600; }"
    "QPushButton:hover { background-color: palette(midlight); }"
    "QPushButton:pressed, QPushButton:checked { background-color: palette(mid); }"
    "QPushButton:disabled { color: palette(mid); border-color: palette(midlight); }"
)

NEUTRAL_BUTTON_STYLE = (
    "QPushButton { background-color: palette(button); color: palette(button-text); "
    "border: 1px solid palette(mid); border-radius: 5px; padding: 5px 11px; "
    "font-weight: 600; }"
    "QPushButton:hover { background-color: palette(midlight); }"
    "QPushButton:pressed, QPushButton:checked { background-color: palette(mid); }"
    "QPushButton:disabled { color: palette(mid); border-color: palette(midlight); }"
)

DANGER_BUTTON_STYLE = (
    "QPushButton { background-color: palette(button); color: #c62828; "
    "border: 1px solid #c62828; border-radius: 5px; padding: 5px 11px; "
    "font-weight: 600; }"
    "QPushButton:hover { background-color: rgba(198, 40, 40, 0.12); }"
    "QPushButton:pressed { background-color: rgba(198, 40, 40, 0.22); }"
    "QPushButton:disabled { color: palette(mid); border-color: palette(midlight); }"
)


def apply_button_style(
    *buttons: QPushButton | None,
    variant: str = "action",
) -> None:
    """Apply one consistent geometry with semantic colour variants."""
    styles = {
        "action": ACTION_BUTTON_STYLE,
        "neutral": NEUTRAL_BUTTON_STYLE,
        "danger": DANGER_BUTTON_STYLE,
    }
    style = styles[variant]
    for button in buttons:
        if button is not None:
            button.setStyleSheet(style)


def fit_button_width(
    *buttons: QPushButton | None,
    minimum: int = 0,
) -> None:
    """给一组按钮定一个统一且不会截断文字的固定宽度。

    直接写死 ``setFixedWidth(70)`` 是平台相关的坑：宽度够不够取决于系统
    UI 字体，Linux 上 CJK 文字量出来五十几像素、Windows 的 Segoe UI 就会
    超出 70，按钮文字被切掉，而开发机是全绿的。

    这里取「各按钮 sizeHint 的最大值」与 ``minimum`` 之间的较大者：既保持
    一排按钮等宽，又保证在任何字体下都装得下。

    ⚠️ 必须在 :func:`apply_button_style` **之后**调用——样式表里的 padding
    参与 sizeHint 计算，先定宽再套样式量出来的是没有内边距的旧尺寸。
    """
    live = [b for b in buttons if b is not None]
    if not live:
        return
    width = max([minimum] + [b.sizeHint().width() for b in live])
    for button in live:
        button.setFixedWidth(width)
