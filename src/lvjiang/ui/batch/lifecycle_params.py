"""生命周期参数编辑面板。

参数定义实时读工作流文件头，值属于本次运行草稿：主页面改这里，绝不写配置组。
"""
from __future__ import annotations

from typing import Any, cast

from loguru import logger
from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QGroupBox,
    QHBoxLayout,
    QLineEdit,
    QPlainTextEdit,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ...core.batch_config import BatchWorkflows, lifecycle_parameter_definitions
from ...i18n import tr
from ...workflows.builtins._coerce import to_bool
from ..combo_box import AutoWidthComboBox, ComboWidthMode
from .compact_fields import CompactFields


class LifecycleParamsPanel(QWidget):
    """按阶段分组编辑生命周期参数。

    只负责取值与展示；参数有哪些仍由工作流文件头决定。``values()`` 返回当前
    控件读出的全部值，调用方直接写运行草稿。
    """

    changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._groups: list[QGroupBox] = []
        self._forms: dict[str, CompactFields] = {}
        self._widgets: dict[tuple[str, str], QWidget] = {}
        self._types: dict[tuple[str, str], str] = {}
        self._defs: dict[str, list[dict]] = {}

    def has_content(self) -> bool:
        return bool(self._groups)

    def set_workflows(
        self,
        workflows: BatchWorkflows,
        values: dict[str, dict],
    ) -> None:
        """按当前工作流重建面板；``values`` 是本次运行的已存参数。"""
        self._clear()
        if not isinstance(values, dict):
            values = {}
        phase_labels = {
            "batch_setup": tr("批次准备"),
            "prepare_item": tr("条目准备"),
            "finish_item": tr("条目收尾"),
            "batch_teardown": tr("批次收尾"),
            # 异常恢复也可声明参数，不能遗漏这个阶段的编辑入口。
            "recover_unattended": tr("异常恢复"),
        }
        definitions = lifecycle_parameter_definitions(workflows)
        for phase, params in definitions.items():
            if not params:
                continue
            group = QGroupBox(phase_labels[phase])
            group_layout = QVBoxLayout(group)
            form = CompactFields(group)
            group_layout.addWidget(form)
            self._groups.append(group)
            self._forms[phase] = form
            self._defs[phase] = list(params)
            saved = values.get(phase, {})
            if not isinstance(saved, dict):
                saved = {}
            for definition in params:
                name = str(definition["name"])
                label = str(definition.get("label") or name)
                value = saved.get(name, definition.get("default"))
                param_type = definition.get("type", "select")
                widget: QWidget
                if param_type == "bool":
                    checkbox = QCheckBox()
                    checkbox.setChecked(to_bool(value))
                    checkbox.toggled.connect(self._on_changed)
                    widget = checkbox
                elif param_type == "number":
                    spin = QSpinBox()
                    spin.setRange(
                        int(definition.get("min", 0)),
                        int(definition.get("max", 999999)),
                    )
                    spin.setValue(int(value) if value is not None else 0)
                    spin.valueChanged.connect(self._on_changed)
                    widget = spin
                elif param_type == "select":
                    combo = AutoWidthComboBox(width_mode=ComboWidthMode.FULL)
                    for option in definition.get("options", []):
                        if isinstance(option, dict):
                            combo.addItem(
                                str(option.get("label", option["value"])),
                                option["value"])
                        else:
                            combo.addItem(str(option), str(option))
                    selected = combo.findData(value)
                    if selected >= 0:
                        combo.setCurrentIndex(selected)
                    combo.currentIndexChanged.connect(self._on_changed)
                    widget = combo
                elif param_type == "checkgroup":
                    container = QWidget()
                    options_layout = QHBoxLayout(container)
                    options_layout.setContentsMargins(0, 0, 0, 0)
                    selected_values = value if isinstance(value, dict) else {}
                    for option in definition.get("options", []):
                        if isinstance(option, dict):
                            option_name = str(option["value"])
                            option_label = str(option.get("label", option_name))
                        else:
                            option_name = option_label = str(option)
                        checkbox = QCheckBox(option_label)
                        checkbox.setObjectName(option_name)
                        checkbox.setChecked(
                            bool(selected_values.get(option_name, True)))
                        checkbox.toggled.connect(self._on_changed)
                        options_layout.addWidget(checkbox)
                    options_layout.addStretch()
                    widget = container
                else:
                    edit = (QPlainTextEdit() if definition.get("multiline")
                            else QLineEdit())
                    if isinstance(edit, QPlainTextEdit):
                        edit.setMaximumHeight(100)
                        edit.setPlainText(str(value or ""))
                        edit.textChanged.connect(self._on_changed)
                    else:
                        edit.setText(str(value or ""))
                        edit.textChanged.connect(self._on_changed)
                    widget = edit
                widget.setObjectName(name)
                self._widgets[(phase, name)] = widget
                self._types[(phase, name)] = str(param_type)
                form.add_field(
                    f"{label}：", widget,
                    full_row=param_type not in ("bool", "number", "select"))
            self._layout.addWidget(group)
        self.setVisible(bool(self._groups))
        self._refresh_visibility()

    def values(self) -> dict[str, dict]:
        """读出各阶段参数值；写草稿与计算 require 可见性共用这一份。"""
        result: dict[str, dict] = {}
        for key, widget in self._widgets.items():
            phase, name = key
            param_type = self._types[key]
            value: Any
            if isinstance(widget, QCheckBox):
                value = widget.isChecked()
            elif isinstance(widget, QSpinBox):
                value = widget.value()
            elif isinstance(widget, AutoWidthComboBox):
                value = widget.currentData()
            elif isinstance(widget, QPlainTextEdit):
                value = widget.toPlainText()
            elif param_type == "checkgroup":
                value = {
                    checkbox.objectName(): checkbox.isChecked()
                    for checkbox in widget.findChildren(QCheckBox)
                }
            else:
                value = cast(QLineEdit, widget).text()
            result.setdefault(phase, {})[name] = value
        return result

    def _on_changed(self, *_args) -> None:
        """只刷新 require 显隐并通知外部；落盘由调用方决定节奏。"""
        self._refresh_visibility()
        self.changed.emit()

    def _refresh_visibility(self) -> None:
        """按 require 依赖隐藏当前取值下不适用的参数行。

        只改显隐：控件和值都留着，本次运行的参数快照照旧包含它们。
        """
        if not any(item.get("require")
                   for params in self._defs.values() for item in params):
            return
        from ...core.param_require import RequireError, visible_parameter_names
        collected = self.values()
        for phase, params in self._defs.items():
            form = self._forms.get(phase)
            if form is None:
                continue
            try:
                visible = visible_parameter_names(
                    params, collected.get(phase, {}))
            except RequireError as exc:
                logger.warning(f"生命周期参数依赖求值失败，本次全部展示: {exc}")
                continue
            for (row_phase, name), widget in self._widgets.items():
                if row_phase == phase:
                    form.set_field_visible(widget, name in visible)

    def _clear(self) -> None:
        while self._layout.count():
            layout_item = self._layout.takeAt(0)
            old_widget = layout_item.widget() if layout_item is not None else None
            if old_widget is not None:
                old_widget.deleteLater()
        self._groups.clear()
        self._forms.clear()
        self._widgets.clear()
        self._types.clear()
        self._defs.clear()
