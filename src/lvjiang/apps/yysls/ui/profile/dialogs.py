"""Profile 模块独立对话框组件

提供三类对话框：
- HistoryDialog: 查看指定 key 的变更记录
- ask_value_dialog: 通用数值输入 + 来源下拉（可输入新来源）对话框
- ProfileDefinitionDialog: 数据模型定义编辑对话框
"""

from __future__ import annotations

from datetime import datetime

from PyQt6.QtGui import QDoubleValidator, QIntValidator
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from .....i18n import tr
from ...profile.profile_db import db_get_history

# ProfileDefinitionDialog 位于 settings_dialog.py，此处 re-export 便于统一导入。
from .settings_dialog import ProfileDefinitionDialog  # noqa: F401

__all__ = ["HistoryDialog", "ask_value_dialog", "ProfileDefinitionDialog"]

# ─── 历史记录对话框 ────────────────────────────────────────────


class HistoryDialog(QDialog):
    """查看指定 key 的变更记录（最近 50 条）"""

    _TYPE_LABEL = {"tick": tr("定时"), "action": tr("操作"), "override": tr("覆写")}  # runtime tr()

    def __init__(self, user_name: str, model_type: str, key: str, key_label: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"{key_label} — {user_name} " + tr("变更记录"))
        self.resize(820, 420)
        self._setup_ui(user_name, model_type, key)

    def _setup_ui(self, user_name: str, model_type: str, key: str):
        layout = QVBoxLayout(self)

        history = db_get_history(user_name, type_=model_type, key=key, limit=50)

        table = QTableWidget()
        table.setColumnCount(6)
        table.setHorizontalHeaderLabels([
                    tr("时间"), tr("类型"), tr("旧值"), tr("新值"), tr("来源"), tr("详情")])
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        table.setAlternatingRowColors(True)
        vh = table.verticalHeader()
        if vh is not None:
            vh.setVisible(False)

        header = table.horizontalHeader()
        if header is not None:
            # 时间列：固定 140px（够放 "MM-DD HH:MM:SS"）
            header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
            table.setColumnWidth(0, 140)
            # 窄字段列（类型/旧值/新值/来源）：固定宽，不挤占详情列空间
            for col, w in ((1, 60), (2, 70), (3, 70), (4, 100)):
                header.setSectionResizeMode(col, QHeaderView.ResizeMode.Fixed)
                table.setColumnWidth(col, w)
            # 详情列：stretch 占满剩余空间
            header.setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)

        table.setRowCount(len(history))
        for row, rec in enumerate(history):
            # 格式化时间
            raw_ts = rec.get("ts", "")
            try:
                formatted_ts = datetime.fromisoformat(raw_ts).strftime("%m-%d %H:%M:%S")
            except (ValueError, TypeError):
                formatted_ts = raw_ts

            ct = rec.get("change_type", "")
            old_val = rec.get("old_value")
            new_val = rec.get("new_value")
            old_str = (
                str(int(old_val)) if old_val is not None and old_val == int(old_val)
                else str(old_val) if old_val is not None else "—"
            )
            new_str = (
                str(int(new_val)) if new_val is not None and new_val == int(new_val)
                else str(new_val) if new_val is not None else "—"
            )

            table.setItem(row, 0, QTableWidgetItem(formatted_ts))
            table.setItem(row, 1, QTableWidgetItem(tr(self._TYPE_LABEL.get(ct, ct))))
            table.setItem(row, 2, QTableWidgetItem(old_str))
            table.setItem(row, 3, QTableWidgetItem(new_str))
            table.setItem(row, 4, QTableWidgetItem(rec.get("source", "")))
            table.setItem(row, 5, QTableWidgetItem(rec.get("detail", "")))

        layout.addWidget(table)


# ─── 通用数值输入对话框 ────────────────────────────────────────────


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
    sync_default: bool = True,
    source_label: str = tr("来源"),
) -> tuple[float | int, str, bool, bool]:
    """数值输入 + 来源/用途下拉（可输入新词条）的通用对话框

    sync_checkbox: 是否展示「同步变更依赖方」复选框
    sync_default:  复选框的默认勾选状态
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
        validator = QDoubleValidator(float(min_val), 999999.0, 4, value_input)
        value_input.setValidator(validator)
    else:
        validator = QIntValidator(min_val, 999999, value_input)
        value_input.setValidator(validator)

    if initial_value is not None:
        value_input.setText(str(initial_value) if is_float else str(int(initial_value)))
    value_input.setPlaceholderText(tr("请输入数值"))
    layout.addRow(prompt, value_input)

    combo = QComboBox()
    combo.setEditable(True)
    combo.addItems(sources)
    combo.setPlaceholderText(tr("选择或输入新{label}").format(label=source_label))
    layout.addRow(f"{source_label}:", combo)

    sync_check: QCheckBox | None = None
    if sync_checkbox:
        sync_check = QCheckBox(tr("同步变更依赖方"))
        sync_check.setChecked(sync_default)
        sync_check.setToolTip(
            "勾选：按 action 语义处理，触发配置 sync_targets 的同步。\n"
            "取消：按纯覆写语义处理，仅写本 key，不触发任何同步。"
        )
        layout.addRow(sync_check)

    buttons = QDialogButtonBox(
        QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
    )
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
