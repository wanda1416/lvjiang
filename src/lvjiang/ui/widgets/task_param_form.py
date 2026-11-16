"""任务参数表单：按参数定义绘制一组可复用控件。

日常页参数面板、批量「任务」页与「工具 → 任务参数」对话框共用这一份绘制与
取值逻辑。三处此前各写一套（两套可编辑、一套只读打印 JSON），控件类型、
``checkgroup`` 容器、数字参数读成字符串、``require`` 逐行显隐这些细节迟早
会走偏，因此统一到这里。

控件与定义类型的对应关系（与历史行为一致）：

- ``number`` → ``QSpinBox``，取值读成字符串（配置里本来就是字符串）；
- ``bool`` → ``QCheckBox``，定义默认值可能是字符串，按 ``to_bool`` 解释；
- ``text`` → ``QLineEdit``；``multiline`` → ``QPlainTextEdit``；
- ``checkgroup`` → ``FlowContainer`` + 每个候选一个 ``QCheckBox``，值为 dict；
- 其它（含 ``select``）→ ``AutoWidthComboBox``，取值优先 ``currentData``。

只读形态用 ``setEnabled(False)`` 表达（禁用而非隐藏）：控件、值都还在，
取消禁用后立刻可编辑。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from loguru import logger
from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ...core.param_require import RequireError, visible_parameter_names
from ...workflows.builtins._coerce import to_bool
from .combo_box import AutoWidthComboBox
from .misc import FlowContainer, FlowLayout


@dataclass
class _Field:
    """一个参数对应的控件、占用的表单行号和它自己的定义。"""

    definition: dict
    widget: QWidget
    rows: list[int] = field(default_factory=list)


class TaskParamForm(QWidget):
    """按参数定义绘制参数控件，并负责取值与依赖显隐。

    调用方只关心三件事：``set_parameters`` 重建、``collect`` 取值、
    ``values_changed`` 表示"控件变了，请按自己的归属去保存"。
    """

    #: 非文本控件（勾选、下拉、数字）变化后携带完整取值，调用方应立即保存。
    value_changed = pyqtSignal(dict)
    #: 文本输入变化；调用方负责防抖，避免每敲一个字都写盘。
    text_edited = pyqtSignal(dict)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._definitions: list[dict] = []
        self._fields: dict[str, _Field] = {}
        self._editable = True
        # QWidget 只能有一个布局：表单布局先不挂父级，放进外层容器。
        self._layout = QFormLayout()
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setFieldGrowthPolicy(
            QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addLayout(self._layout)
        outer.addStretch(1)

    # ─── 对外接口 ─────────────────────────────────────────

    def set_parameters(
        self,
        definitions: list[dict],
        values: dict[str, Any] | None = None,
        *,
        editable: bool = True,
    ) -> None:
        """按定义重建全部控件；``values`` 为 None 时用定义默认值。"""
        self._clear()
        self._definitions = [
            item for item in definitions
            if isinstance(item, dict) and item.get("name")
        ]
        current = dict(values or {})
        for definition in self._definitions:
            name = str(definition["name"])
            self._fields[name] = self._build_field(definition, current)
        self._editable = bool(editable)
        self.setEnabled(self._editable)
        self._refresh_visibility()

    def collect(self) -> dict[str, Any]:
        """按定义顺序读出当前所有参数的取值。"""
        values: dict[str, Any] = {}
        for name, item in self._fields.items():
            values[name] = self._collect_field(item)
        return values

    def set_values(self, values: dict[str, Any], *, names: list[str] | None = None) -> None:
        """写回控件且不发信号；``names`` 限定只写哪些参数。

        外部改动（例如全局参数被编辑）同步到本表单时用它，避免把同步误当成
        用户编辑再保存一轮。
        """
        targets = self._fields.keys() if names is None else names
        for name in targets:
            item = self._fields.get(name)
            if item is None or name not in values:
                continue
            self._apply_value(item, values[name])
        self._refresh_visibility()

    def set_editable(self, editable: bool) -> None:
        """整体禁用/启用（禁用保留控件与值，取消禁用即可继续编辑）。"""
        self._editable = bool(editable)
        self.setEnabled(self._editable)

    def is_editable(self) -> bool:
        return self._editable

    def has_fields(self) -> bool:
        return bool(self._fields)

    def widget_for(self, name: str) -> QWidget | None:
        item = self._fields.get(name)
        return item.widget if item is not None else None

    def refresh_visibility(self) -> None:
        """公开的依赖显隐入口，供调用方在外部改值后同步。"""
        self._refresh_visibility()

    # ─── 绘制 ─────────────────────────────────────────────

    def _clear(self) -> None:
        self._fields = {}
        self._definitions = []
        while self._layout.rowCount():
            self._layout.removeRow(0)
        # removeRow 只移除布局项；控件仍挂在 self 下，显式摘除父级后销毁，
        # 否则重复脚本 id 时 findChild 会读到上一份表单的控件。
        for child in self.findChildren(QWidget):
            child.setParent(None)
            child.deleteLater()

    def _build_field(self, definition: dict, values: dict[str, Any]) -> _Field:
        name = str(definition["name"])
        label = str(definition.get("label", name))
        param_type = definition.get("type", "select")
        default = values.get(name, definition.get("default"))
        options = definition.get("options", [])
        rows_before = self._layout.rowCount()

        if param_type == "number":
            spin = QSpinBox()
            spin.setObjectName(name)
            spin.setRange(definition.get("min", 0), definition.get("max", 999999))
            spin.setValue(int(default) if default is not None else 1)
            # 连续输入只在编辑结束时提交，避免把 1、12 这样的中间值写盘。
            spin.setKeyboardTracking(False)
            spin.valueChanged.connect(self._on_field_changed)
            self._layout.addRow(label + ":", spin)
            widget: QWidget = spin
        elif param_type == "bool":
            check = QCheckBox()
            check.setObjectName(name)
            if isinstance(default, str):
                check.setChecked(to_bool(default))
            else:
                check.setChecked(bool(default))
            check.toggled.connect(self._on_field_changed)
            self._layout.addRow(label + ":", check)
            widget = check
        elif param_type == "text":
            placeholder = str(definition.get("placeholder", ""))
            if definition.get("multiline", False):
                multiline = QPlainTextEdit()
                multiline.setMaximumHeight(120)
                multiline.setPlainText(str(default) if default is not None else "")
                multiline.setObjectName(name)
                if placeholder:
                    multiline.setPlaceholderText(placeholder)
                multiline.textChanged.connect(self._on_field_changed)
                self._layout.addRow(QLabel(label + ":"))
                self._layout.addRow(multiline)
                widget = multiline
            else:
                line = QLineEdit()
                line.setText(str(default) if default is not None else "")
                line.setObjectName(name)
                if placeholder:
                    line.setPlaceholderText(placeholder)
                line.textChanged.connect(self._on_field_changed)
                self._layout.addRow(label + ":", line)
                widget = line
        elif param_type == "checkgroup":
            container = FlowContainer()
            container.setObjectName(name)
            flow = FlowLayout(container, spacing=6)
            flow.setContentsMargins(0, 0, 0, 0)
            container.set_flow_layout(flow)
            saved = default if isinstance(default, dict) else {}
            for option in options:
                if isinstance(option, dict):
                    key = str(option["value"])
                    option_label = str(option.get("label", option["value"]))
                else:
                    key = option_label = str(option)
                check = QCheckBox(option_label)
                check.setObjectName(key)
                # 未保存过的候选默认勾选，与历史行为一致。
                check.setChecked(bool(saved.get(key, True)))
                check.toggled.connect(self._on_field_changed)
                flow.addWidget(check)
            self._layout.addRow(QLabel(label + ":"))
            self._layout.addRow(container)
            widget = container
        else:
            combo = AutoWidthComboBox()
            combo.setObjectName(name)
            if options:
                for option in options:
                    if isinstance(option, dict):
                        combo.addItem(str(option["label"]), option["value"])
                    else:
                        combo.addItem(str(option), str(option))
                if default is not None:
                    index = combo.findData(str(default))
                    if index >= 0:
                        combo.setCurrentIndex(index)
            combo.currentIndexChanged.connect(self._on_field_changed)
            self._layout.addRow(label + ":", combo)
            widget = combo

        return _Field(
            definition=definition,
            widget=widget,
            rows=list(range(rows_before, self._layout.rowCount())),
        )

    # ─── 取值 / 写值 ──────────────────────────────────────

    @staticmethod
    def _collect_field(item: _Field) -> Any:
        widget = item.widget
        if item.definition.get("type") == "checkgroup":
            group: dict[str, bool] = {}
            for check in widget.findChildren(QCheckBox):
                group[check.objectName()] = check.isChecked()
            return group
        if isinstance(widget, QSpinBox):
            blocked = widget.blockSignals(True)
            try:
                widget.interpretText()
            finally:
                widget.blockSignals(blocked)
            return str(widget.value())
        if isinstance(widget, QCheckBox):
            return widget.isChecked()
        if isinstance(widget, QComboBox):
            data = widget.currentData()
            return data if data is not None else widget.currentText()
        if isinstance(widget, QLineEdit):
            return widget.text()
        if isinstance(widget, QPlainTextEdit):
            return widget.toPlainText()
        return None

    @staticmethod
    def _apply_value(item: _Field, value: Any) -> None:
        widget = item.widget
        if item.definition.get("type") == "checkgroup":
            if not isinstance(value, dict):
                return
            for check in widget.findChildren(QCheckBox):
                blocked = check.blockSignals(True)
                try:
                    check.setChecked(bool(value.get(check.objectName(), False)))
                finally:
                    check.blockSignals(blocked)
            return
        if isinstance(widget, QSpinBox):
            blocked = widget.blockSignals(True)
            try:
                widget.setValue(int(value))
            finally:
                widget.blockSignals(blocked)
            return
        if isinstance(widget, QCheckBox):
            blocked = widget.blockSignals(True)
            try:
                widget.setChecked(bool(value))
            finally:
                widget.blockSignals(blocked)
            return
        if isinstance(widget, QComboBox):
            blocked = widget.blockSignals(True)
            try:
                index = widget.findData(value)
                if index < 0:
                    index = widget.findText(str(value))
                if index >= 0:
                    widget.setCurrentIndex(index)
            finally:
                widget.blockSignals(blocked)
            return
        if isinstance(widget, QLineEdit):
            blocked = widget.blockSignals(True)
            try:
                widget.setText(str(value))
            finally:
                widget.blockSignals(blocked)
            return
        if isinstance(widget, QPlainTextEdit):
            blocked = widget.blockSignals(True)
            try:
                widget.setPlainText(str(value))
            finally:
                widget.blockSignals(blocked)

    def _on_field_changed(self, *_args) -> None:
        self._refresh_visibility()
        values = self.collect()
        if isinstance(self.sender(), (QLineEdit, QPlainTextEdit)):
            self.text_edited.emit(values)
        else:
            self.value_changed.emit(values)

    # ─── require 显隐 ─────────────────────────────────────

    def _refresh_visibility(self) -> None:
        """按 ``require`` 依赖隐藏当前取值下不适用的行。

        只改显隐：控件和值都留着，运行时快照照旧包含这些参数，wf 引用它们
        不会未定义。
        """
        if not any(item.get("require") for item in self._definitions):
            return
        try:
            visible = visible_parameter_names(self._definitions, self.collect())
        except RequireError as exc:
            # 元数据校验期已挡住非法 require；真走到这里说明定义绕过了校验，
            # 此时全部展示，不能让表单变成空白。
            logger.warning(f"参数依赖求值失败，本次全部展示: {exc}")
            return
        for name, item in self._fields.items():
            for index in item.rows:
                self._layout.setRowVisible(index, name in visible)


__all__ = ["TaskParamForm"]
