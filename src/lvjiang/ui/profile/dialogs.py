"""Profile 模块独立对话框组件

提供三类对话框：
- HistoryDialog: 查看指定 key 的变更记录
- ask_value_dialog: 通用数值输入 + 来源下拉（可输入新来源）对话框
- ProfileDefinitionDialog: 数据模型定义编辑对话框
"""

from __future__ import annotations

from datetime import datetime

from PyQt6.QtCore import Qt
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

from ...core.profile.models import format_sync_label
from ...core.profile.repository import (
    db_count_history,
    db_get_history,
    db_update_history_source,
)
from ...core.profile.service import (
    ProfileWriteConflict,
    profile_history_undo_unavailable_reason,
    undo_profile_history,
)
from ...i18n import tr
from ..button_styles import (
    apply_button_style,
    apply_dialog_button_box_style,
    fit_button_width,
)

# ProfileDefinitionDialog 位于 settings_dialog.py，此处 re-export 便于统一导入。
from .settings_dialog import ProfileDefinitionDialog  # noqa: F401

__all__ = ["HistoryDialog", "KeyRenameHistoryDialog", "ask_value_dialog", "ProfileDefinitionDialog"]

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


class KeyRenameHistoryDialog(QDialog):
    """独立展示模型定义的重命名审计，不混入数值变更历史。"""

    def __init__(self, model_type: str, key: str, parent=None):
        super().__init__(parent)
        from ...core.profile.repository import get_profile_db
        self.setWindowTitle(tr("key 重命名记录"))
        self.resize(780, 360)
        layout = QVBoxLayout(self)
        records = get_profile_db().get_key_renames(model_type, key)
        table = QTableWidget(len(records), 6, self)
        table.setHorizontalHeaderLabels([
            tr("时间"), tr("原 key"), tr("新 key"),
            tr("当前记录数"), tr("历史记录数"), tr("同步引用数"),
        ])
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        table.setAlternatingRowColors(True)
        header = table.horizontalHeader()
        assert header is not None
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setStretchLastSection(True)
        for row, record in enumerate(records):
            for column, field in enumerate(("ts", "old_key", "new_key", "entries_count", "history_count", "sync_count")):
                table.setItem(row, column, QTableWidgetItem(str(record[field])))
        layout.addWidget(table)
        if not records:
            layout.addWidget(QLabel(tr("尚无 key 重命名记录")))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        apply_dialog_button_box_style(buttons)
        layout.addWidget(buttons)


