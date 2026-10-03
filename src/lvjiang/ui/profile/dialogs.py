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
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from lvjiang.ui.combo_box import AutoWidthComboBox

from ...core.profile.repository import db_count_history, db_get_history
from ...i18n import tr
from ..button_styles import (
    apply_button_style,
    apply_dialog_button_box_style,
    fit_button_width,
)

# ProfileDefinitionDialog 位于 settings_dialog.py，此处 re-export 便于统一导入。
from .settings_dialog import ProfileDefinitionDialog  # noqa: F401

__all__ = ["HistoryDialog", "ask_value_dialog", "ProfileDefinitionDialog"]

# ─── 历史记录对话框 ────────────────────────────────────────────

#: 来源列至少要完整显示 9 个全角字，覆盖「限时活动：XXXX」这类取值。
_SOURCE_CHARACTER_CAPACITY = 9
#: 单元左右内边距与边框；不量进来的话文字会贴边或被省略号吃掉。
_COLUMN_CHROME_WIDTH = 24


def _cjk_column_width(widget: QWidget, characters: int) -> int:
    """量出 N 个全角汉字所需的列宽（含单元留白）。

    不写死像素宽：来源允许用户输入任意文本，同一个 9 字来源在 Windows 的 UI
    字体下比别处宽。列宽不够时表格不会报错，只把「限时活动：XXXX」截成前半截，
    让人以为来源本来就短。
    """
    metrics = widget.fontMetrics()
    return metrics.horizontalAdvance("汉" * characters) + _COLUMN_CHROME_WIDTH


class HistoryDialog(QDialog):
    """按 key 查看变更记录；可限定单个用户，也可跨用户分页。"""

    _TYPE_LABEL = {"tick": tr("定时"), "action": tr("操作"), "override": tr("覆写")}  # runtime tr()

    def __init__(
        self, user_name: str | None, model_type: str, key: str,
        key_label: str, parent=None,
    ):
        super().__init__(parent)
        self._user_name = user_name
        self._model_type = model_type
        self._key = key
        self._page = 1
        self._page_size = 100
        title = f"{key_label} — {user_name}" if user_name is not None else key_label
        self.setWindowTitle(f"{title} " + tr("变更记录"))
        self.resize(820 if user_name is not None else 960, 480)
        self._setup_ui()
        self._load_page()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        table = QTableWidget(self)
        table.setColumnCount(7)
        table.setHorizontalHeaderLabels([
            tr("时间"), tr("用户名"), tr("类型"), tr("旧值"),
            tr("新值"), tr("来源"), tr("详情"),
        ])
        table.setColumnHidden(1, self._user_name is not None)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        table.setAlternatingRowColors(True)
        vh = table.verticalHeader()
        if vh is not None:
            vh.setVisible(False)

        header = table.horizontalHeader()
        if header is not None:
            header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
            table.setColumnWidth(0, 140)
            header.setSectionResizeMode(
                1, QHeaderView.ResizeMode.ResizeToContents)
            for col, w in (
                (2, 60), (3, 70), (4, 70),
                (5, _cjk_column_width(table, _SOURCE_CHARACTER_CAPACITY)),
            ):
                header.setSectionResizeMode(col, QHeaderView.ResizeMode.Fixed)
                table.setColumnWidth(col, w)
            header.setSectionResizeMode(6, QHeaderView.ResizeMode.Stretch)

        self._table = table
        layout.addWidget(table)

        pager = QHBoxLayout()
        pager.addWidget(QLabel(tr("每页")))
        self._page_size_combo = AutoWidthComboBox(self)
        for size in (50, 100, 200, 500):
            self._page_size_combo.addItem(str(size), size)
        self._page_size_combo.setCurrentIndex(
            self._page_size_combo.findData(self._page_size))
        self._page_size_combo.currentIndexChanged.connect(
            self._on_page_size_changed)
        pager.addWidget(self._page_size_combo)
        pager.addWidget(QLabel(tr("条记录")))
        pager.addStretch()
        self._page_label = QLabel()
        pager.addWidget(self._page_label)
        self._first_button = QPushButton(tr("首页"))
        self._previous_button = QPushButton(tr("上一页"))
        self._next_button = QPushButton(tr("下一页"))
        self._last_button = QPushButton(tr("末页"))
        self._first_button.clicked.connect(lambda: self._go_to_page(1))
        self._previous_button.clicked.connect(
            lambda: self._go_to_page(self._page - 1))
        self._next_button.clicked.connect(lambda: self._go_to_page(self._page + 1))
        self._last_button.clicked.connect(
            lambda: self._go_to_page(self._page_count))
        for button in (
            self._first_button, self._previous_button,
            self._next_button, self._last_button,
        ):
            apply_button_style(button, variant="neutral")
            pager.addWidget(button)
        fit_button_width(
            self._first_button, self._previous_button,
            self._next_button, self._last_button,
        )
        layout.addLayout(pager)

    def _on_page_size_changed(self) -> None:
        self._page_size = int(self._page_size_combo.currentData())
        self._page = 1
        self._load_page()

    def _go_to_page(self, page: int) -> None:
        if 1 <= page <= self._page_count and page != self._page:
            self._page = page
            self._load_page()

    def _load_page(self) -> None:
        total = db_count_history(
            self._user_name, type_=self._model_type, key=self._key)
        self._page_count = max(1, (total + self._page_size - 1) // self._page_size)
        self._page = min(self._page, self._page_count)
        history = db_get_history(
            self._user_name, type_=self._model_type, key=self._key,
            limit=self._page_size, offset=(self._page - 1) * self._page_size,
        )
        table = self._table

        table.setRowCount(len(history))
        for row, rec in enumerate(history):
            # 格式化时间
            raw_ts = rec.get("ts", "")
            try:
                formatted_ts = datetime.fromisoformat(raw_ts).strftime("%m-%d %H:%M:%S")
            except (ValueError, TypeError):
                formatted_ts = raw_ts

            ct = rec.get("change_type", "")
            rec_type = rec.get("type", "")

            # note 模型展示文本值，其他模型展示数值
            if rec_type == "note":
                old_str = rec.get("old_value_text", "") or "—"
                new_str = rec.get("new_value_text", "") or "—"
                if not old_str:
                    old_str = "—"
            else:
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
            table.setItem(row, 1, QTableWidgetItem(rec.get("username", "")))
            table.setItem(row, 2, QTableWidgetItem(tr(self._TYPE_LABEL.get(ct, ct))))
            table.setItem(row, 3, QTableWidgetItem(old_str))
            table.setItem(row, 4, QTableWidgetItem(new_str))
            table.setItem(row, 5, QTableWidgetItem(rec.get("source", "")))
            table.setItem(row, 6, QTableWidgetItem(rec.get("detail", "")))

        start = (self._page - 1) * self._page_size + 1 if total else 0
        end = start + len(history) - 1 if history else 0
        self._page_label.setText(tr(
            "第 {page}/{pages} 页 · 显示 {start}–{end} / 共 {total} 条"
        ).format(
            page=self._page, pages=self._page_count,
            start=start, end=end, total=total,
        ))
        self._first_button.setEnabled(self._page > 1)
        self._previous_button.setEnabled(self._page > 1)
        self._next_button.setEnabled(self._page < self._page_count)
        self._last_button.setEnabled(self._page < self._page_count)


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
