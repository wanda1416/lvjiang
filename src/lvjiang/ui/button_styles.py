"""Shared button styles for top toolbars and configuration dialogs."""

from __future__ import annotations

from PyQt6.QtWidgets import (
    QDialogButtonBox,
    QMessageBox,
    QPushButton,
    QToolButton,
)

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

COMPACT_ACTION_BUTTON_STYLE = (
    "QPushButton { border: 1px solid palette(highlight); "
    "color: palette(highlight); border-radius: 4px; padding: 2px 7px; "
    "font-weight: 600; }"
    "QPushButton:hover { background-color: palette(midlight); }"
    "QPushButton:pressed, QPushButton:checked { background-color: palette(mid); }"
    "QPushButton:disabled { color: palette(mid); border-color: palette(midlight); }"
)

COMPACT_NEUTRAL_BUTTON_STYLE = (
    "QPushButton { background-color: palette(button); color: palette(button-text); "
    "border: 1px solid palette(mid); border-radius: 4px; padding: 2px 7px; "
    "font-weight: 600; }"
    "QPushButton:hover { background-color: palette(midlight); }"
    "QPushButton:pressed, QPushButton:checked { background-color: palette(mid); }"
    "QPushButton:disabled { color: palette(mid); border-color: palette(midlight); }"
)

COMPACT_DANGER_BUTTON_STYLE = (
    "QPushButton { background-color: palette(button); color: #c62828; "
    "border: 1px solid #c62828; border-radius: 4px; padding: 2px 7px; "
    "font-weight: 600; }"
    "QPushButton:hover { background-color: rgba(198, 40, 40, 0.12); }"
    "QPushButton:pressed { background-color: rgba(198, 40, 40, 0.22); }"
    "QPushButton:disabled { color: palette(mid); border-color: palette(midlight); }"
)

COMPACT_TOOL_BUTTON_STYLE = (
    "QToolButton { background: transparent; color: palette(button-text); "
    "border: 1px solid palette(mid); border-radius: 10px; padding: 0; "
    "font-weight: 600; }"
    "QToolButton:hover { background-color: palette(midlight); }"
    "QToolButton:pressed { background-color: palette(mid); }"
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


def apply_compact_tool_button_style(*buttons: QToolButton | None) -> None:
    """Style compact help/info controls without regular-button padding."""
    for button in buttons:
        if button is not None:
            button.setStyleSheet(COMPACT_TOOL_BUTTON_STYLE)


def apply_compact_button_style(
    *buttons: QPushButton | None,
    variant: str = "neutral",
) -> None:
    """Apply application semantics with reduced padding for dense form rows."""
    styles = {
        "action": COMPACT_ACTION_BUTTON_STYLE,
        "neutral": COMPACT_NEUTRAL_BUTTON_STYLE,
        "danger": COMPACT_DANGER_BUTTON_STYLE,
    }
    style = styles[variant]
    for button in buttons:
        if button is not None:
            button.setStyleSheet(style)


def apply_message_box_button_style(box: QMessageBox) -> None:
    """Apply the shared geometry to buttons created by ``QMessageBox``.

    ``QMessageBox`` owns its buttons, so callers cannot style them until the
    standard buttons have been installed.  Destructive buttons retain the
    danger semantic; dismissive buttons use the neutral variant; all other
    choices use the normal action style.
    """
    danger = {
        QMessageBox.StandardButton.Abort,
        QMessageBox.StandardButton.Discard,
    }
    neutral = {
        QMessageBox.StandardButton.Cancel,
        QMessageBox.StandardButton.Close,
        QMessageBox.StandardButton.Ignore,
        QMessageBox.StandardButton.No,
    }
    for standard in QMessageBox.StandardButton:
        button = box.button(standard)
        if not isinstance(button, QPushButton):
            continue
        variant = (
            "danger" if standard in danger
            else "neutral" if standard in neutral
            else "action"
        )
        apply_button_style(button, variant=variant)


def apply_dialog_button_box_style(box: QDialogButtonBox) -> None:
    """Style standard dialog buttons according to their interaction role."""
    danger = {
        QDialogButtonBox.StandardButton.Abort,
        QDialogButtonBox.StandardButton.Discard,
    }
    neutral = {
        QDialogButtonBox.StandardButton.Cancel,
        QDialogButtonBox.StandardButton.Close,
        QDialogButtonBox.StandardButton.Help,
        QDialogButtonBox.StandardButton.Ignore,
        QDialogButtonBox.StandardButton.No,
        QDialogButtonBox.StandardButton.Reset,
        QDialogButtonBox.StandardButton.RestoreDefaults,
    }
    for standard in QDialogButtonBox.StandardButton:
        button = box.button(standard)
        if not isinstance(button, QPushButton):
            continue
        variant = (
            "danger" if standard in danger
            else "neutral" if standard in neutral
            else "action"
        )
        apply_button_style(button, variant=variant)


def exec_styled_message_box(box: QMessageBox) -> int:
    """Style and execute a fully configured message box."""
    apply_message_box_button_style(box)
    return box.exec()


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
