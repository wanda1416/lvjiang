"""编辑工作流文件开头的完整 ``#%`` 声明。"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

import yaml
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ...core.config.resolver import load_available_envs
from ...i18n import tr
from ...workflows.metadata import CAPABILITIES, parse_metadata
from ..button_styles import (
    apply_button_style,
    apply_compact_button_style,
    fit_button_width,
)


def read_front_matter(text: str) -> dict[str, Any]:
    """读取原始映射，不能使用会过滤未知键的运行时解析结果。"""
    lines = []
    for line in text.splitlines():
        if not line.lstrip().startswith("#%"):
            break
        lines.append(line.lstrip()[2:].removeprefix(" "))
    if not lines:
        return {}
    data = yaml.safe_load("\n".join(lines))
    if not isinstance(data, dict):
        raise ValueError(tr("元数据根节点必须是键值映射"))
    return data


def replace_front_matter(text: str, metadata: dict[str, Any]) -> str:
    """只替换文件开头连续的 ``#%`` 块，正文逐字保留。"""
    lines = text.splitlines(keepends=True)
    index = 0
    while index < len(lines) and lines[index].lstrip().startswith("#%"):
        index += 1
    body = "".join(lines[index:])
    dumped = yaml.safe_dump(
        metadata, allow_unicode=True, sort_keys=False, default_flow_style=False,
    ).rstrip("\n")
    header = "\n".join(f"#% {line}" for line in dumped.splitlines())
    if body and not body.startswith("\n"):
        header += "\n"
    return header + "\n" + body


class ExpandableText(QWidget):
    """一行编辑，长内容可在原位置展开；收起不会丢掉换行。"""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._has_multiline = False
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        self.line = QLineEdit()
        self.toggle = QPushButton(tr("展开"))
        apply_compact_button_style(self.toggle, variant="neutral")
        fit_button_width(self.toggle)
        self.multi = QPlainTextEdit()
        self.multi.setMinimumHeight(88)
        row.addWidget(self.line, 1)
        row.addWidget(self.multi, 1)
        row.addWidget(self.toggle, 0, Qt.AlignmentFlag.AlignTop)
        self.multi.hide()
        self.toggle.clicked.connect(self._toggle)

    def _toggle(self) -> None:
        expanded = self.multi.isHidden()
        if expanded:
            if not self._has_multiline:
                self.multi.setPlainText(self.line.text())
        else:
            value = self.multi.toPlainText()
            self._has_multiline = "\n" in value
            self.line.setText(value.split("\n", 1)[0] + ("…" if "\n" in value else ""))
            self.line.setReadOnly("\n" in value)
        self.multi.setVisible(expanded)
        self.line.setVisible(not expanded)
        self.toggle.setText(tr("收起") if expanded else tr("展开"))

    def setText(self, value: str) -> None:  # noqa: N802 — 与 Qt 编辑控件一致
        self._has_multiline = "\n" in value
        self.multi.setPlainText(value)
        self.line.setText(value.split("\n", 1)[0] + ("…" if "\n" in value else ""))
        self.line.setReadOnly("\n" in value)

    def text(self) -> str:
        return (self.multi.toPlainText() if not self.multi.isHidden() or self._has_multiline
                else self.line.text())

    def setEnabled(self, enabled: bool) -> None:  # noqa: N802 — Qt override
        super().setEnabled(enabled)
        self.line.setReadOnly(not enabled or "\n" in self.multi.toPlainText())


