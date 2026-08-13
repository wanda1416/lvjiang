"""Profile 模块独立对话框组件

提供三类对话框：
- HistoryDialog: 查看指定 key 的变更记录
- ask_value_dialog: 通用数值输入 + 来源下拉（可输入新来源）对话框
- ProfileDefinitionDialog: 数据模型定义编辑对话框
"""

from __future__ import annotations

from datetime import datetime

from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QHeaderView,
    QLabel,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from ...profile.profile_db import db_get_history

# ProfileDefinitionDialog 位于 settings_dialog.py，此处 re-export 便于统一导入。
from .settings_dialog import ProfileDefinitionDialog  # noqa: F401

__all__ = ["HistoryDialog", "ask_value_dialog", "ProfileDefinitionDialog"]

# ─── 历史记录对话框 ────────────────────────────────────────────


class HistoryDialog(QDialog):
    """查看指定 key 的变更记录（最近 50 条）"""

    _TYPE_LABEL = {"tick": "定时", "action": "操作", "override": "覆写"}

    def __init__(self, user_name: str, model_type: str, key: str, key_label: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"{key_label} — {user_name} 变更记录")
        self.resize(820, 420)
        self._setup_ui(user_name, model_type, key)

    def _setup_ui(self, user_name: str, model_type: str, key: str):
        layout = QVBoxLayout(self)

        history = db_get_history(user_name, type_=model_type, key=key, limit=50)

        table = QTableWidget()
        table.setColumnCount(6)
        table.setHorizontalHeaderLabels(["时间", "类型", "旧值", "新值", "来源", "详情"])
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
            table.setItem(row, 1, QTableWidgetItem(self._TYPE_LABEL.get(ct, ct)))
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
    initial_value: float = 0,
    sync_checkbox: bool = False,
    sync_default: bool = True,
) -> tuple[float | int, str, bool, bool]:
    """数值输入 + 来源下拉（可输入新来源）的通用对话框

    sync_checkbox: 是否展示「同步变更依赖方」复选框
    sync_default:  复选框的默认勾选状态

    Returns: (value, source, sync_checked, ok)
        sync_checked 仅在 sync_checkbox=True 时有意义，否则始终为 True。
    """
    dialog = QDialog(parent)
    dialog.setWindowTitle(title)
    dialog.setMinimumWidth(320)
    layout = QFormLayout(dialog)

    hint_label = QLabel(hint)
    layout.addRow(hint_label)

    spin: QSpinBox | QDoubleSpinBox
    if is_float:
        ds = QDoubleSpinBox()
        ds.setRange(min_val, 999999)
        ds.setDecimals(4)
        ds.setValue(initial_value)
        spin = ds
    else:
        si = QSpinBox()
        si.setRange(min_val, 999999)
        si.setValue(int(initial_value))
        spin = si
    layout.addRow(prompt, spin)

    combo = QComboBox()
    combo.setEditable(True)
    combo.addItems(sources)
    combo.setPlaceholderText("选择或输入新来源")
    layout.addRow("来源:", combo)

    sync_check: QCheckBox | None = None
    if sync_checkbox:
        sync_check = QCheckBox("同步变更依赖方")
        sync_check.setChecked(sync_default)
        sync_check.setToolTip(
            "勾选：按 action 语义处理，触发配额→资源的同步（如配置了 sync_to）。\n"
            "取消：按纯覆写语义处理，仅写本 key，不触发任何同步。"
        )
        layout.addRow(sync_check)

    buttons = QDialogButtonBox(
        QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
    )
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    layout.addRow(buttons)

    if dialog.exec():
        sync_checked = sync_check.isChecked() if sync_check is not None else True
        return spin.value(), combo.currentText().strip(), sync_checked, True
    return 0, "", sync_default, False
