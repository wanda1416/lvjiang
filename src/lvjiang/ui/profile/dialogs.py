"""Profile 模块独立对话框组件

提供：
- HistoryDialog: 变更记录（实现在 ``history_dialog.py``，此处 re-export）
- ask_value_dialog: 通用数值输入 + 来源下拉（可输入新来源）对话框
- ProfileDefinitionDialog: 数据模型定义编辑对话框（实现在 ``settings_dialog.py``）
"""

from __future__ import annotations

from PyQt6.QtGui import QDoubleValidator, QIntValidator
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
)

from lvjiang.ui.combo_box import AutoWidthComboBox

from ...i18n import tr
from ..button_styles import apply_dialog_button_box_style

# 变更记录对话框与数据模型定义对话框的实际实现分居两个模块，
# 这里 re-export 让调用方只需认 ``profile.dialogs``。
from .history_dialog import HistoryDialog, HistoryScope  # noqa: F401
from .settings_dialog import ProfileDefinitionDialog  # noqa: F401

__all__ = [
    "HistoryDialog", "HistoryScope", "ask_value_dialog", "ProfileDefinitionDialog",
]


# ─── 通用数值输入对话框 ────────────────────────────────────────────

# 浮点输入的显示精度，与 QDoubleValidator 允许的小数位数共用同一口径
_FLOAT_DECIMALS = 4


def format_number_text(value: float, decimals: int = _FLOAT_DECIMALS) -> str:
    """把数值渲染成定点文本：不用科学计数法，去掉多余的尾随零

    对话框的提示与输入框共用它，避免出现两套口径（提示显示 ``242.39``、
    输入框显示 ``242.39000000000001``）。原始 float 的 ``str()`` 会给出
    17 位有效数字，既超出 ``QDoubleValidator`` 允许的 4 位小数被判为无效，
    又会在编辑器失焦时被 Qt 归一成科学计数法（``2.4239E+02``）。
    """
    text = f"{float(value):.{decimals}f}".rstrip("0").rstrip(".")
    return text or "0"


def ask_value_dialog(
    parent,
    title: str,
    hint: str,
    prompt: str,
    is_float: bool,
    min_val: int,
    sources: list[str],
    initial_value: float | None = None,
    sync_checkbox: bool = False,
    sync_default: bool = False,
    source_label: str = tr("来源"),
) -> tuple[float | int, str, bool, bool]:
    """数值输入 + 来源/用途下拉（可输入新词条）的通用对话框

    sync_checkbox: 是否展示「同步变更依赖方」复选框
    sync_default:  复选框的默认勾选状态（默认不勾选＝纯覆写语义，仅写本 key）
    source_label:  下拉行标签（增加用「来源」，减少用「用途」）

    Returns: (value, source, sync_checked, ok)
        sync_checked 仅在 sync_checkbox=True 时有意义，否则始终为 True。
    """
    dialog = QDialog(parent)
    dialog.setWindowTitle(title)
    dialog.setMinimumWidth(320)
    layout = QFormLayout(dialog)

    hint_label = QLabel(hint)
    layout.addRow(hint_label)

    value_input = QLineEdit()
    validator: QDoubleValidator | QIntValidator
    if is_float:
        validator = QDoubleValidator(
            float(min_val), 999999.0, _FLOAT_DECIMALS, value_input)
        # QDoubleValidator 默认是科学计数法记法：文本一旦被判无效（例如
        # 超出小数位数的长小数），编辑器失焦时会把它归一成 2.4239E+02。
        # 这里的输入都是普通数量值，定点记法既好读也不会被改写。
        validator.setNotation(QDoubleValidator.Notation.StandardNotation)
        value_input.setValidator(validator)
    else:
        validator = QIntValidator(min_val, 999999, value_input)
        value_input.setValidator(validator)

    if initial_value is not None:
        if is_float:
            value_input.setText(format_number_text(initial_value))
        else:
            value_input.setText(str(int(initial_value)))
    layout.addRow(prompt, value_input)

    combo = AutoWidthComboBox()
    combo.setEditable(True)
    combo.addItems(sources)
    layout.addRow(f"{source_label}:", combo)

    sync_check: QCheckBox | None = None
    if sync_checkbox:
        sync_check = QCheckBox(tr("同步变更依赖方"))
        sync_check.setChecked(sync_default)
        sync_check.setToolTip(
            tr(
                "勾选：按 action 语义处理，触发配置 sync_targets 的同步。\n"
                "取消：按纯覆写语义处理，仅写本 key，不触发任何同步。"
            )
        )
        layout.addRow(sync_check)

    buttons = QDialogButtonBox(
        QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
    )
    apply_dialog_button_box_style(buttons)
    buttons.rejected.connect(dialog.reject)
    layout.addRow(buttons)

    parsed_value: list[float | int] = [0.0 if is_float else 0]

    def on_accept() -> None:
        text = value_input.text().strip()
        if not text:
            QMessageBox.warning(dialog, tr("输入错误"), tr("请输入{field}").format(field=prompt.rstrip(':：')))
            value_input.setFocus()
            return
        try:
            value = float(text) if is_float else int(text)
        except ValueError:
            QMessageBox.warning(dialog, tr("输入错误"), tr("{field}必须是有效数字").format(field=prompt.rstrip(':：')))
            value_input.setFocus()
            return
        if value < min_val:
            QMessageBox.warning(dialog, tr("输入错误"), tr("{field}不能小于 {min}").format(field=prompt.rstrip(':：'), min=min_val))
            value_input.setFocus()
            return
        if value > 999999:
            QMessageBox.warning(dialog, tr("输入错误"), tr("{field}不能大于 999999").format(field=prompt.rstrip(':：')))
            value_input.setFocus()
            return
        parsed_value[0] = value
        dialog.accept()

    buttons.accepted.connect(on_accept)

    if dialog.exec():
        sync_checked = sync_check.isChecked() if sync_check is not None else True
        return parsed_value[0], combo.currentText().strip(), sync_checked, True
    return 0, "", sync_default, False