class ParameterEditor(QWidget):
    """参数定义的列表和逐项表单。内部保留原始映射以免丢弃缺省语义。"""

    TYPES = ("select", "number", "bool", "checkgroup", "text")

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._items: list[dict[str, Any]] = []
        self._loading = False
        self._selected_index = -1
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        selector = QWidget()
        bar = QHBoxLayout(selector)
        bar.setContentsMargins(0, 0, 0, 0)
        self.list = QListWidget()
        self.list.setMinimumHeight(110)
        bar.addWidget(self.list, 1)
        buttons = QVBoxLayout()
        list_buttons = []
        for caption, handler in ((tr("新增"), self._add), (tr("删除"), self._remove),
                                 (tr("上移"), lambda: self._move(-1)),
                                 (tr("下移"), lambda: self._move(1))):
            button = QPushButton(caption)
            apply_compact_button_style(
                button, variant=("action" if caption == tr("新增") else
                                 "danger" if caption == tr("删除") else "neutral"))
            button.clicked.connect(handler)
            buttons.addWidget(button)
            list_buttons.append(button)
        fit_button_width(*list_buttons)
        buttons.addStretch()
        bar.addLayout(buttons)
        selector_form = QFormLayout()
        selector_form.setContentsMargins(0, 0, 0, 0)
        selector_form.setLabelAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        selector_form.addRow(tr("参数定义"), selector)
        layout.addLayout(selector_form)

        self.details = QWidget()
        form = QFormLayout(self.details)
        form.setContentsMargins(0, 0, 0, 0)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self._form = form
        self.name = QLineEdit()
        self.label = ExpandableText()
        self.kind = QComboBox()
        for kind in self.TYPES:
            self.kind.addItem(kind, kind)
        self.has_default = QCheckBox(tr("声明默认值"))
        self.default_text = ExpandableText()
        self.default_choice = QComboBox()
        self.default_bool = QCheckBox(tr("默认勾选"))
        self.minimum = QLineEdit()
        self.maximum = QLineEdit()
        self.placeholder = ExpandableText()
        self.multiline = QCheckBox(tr("运行时使用多行输入"))
        self.envs = QListWidget()
        self.envs.setMaximumHeight(90)
        for key, display in load_available_envs():
            self._add_choice(self.envs, key, display)
        self.require_list = QCheckBox(tr("多个条件（每行一个，全部满足）"))
        self.require = ExpandableText()
        self.options = QTableWidget(0, 3)
        self.options.setHorizontalHeaderLabels([tr("值"), tr("显示名"), tr("默认状态")])
        option_actions = QHBoxLayout()
        option_buttons = []
        for caption, handler in ((tr("新增选项"), self._add_option),
                                 (tr("删除选项"), self._remove_option),
                                 (tr("上移选项"), lambda: self._move_option(-1)),
                                 (tr("下移选项"), lambda: self._move_option(1))):
            button = QPushButton(caption)
            apply_compact_button_style(
                button, variant=("action" if caption == tr("新增选项") else
                                 "danger" if caption == tr("删除选项") else "neutral"))
            button.clicked.connect(handler)
            option_actions.addWidget(button)
            option_buttons.append(button)
        fit_button_width(*option_buttons)
        option_actions.addStretch()
        self.option_buttons = QWidget()
        self.option_buttons.setLayout(option_actions)
        form.addRow(tr("参数名"), self.name)
        form.addRow(tr("标签"), self.label)
        form.addRow(tr("类型"), self.kind)
        form.addRow(tr("显示环境"), self.envs)
        form.addRow(tr("显示条件"), self.require_list)
        form.addRow("", self.require)
        form.addRow("", self.has_default)
        form.addRow(tr("默认值"), self.default_text)
        form.addRow(tr("默认选项"), self.default_choice)
        form.addRow("", self.default_bool)
        form.addRow(tr("最小值"), self.minimum)
        form.addRow(tr("最大值"), self.maximum)
        form.addRow(tr("占位提示"), self.placeholder)
        form.addRow("", self.multiline)
        form.addRow(tr("选项"), self.options)
        form.addRow("", self.option_buttons)
        layout.addWidget(self.details)
        self.list.currentRowChanged.connect(self._select)
        self.kind.currentIndexChanged.connect(self._on_kind_changed)
        self.has_default.toggled.connect(self._show_kind)
        self.options.itemChanged.connect(self._refresh_default_choices)
        self._show_kind()

    @staticmethod
    def _add_choice(widget: QListWidget, key: str, label: str) -> None:
        from PyQt6.QtCore import Qt
        from PyQt6.QtWidgets import QListWidgetItem

        item = QListWidgetItem(label)
        item.setData(Qt.ItemDataRole.UserRole, key)
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        item.setCheckState(Qt.CheckState.Unchecked)
        widget.addItem(item)

    def _checked(self, widget: QListWidget) -> list[str]:
        from PyQt6.QtCore import Qt

        selected = []
        for index in range(widget.count()):
            item = widget.item(index)
            if item is not None and item.checkState() == Qt.CheckState.Checked:
                selected.append(str(item.data(Qt.ItemDataRole.UserRole)))
        return selected

    def _set_checked(self, widget: QListWidget, values: list[str]) -> None:
        from PyQt6.QtCore import Qt

        for value in values:
            if all((item := widget.item(i)) is None
                   or item.data(Qt.ItemDataRole.UserRole) != value
                   for i in range(widget.count())):
                self._add_choice(widget, value, f"{value}（未定义）")
        for i in range(widget.count()):
            item = widget.item(i)
            if item is None:
                continue
            item.setCheckState(Qt.CheckState.Checked if
                               item.data(Qt.ItemDataRole.UserRole) in values else
                               Qt.CheckState.Unchecked)

    def load(self, items: list[dict[str, Any]]) -> None:
        self._loading = True
        self._items = deepcopy(items)
        self._selected_index = -1
        while self.kind.count() > len(self.TYPES):
            self.kind.removeItem(self.kind.count() - 1)
        self.list.clear()
        for item in self._items:
            self.list.addItem(self._summary(item))
        self._loading = False
        self.list.setCurrentRow(0 if self._items else -1)
        self.details.setVisible(bool(self._items))

    @staticmethod
    def _summary(item: dict) -> str:
        return f"{item.get('name', '')}  ·  {item.get('label') or item.get('name', '')}  [{item.get('type', 'select')}]"

    def _select(self, index: int) -> None:
        if self._loading:
            return
        if self._selected_index >= 0:
            self._save_selected()
        self._loading = True
        self._selected_index = index
        self.details.setVisible(index >= 0)
        if index >= 0:
            item = self._items[index]
            self.name.setText(str(item.get("name", "")))
            self.label.setText(str(item.get("label", "")))
            kind = item.get("type", "select")
            if self.kind.findData(kind) < 0:
                self.kind.addItem(tr("不支持：{kind}（请选择有效类型）").format(kind=kind), kind)
            self.kind.setCurrentIndex(self.kind.findData(kind))
            self._set_checked(self.envs, list(item.get("env") or []))
            requirement = item.get("require", "")
            self.require_list.setChecked(isinstance(requirement, list))
            self.require.setText("\n".join(requirement) if isinstance(requirement, list)
                                 else str(requirement))
            self.has_default.setChecked("default" in item)
            self.has_default.setEnabled(kind in self.TYPES)
            self.default_text.setText(str(item.get("default", "")) if
                                      item.get("type") != "bool" else "")
            default_bool = item.get("default", False)
            self.default_bool.setChecked(
                default_bool is True or str(default_bool).lower() in {"true", "1"})
            self.minimum.setText(str(item.get("min", "")))
            self.maximum.setText(str(item.get("max", "")))
            self.placeholder.setText(str(item.get("placeholder", "")))
            self.multiline.setChecked(bool(item.get("multiline", False)))
            self.options.setRowCount(0)
            for option in item.get("options", []):
                value = option if isinstance(option, str) else option["value"]
                label = value if isinstance(option, str) else option.get("label", value)
                self._add_option_row(value, label,
                                     item.get("default", {}).get(value) if
                                     isinstance(item.get("default"), dict) else None)
            self._loading = False
            self._refresh_default_choices()
            self._loading = True
            self.default_choice.setCurrentIndex(
                self.default_choice.findData(item.get("default")))
            self._show_kind()
        self._loading = False

    def _show_kind(self) -> None:
        kind = self.kind.currentData()
        self.has_default.setEnabled(kind in self.TYPES)
        is_choice = kind in {"select", "checkgroup"}
        for widget, visible in (
            (self.options, is_choice), (self.option_buttons, is_choice),
            (self.minimum, kind == "number"), (self.maximum, kind == "number"),
            (self.placeholder, kind == "text"), (self.multiline, kind == "text"),
            (self.default_bool, kind == "bool" and self.has_default.isChecked()),
            (self.default_choice, kind == "select" and self.has_default.isChecked()),
            (self.default_text, kind in {"number", "text"}
             and self.has_default.isChecked()),
        ):
            widget.setVisible(visible)
            label = self._form.labelForField(widget)
            if label is not None:
                label.setVisible(visible)

    def _on_kind_changed(self) -> None:
        index = self._selected_index
        if not self._loading and 0 <= index < len(self._items):
            original = self._items[index]
            old_kind = original.get("type", "select")
            new_kind = self.kind.currentData()
            allowed = {
                "select": {"options"}, "checkgroup": {"options"},
                "number": {"min", "max"}, "bool": set(),
                "text": {"placeholder", "multiline"},
            }
            dropped = set(original) & (allowed.get(old_kind, set())
                                       - allowed.get(new_kind, set()))
            if new_kind != old_kind and dropped:
                answer = QMessageBox.question(
                    self, tr("更改参数类型"),
                    tr("切换类型会移除旧类型专属的设置。继续？"),
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if answer != QMessageBox.StandardButton.Yes:
                    self.kind.blockSignals(True)
                    self.kind.setCurrentIndex(self.kind.findData(old_kind))
                    self.kind.blockSignals(False)
        self._show_kind()

    def _add_option_row(self, value: str = "", label: str = "", state: bool | None = None) -> None:
        row = self.options.rowCount()
        self.options.insertRow(row)
        self.options.setItem(row, 0, QTableWidgetItem(value))
        self.options.setItem(row, 1, QTableWidgetItem(label))
        combo = QComboBox()
        combo.addItem(tr("未声明（勾选）"), None)
        combo.addItem(tr("勾选"), True)
        combo.addItem(tr("不勾选"), False)
        combo.setCurrentIndex(0 if state is None else (1 if state else 2))
        self.options.setCellWidget(row, 2, combo)
        self._refresh_default_choices()

    def _refresh_default_choices(self, *_args: object) -> None:
        if self._loading:
            return
        current = self.default_choice.currentData()
        self.default_choice.blockSignals(True)
        self.default_choice.clear()
        for row in range(self.options.rowCount()):
            value_item = self.options.item(row, 0)
            label_item = self.options.item(row, 1)
            if value_item is not None:
                value = value_item.text()
                self.default_choice.addItem(
                    label_item.text() if label_item is not None else value, value)
        self.default_choice.setCurrentIndex(self.default_choice.findData(current))
        self.default_choice.blockSignals(False)

    def _add_option(self) -> None:
        self._add_option_row()

    def _remove_option(self) -> None:
        if self.options.currentRow() >= 0:
            self.options.removeRow(self.options.currentRow())
            self._refresh_default_choices()

    def _move_option(self, step: int) -> None:
        index = self.options.currentRow()
        other = index + step
        if not (0 <= index < self.options.rowCount() and
                0 <= other < self.options.rowCount()):
            return
        rows = []
        for row in (index, other):
            value = self.options.item(row, 0)
            label = self.options.item(row, 1)
            combo = self.options.cellWidget(row, 2)
            if value is None or label is None or not isinstance(combo, QComboBox):
                return
            rows.append((value.text(), label.text(), combo.currentIndex()))
        self._loading = True
        for row, values in zip((other, index), rows, strict=True):
            value_item = self.options.item(row, 0)
            label_item = self.options.item(row, 1)
            if value_item is not None and label_item is not None:
                value_item.setText(values[0])
                label_item.setText(values[1])
            combo = self.options.cellWidget(row, 2)
            if isinstance(combo, QComboBox):
                combo.setCurrentIndex(values[2])
        self._loading = False
        self.options.selectRow(other)
        self._refresh_default_choices()

    def _save_selected(self) -> None:
        index = self._selected_index
        if index < 0 or self._loading:
            return
        original = self._items[index]
        item = deepcopy(original)
        item["name"] = self.name.text().strip()
        if self.label.text() or "label" in item:
            item["label"] = self.label.text()
        kind = self.kind.currentData()
        if kind != original.get("type", "select"):
            # 类型改变后，旧类型的字段不得留在新定义中。
            for key in ("options", "min", "max", "placeholder", "multiline", "default"):
                item.pop(key, None)
            item["type"] = kind
        env = self._checked(self.envs)
        previous_env = item.get("env", [])
        if len(env) == len(previous_env) and set(env) == set(previous_env):
            env = previous_env
        if env or "env" in item:
            item["env"] = env
        requirement = self.require.text()
        if requirement or "require" in item:
            item["require"] = ([line for line in requirement.splitlines() if line.strip()]
                               if self.require_list.isChecked() else requirement)
            if not item["require"]:
                item.pop("require")
        if kind not in self.TYPES:
            pass  # 原样保留未知类型，改选有效类型后再编辑其专属字段。
        elif self.has_default.isChecked():
            if kind == "bool":
                value = self.default_bool.isChecked()
                old = original.get("default")
                item["default"] = old if isinstance(old, str) and (
                    old.lower() in {"true", "1"}) == value else value
            elif kind == "number":
                item["default"] = self._integer(self.default_text.text(), "default")
            elif kind == "select":
                item["default"] = self.default_choice.currentData()
            elif kind == "text":
                item["default"] = self.default_text.text()
            else:
                defaults: dict[str, bool] = {}
                for row in range(self.options.rowCount()):
                    value_item = self.options.item(row, 0)
                    combo = self.options.cellWidget(row, 2)
                    if (value_item is not None and isinstance(combo, QComboBox)
                            and combo.currentIndex() > 0):
                        defaults[value_item.text()] = bool(combo.currentData())
                item["default"] = (None if original.get("default") is None
                                   and "default" in original and not defaults else defaults)
        else:
            item.pop("default", None)
        if kind == "number":
            for key, edit in (("min", self.minimum), ("max", self.maximum)):
                if edit.text().strip():
                    item[key] = self._integer(edit.text(), key)
                else:
                    item.pop(key, None)
        if kind == "text":
            if self.placeholder.text() or "placeholder" in item:
                item["placeholder"] = self.placeholder.text()
            if self.multiline.isChecked() or "multiline" in item:
                item["multiline"] = self.multiline.isChecked()
        if kind in {"select", "checkgroup"}:
            old_options = original.get("options", []) if kind == original.get("type", "select") else []
            options: list[Any] = []
            for row in range(self.options.rowCount()):
                value_item = self.options.item(row, 0)
                label_item = self.options.item(row, 1)
                if value_item is None or label_item is None:
                    continue
                option_value = value_item.text()
                label = label_item.text()
                old = old_options[row] if row < len(old_options) else None
                if isinstance(old, str) and old == option_value == label:
                    options.append(old)
                elif (isinstance(old, dict) and old.get("value") == option_value
                      and old.get("label", option_value) == label):
                    options.append(old)
                elif label == option_value:
                    options.append(option_value)
                else:
                    options.append({"value": option_value, "label": label})
            item["options"] = options
        self._items[index] = item
        list_item = self.list.item(index)
        if list_item is not None:
            list_item.setText(self._summary(item))

    @staticmethod
    def _integer(value: str, field: str) -> int | str:
        try:
            return int(value)
        except ValueError:
            # 编辑中允许暂存不完整输入，应用时由运行时校验器给出字段错误。
            return value

    def _add(self) -> None:
        self._save_selected()
        item = {"name": "new_parameter", "type": "bool", "default": False}
        self._items.append(item)
        self.list.addItem(self._summary(item))
        self.list.setCurrentRow(len(self._items) - 1)

    def _remove(self) -> None:
        index = self.list.currentRow()
        if index >= 0:
            self._loading = True
            self._items.pop(index)
            self.list.takeItem(index)
            self._selected_index = -1
            self._loading = False
            self.list.setCurrentRow(min(index, len(self._items) - 1))
            self.details.setVisible(bool(self._items))

    def _move(self, step: int) -> None:
        self._save_selected()
        index = self.list.currentRow()
        other = index + step
        if 0 <= index < len(self._items) and 0 <= other < len(self._items):
            self._items[index], self._items[other] = self._items[other], self._items[index]
            self._loading = True
            first = self.list.item(index)
            second = self.list.item(other)
            if first is not None and second is not None:
                first.setText(self._summary(self._items[index]))
                second.setText(self._summary(self._items[other]))
            self._selected_index = -1
            self._loading = False
            self.list.setCurrentRow(other)

    def values(self) -> list[dict[str, Any]]:
        self._save_selected()
        return deepcopy(self._items)


class MetadataPanel(QWidget):
    """完整 front-matter 表单；应用只更新代码缓冲区，保存由宿主负责。"""

    text_applied = pyqtSignal(str)
    location_changed = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._text = ""
        self._raw: dict[str, Any] = {}
        self._editable = False
        self._loaded_location = "local"
        self._fallback_id = ""
        self._form_valid = False
        self._setup_ui()

    def _setup_ui(self) -> None:
        root = QVBoxLayout(self)
        hint = QLabel(tr("修改会写回代码缓冲区，仍需点击“保存”才会落盘。"))
        hint.setWordWrap(True)
        root.addWidget(hint)
        self.error = QLabel("")
        self.error.setWordWrap(True)
        self.error.setStyleSheet("color: #b71c1c;")
        self.error.hide()
        root.addWidget(self.error)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        content = QWidget()
        form = QFormLayout(content)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.location_row = QWidget()
        location_layout = QHBoxLayout(self.location_row)
        location_layout.setContentsMargins(0, 0, 0, 0)
        self.location_group = QButtonGroup(self.location_row)
        self.location_radios = {
            "local": QRadioButton(tr("本地 (local)")),
            "system": QRadioButton(tr("系统 (system)")),
        }
        for button in self.location_radios.values():
            self.location_group.addButton(button)
            location_layout.addWidget(button)
        location_layout.addStretch()
        self.location_radios["local"].setChecked(True)
        self.location_group.buttonToggled.connect(
            lambda _button, checked: self.location_changed.emit(self.location())
            if checked else None)
        self.edit_id = QLineEdit()
        self.edit_id.setToolTip(tr("稳定脚本标识；修改后已有引用不会自动更新"))
        self.edit_name = QLineEdit()
        self.edit_note = ExpandableText()
        self.scope_row = QWidget()
        scope_layout = QHBoxLayout(self.scope_row)
        scope_layout.setContentsMargins(0, 0, 0, 0)
        self.scope_group = QButtonGroup(self.scope_row)
        self.scope_radios = {
            "daily": QRadioButton(tr("日常")),
            "dedicated": QRadioButton(tr("专用")),
        }
        for button in self.scope_radios.values():
            self.scope_group.addButton(button)
            scope_layout.addWidget(button)
        scope_layout.addStretch()
        self.scope_radios["daily"].setChecked(True)
        form.addRow(tr("保存位置"), self.location_row)
        form.addRow("id", self.edit_id)
        form.addRow(tr("名称"), self.edit_name)
        form.addRow(tr("说明"), self.edit_note)
        form.addRow(tr("脚本性质"), self.scope_row)
        self._env_row = QHBoxLayout()
        self._env_checks: dict[str, QCheckBox] = {}
        self._env_row.addStretch()
        for key, display in load_available_envs():
            self._add_env_check(key, display)
        form.addRow(tr("运行环境"), self._env_row)
        self.capabilities = QListWidget()
        self.capabilities.setMaximumHeight(70)
        for key, display in CAPABILITIES.items():
            ParameterEditor._add_choice(self.capabilities, key, display)
        form.addRow(tr("所需能力"), self.capabilities)
        traits = QHBoxLayout()
        self.check_runnable = QCheckBox(tr("可独立运行"))
        self.check_batchable = QCheckBox(tr("可批量运行"))
        self.check_hidden = QCheckBox(tr("默认隐藏"))
        self.check_batch_unit_prepare = QCheckBox(tr("属性单元准备工作流"))
        for check in (self.check_runnable, self.check_batchable,
                      self.check_hidden, self.check_batch_unit_prepare):
            traits.addWidget(check)
        traits.addStretch()
        form.addRow(tr("脚本声明"), traits)
        self.edit_batch_check = QLineEdit()
        form.addRow(tr("批量检查"), self.edit_batch_check)
        self.parameters = ParameterEditor()
        form.addRow(self.parameters)
        scroll.setWidget(content)
        root.addWidget(scroll, 1)
        actions = QHBoxLayout()
        actions.addStretch()
        self.btn_reload = QPushButton(tr("从代码重新加载"))
        self.btn_apply = QPushButton(tr("应用到代码"))
        apply_button_style(self.btn_reload, variant="neutral")
        apply_button_style(self.btn_apply)
        self.btn_reload.clicked.connect(self._reload)
        self.btn_apply.clicked.connect(self._apply)
        self.check_batchable.toggled.connect(
            lambda checked: self.check_runnable.setChecked(True) if checked else None)
        self.check_runnable.toggled.connect(
            lambda checked: self.check_batchable.setChecked(False) if not checked else None)
        actions.addWidget(self.btn_reload)
        actions.addWidget(self.btn_apply)
        root.addLayout(actions)

    def _add_env_check(self, key: str, display: str) -> None:
        check = QCheckBox(display)
        check.setToolTip(key)
        self._env_checks[key] = check
        self._env_row.insertWidget(max(self._env_row.count() - 1, 0), check)

    def _set_env(self, values: list[str]) -> None:
        for key in values:
            if key not in self._env_checks:
                self._add_env_check(key, f"{key}（系统参数中未定义）")
        for key, check in self._env_checks.items():
            check.setChecked(key in values)

    def _selected_env(self) -> list[str]:
        return [key for key, check in self._env_checks.items() if check.isChecked()]

    def set_location(self, layer: str, *, developer: bool, editable: bool) -> None:
        self.location_group.blockSignals(True)
        self.location_radios.get(layer, self.location_radios["local"]).setChecked(True)
        self.location_group.blockSignals(False)
        self._loaded_location = layer
        self.location_row.setEnabled(developer and editable and self._form_valid)

    def location(self) -> str:
        return "system" if self.location_radios["system"].isChecked() else "local"

    def load_text(self, text: str, *, editable: bool, fallback_id: str = "") -> None:
        self._text = text
        self._editable = editable
        self._fallback_id = fallback_id
        self._form_valid = False
        try:
            meta = parse_metadata(text)
            raw = read_front_matter(text)
            known = {"id", "name", "note", "env", "requires", "parameters", "scope",
                     "hidden", "runnable", "batchable", "batch_check", "batch_unit_prepare"}
            unknown = set(raw) - known
            if unknown:
                raise ValueError(tr("存在尚未支持的声明：{keys}；请先在代码页处理")
                                 .format(keys=", ".join(sorted(map(str, unknown)))))
            for index, item in enumerate(raw.get("parameters", [])):
                if not isinstance(item, dict):
                    raise ValueError(tr("第 {index} 个参数不是字段映射")
                                     .format(index=index + 1))
                if item.get("type", "select") not in ParameterEditor.TYPES:
                    continue  # 保留原始定义，并在类型下拉框中提示用户修正。
                common = {"name", "label", "type", "default", "env", "require"}
                specific = {"select": {"options"}, "checkgroup": {"options"},
                            "number": {"min", "max"}, "bool": set(),
                            "text": {"placeholder", "multiline"}}
                extra = set(item) - common - specific[item.get("type", "select")]
                if extra:
                    raise ValueError(tr("第 {index} 个参数有未支持的字段：{keys}")
                                     .format(index=index + 1, keys=", ".join(sorted(extra))))
                for option in item.get("options", []):
                    if isinstance(option, dict) and set(option) - {"value", "label"}:
                        raise ValueError(tr("第 {index} 个参数的选项包含未支持的字段")
                                         .format(index=index + 1))
        except Exception as exc:  # 原始声明不可解释时禁止表单覆盖
            self._set_enabled(False)
            self.error.setText(tr("元数据表单不可编辑：{error}").format(error=exc))
            self.error.show()
            return
        self._raw = raw
        self._form_valid = True
        self.error.hide()
        self._set_enabled(editable)
        self.edit_id.setText(str(meta.get("id") or fallback_id))
        self.edit_name.setText(str(meta.get("name") or ""))
        self.edit_note.setText(str(meta.get("note") or ""))
        self.scope_radios.get(meta.get("scope") or "daily", self.scope_radios["daily"]).setChecked(True)
        self._set_env(list(meta.get("env") or []))
        self.parameters._set_checked(self.capabilities, list(meta.get("requires") or []))
        self.check_runnable.blockSignals(True)
        self.check_batchable.blockSignals(True)
        self.check_runnable.setChecked(bool(meta.get("runnable", False)))
        self.check_batchable.setChecked(bool(meta.get("batchable", False)))
        self.check_runnable.blockSignals(False)
        self.check_batchable.blockSignals(False)
        self.check_hidden.setChecked(bool(meta.get("hidden", False)))
        self.check_batch_unit_prepare.setChecked(bool(meta.get("batch_unit_prepare", False)))
        self.edit_batch_check.setText(str(meta.get("batch_check") or ""))
        self.parameters.load(raw.get("parameters", []))
        self.btn_reload.setToolTip("")

    def _set_enabled(self, enabled: bool) -> None:
        for widget in (self.location_row, self.edit_id, self.edit_name, self.edit_note,
                       self.scope_row,
                       *self._env_checks.values(), self.capabilities, self.check_runnable,
                       self.check_batchable, self.check_hidden, self.check_batch_unit_prepare,
                       self.edit_batch_check, self.parameters, self.btn_apply):
            widget.setEnabled(enabled)
        self.btn_reload.setEnabled(bool(self._text))

    def _reload(self) -> None:
        self.load_text(self._text, editable=self._editable,
                       fallback_id=self.edit_id.text())

    @staticmethod
    def _put(meta: dict, key: str, value: Any, default: Any) -> None:
        if value != default or key in meta:
            meta[key] = value

    def _collect(self) -> dict[str, Any]:
        meta = deepcopy(self._raw)
        self._put(meta, "id", self.edit_id.text().strip(), self._fallback_id)
        self._put(meta, "name", self.edit_name.text(), "")
        self._put(meta, "note", self.edit_note.text(), "")
        self._put(meta, "scope", "dedicated" if self.scope_radios["dedicated"].isChecked()
                  else "daily", "daily")
        env = self._selected_env()
        previous_env = meta.get("env", [])
        if len(env) == len(previous_env) and set(env) == set(previous_env):
            env = previous_env
        self._put(meta, "env", env, [])
        capabilities = self.parameters._checked(self.capabilities)
        if capabilities:
            meta["requires"] = capabilities
        else:
            meta.pop("requires", None)
        for key, check in (("runnable", self.check_runnable),
                           ("batchable", self.check_batchable),
                           ("hidden", self.check_hidden),
                           ("batch_unit_prepare", self.check_batch_unit_prepare)):
            self._put(meta, key, check.isChecked(), False)
        value = self.edit_batch_check.text().strip()
        if value:
            meta["batch_check"] = value
        else:
            meta.pop("batch_check", None)
        params = self.parameters.values()
        if params or "parameters" in meta:
            meta["parameters"] = params
        return meta

    def has_draft(self) -> bool:
        return self.has_content_draft() or self.location() != self._loaded_location

    def has_content_draft(self) -> bool:
        try:
            return self._collect() != self._raw
        except ValueError:
            return True

    def _apply(self) -> None:
        try:
            metadata = self._collect()
            candidate = (self._text if metadata == self._raw else
                         replace_front_matter(self._text, metadata))
            parse_metadata(candidate)
        except Exception as exc:
            QMessageBox.warning(self, tr("元数据无效"), str(exc))
            return
        self._text = candidate
        self._raw = metadata
        self.text_applied.emit(candidate)