class HistoryDialog(QDialog):
    """按 key 查看变更记录；可限定单个用户，也可跨用户分页。"""

    _TYPE_LABEL = {
        "tick": tr("恢复"), "reset": tr("重置"), "action": tr("操作"),
        "override": tr("覆写"), "undo": tr("撤销"),
    }  # runtime tr()
    _SOURCE_COLUMN = 5
    _ACTION_COLUMN = 8
    _HISTORY_ID_ROLE = int(Qt.ItemDataRole.UserRole)
    _ORIGINAL_SOURCE_ROLE = _HISTORY_ID_ROLE + 1

    def __init__(
        self, user_name: str | None, model_type: str, key: str,
        key_label: str, parent=None,
    ):
        super().__init__(parent)
        self._user_name = user_name
        self._model_type = model_type
        self._key = key
        self._key_label = key_label
        self._page = 1
        self._page_size = 100
        self._loading_history = False
        self.values_changed = False
        title = f"{key_label} — {user_name}" if user_name is not None else key_label
        self.setWindowTitle(f"{title} " + tr("变更记录"))
        self.resize(900 if user_name is not None else 1040, 480)
        self._setup_ui()
        self._load_page()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        table = QTableWidget(self)
        table.setColumnCount(9)
        table.setHorizontalHeaderLabels([
            tr("时间"), tr("用户名"), tr("类型"), tr("旧值"),
            tr("新值"), tr("来源"), tr("变动量"), tr("同步来源"), "",
        ])
        table.setColumnHidden(1, self._user_name is not None)
        table.setEditTriggers(QTableWidget.EditTrigger.DoubleClicked)
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        table.setAlternatingRowColors(True)
        table.itemChanged.connect(self._on_item_changed)
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
            header.setSectionResizeMode(6, QHeaderView.ResizeMode.ResizeToContents)
            header.setSectionResizeMode(7, QHeaderView.ResizeMode.Stretch)
            header.setSectionResizeMode(8, QHeaderView.ResizeMode.Fixed)
            table.setColumnWidth(8, _cjk_column_width(table, 3))

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

        self._loading_history = True
        table.setRowCount(len(history))
        latest_ids: dict[str, int | None] = {}
        for rec in history:
            username = str(rec.get("username", ""))
            if username not in latest_ids:
                latest = db_get_history(
                    username, self._model_type, self._key, limit=1,
                )
                latest_ids[username] = int(latest[0]["id"]) if latest else None
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

            for column, text in enumerate((
                formatted_ts,
                rec.get("username", ""),
                tr(self._TYPE_LABEL.get(ct, ct)),
                old_str,
                new_str,
            )):
                item = QTableWidgetItem(str(text))
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                table.setItem(row, column, item)

            source = rec.get("source", "") or ""
            source_item = QTableWidgetItem(source)
            source_item.setData(self._HISTORY_ID_ROLE, rec.get("id"))
            source_item.setData(self._ORIGINAL_SOURCE_ROLE, source)
            source_item.setToolTip(tr("双击修改来源"))
            table.setItem(row, self._SOURCE_COLUMN, source_item)
            delta = rec.get("delta_value")
            delta_item = QTableWidgetItem(f"{delta:+g}" if delta is not None else "—")
            delta_item.setFlags(delta_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            table.setItem(row, 6, delta_item)
            sync_from = rec.get("sync_from") or ""
            sync_item = QTableWidgetItem(format_sync_label(sync_from) if sync_from else "")
            sync_item.setFlags(sync_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            table.setItem(row, 7, sync_item)

            username = str(rec.get("username", ""))
            reason = profile_history_undo_unavailable_reason(
                rec, is_latest=latest_ids.get(username) == rec.get("id"),
            )
            undo_button = QPushButton(tr("撤销"), table)
            apply_button_style(undo_button, variant="neutral")
            fit_button_width(undo_button)
            undo_button.setEnabled(reason is None)
            if reason is not None:
                undo_button.setToolTip(reason)
            undo_button.clicked.connect(
                lambda _checked=False, record=rec, old=old_str, new=new_str:
                self._confirm_undo(record, old, new)
            )
            table.setCellWidget(row, self._ACTION_COLUMN, undo_button)
        self._loading_history = False

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

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        if self._loading_history or item.column() != self._SOURCE_COLUMN:
            return
        history_id = item.data(self._HISTORY_ID_ROLE)
        old_source = item.data(self._ORIGINAL_SOURCE_ROLE) or ""
        new_source = item.text().strip()
        if new_source == old_source:
            if item.text() != old_source:
                self._loading_history = True
                item.setText(old_source)
                self._loading_history = False
            return
        if not db_update_history_source(
            int(history_id), expected_source=old_source, new_source=new_source,
        ):
            QMessageBox.warning(
                self, tr("修改失败"), tr("该条历史记录已发生变化，请刷新后重试"),
            )
            self._load_page()
            return
        self._loading_history = True
        item.setText(new_source)
        item.setData(self._ORIGINAL_SOURCE_ROLE, new_source)
        self._loading_history = False

    def _confirm_undo(self, record: dict, old_value: str, new_value: str) -> None:
        message = tr(
            "确定撤销这条变更吗？\n\n"
            "用户：{username}\n"
            "数据项：{label}\n"
            "当前值：{new_value}\n"
            "撤销后：{old_value}\n\n"
            "撤销会新增一条变更记录。"
        ).format(
            username=record.get("username", ""),
            label=self._key_label,
            new_value=new_value,
            old_value=old_value,
        )
        answer = QMessageBox.question(
            self,
            tr("确认撤销"),
            message,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            undo_profile_history(int(record["id"]))
        except (ProfileWriteConflict, ValueError) as exc:
            QMessageBox.warning(self, tr("撤销失败"), str(exc))
            self._load_page()
            return
        self.values_changed = True
        self._load_page()


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
