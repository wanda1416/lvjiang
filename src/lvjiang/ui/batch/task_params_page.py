"""批量「任务」页：按全局/用户快速编辑勾选脚本的任务参数。

数据源与日常任务完全一致，不额外维护副本：

- 全局参数：``session.json`` 的 ``wf_configs[script_id]``（``update_wf_config``）
- 用户独立参数：用户元数据的 ``workflow_params[script_id]``
  （``get/set_user_workflow_params``）

编辑即落盘：非文本控件立即保存，文本输入按 250ms 合并保存。用户作用域下
只把改动过的字段合并进该用户的独立参数，其余字段继续跟随全局值。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ...core.config.wf_configs import get_wf_config, update_wf_config
from ...core.param_require import RequireError, visible_parameter_names
from ...core.task_params import (
    merge_task_params,
    parameters_for_env,
)
from ...core.user_config import (
    get_user_workflow_params,
    set_user_workflow_params,
)
from ...i18n import tr
from ...workflows.builtins._coerce import to_bool
from ...workflows.discovery import script_display_name
from ..widgets.combo_box import AutoWidthComboBox
from ..widgets.misc import FlowContainer, FlowLayout


@dataclass
class _ScriptForm:
    """一个脚本的参数表单及其保存基线。"""

    script_id: str
    name: str
    definitions: list[dict]
    widgets: dict[str, QWidget]
    rows: dict[str, list[int]] = field(default_factory=dict)
    baseline: dict[str, Any] = field(default_factory=dict)
    layout: QFormLayout | None = None


class TaskParamsPage(QWidget):
    """把勾选脚本的参数定义依次绘制成表单。"""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._scripts: list[dict] = []
        self._usernames: list[str] = []
        self._users_dir: Path | None = None
        self._run_env = ""
        #: "" 表示全局参数，其它值为用户名。
        self._scope = ""
        self._forms: list[_ScriptForm] = []
        self._owners: dict[int, tuple[_ScriptForm, str]] = {}
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(250)
        self._save_timer.timeout.connect(self._flush_text_edits)
        self._build_ui()

    # ─── 对外接口 ─────────────────────────────────────────

    def set_context(
        self,
        scripts: list[dict],
        usernames: list[str],
        *,
        users_dir: Path | None = None,
        run_env: str = "",
    ) -> None:
        """重建表单。``scripts`` 按勾选顺序传入脚本元数据。"""
        # 先按旧作用域保存待提交的文本编辑，避免切换范围时丢改动。
        self._save_timer.stop()
        self._save_all()
        self._scripts = list(scripts)
        self._usernames = [name for name in usernames if name]
        self._users_dir = users_dir
        self._run_env = run_env
        self._refresh_scope_combo()
        self._rebuild()

    # ─── UI ───────────────────────────────────────────────

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(6)

        scope_row = QHBoxLayout()
        scope_row.setContentsMargins(0, 0, 0, 0)
        scope_row.setSpacing(8)
        scope_label = QLabel(tr("编辑范围"))
        scope_row.addWidget(scope_label)
        self._scope_combo = AutoWidthComboBox()
        self._scope_combo.setMinimumWidth(160)
        self._scope_combo.currentIndexChanged.connect(self._on_scope_changed)
        scope_row.addWidget(self._scope_combo)
        scope_row.addStretch(1)
        # 说明走 tooltip，不在顶部常驻长文案。
        hint = tr("改动立即生效，与日常任务共用同一份参数")
        scope_label.setToolTip(hint)
        self._scope_combo.setToolTip(hint)
        layout.addLayout(scope_row)

        self._empty_hint = QLabel(
            tr("勾选的脚本没有可配置的任务参数"))
        self._empty_hint.setVisible(False)
        layout.addWidget(self._empty_hint)

        self._body = QWidget()
        self._body_layout = QVBoxLayout(self._body)
        self._body_layout.setContentsMargins(0, 0, 0, 0)
        self._body_layout.setSpacing(8)
        self._body_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(self._body)
        layout.addWidget(scroll, 1)

    def _refresh_scope_combo(self) -> None:
        combo = self._scope_combo
        combo.blockSignals(True)
        try:
            combo.clear()
            combo.addItem(tr("全局参数"), "")
            for name in self._usernames:
                combo.addItem(tr("用户：{name}").format(name=name), name)
            index = combo.findData(self._scope)
            if index < 0:
                self._scope = ""
                index = 0
            combo.setCurrentIndex(index)
        finally:
            combo.blockSignals(False)
        combo.setEnabled(bool(self._scripts))

    def _on_scope_changed(self, index: int) -> None:
        if index < 0:
            return
        self._save_timer.stop()
        self._save_all()
        data = self._scope_combo.itemData(index)
        self._scope = str(data or "")
        self._rebuild()

    # ─── 表单重建 ─────────────────────────────────────────

    def _rebuild(self) -> None:
        self._clear_body()
        self._forms = []
        self._owners = {}
        shown = 0
        for script in self._scripts:
            definitions = parameters_for_env(
                list(script.get("parameters") or []), self._run_env)
            if not definitions:
                continue
            self._build_script_form(script, definitions)
            shown += 1
        self._empty_hint.setVisible(shown == 0)

    def _clear_body(self) -> None:
        while self._body_layout.count():
            item = self._body_layout.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                # 立刻摘出父子树：deleteLater 要等事件循环，期间 findChild
                # 仍会返回旧控件（重复脚本 id 时尤其容易读到上一份表单）。
                widget.setParent(None)
                widget.deleteLater()

    def _build_script_form(
        self, script: dict, definitions: list[dict],
    ) -> None:
        script_id = str(script["id"])
        name = script_display_name(script)
        shared = get_wf_config(script_id)
        override = (
            get_user_workflow_params(self._scope, script_id, self._users_dir)
            if self._scope else None
        )
        values, _source = merge_task_params(definitions, shared, override)

        box = QGroupBox(name)
        form_layout = QFormLayout(box)
        form_layout.setContentsMargins(10, 8, 10, 8)
        form = _ScriptForm(
            script_id=script_id, name=name, definitions=definitions,
            widgets={}, layout=form_layout,
        )
        for param_def in definitions:
            self._add_param_row(form, form_layout, param_def, values)
        # 基线必须取「控件实际值」而不是 merge 结果：数字参数在合并结果里是
        # int、控件读出来是 str，拿 merge 结果当基线会把默认值误判成改动。
        form.baseline = self._collect(form)
        self._apply_visibility(form, values)
        self._forms.append(form)
        self._body_layout.addWidget(box)

    def _add_param_row(
        self, form: _ScriptForm, layout: QFormLayout,
        param_def: dict, values: dict[str, Any],
    ) -> None:
        name = str(param_def["name"])
        label = str(param_def.get("label", name))
        param_type = param_def.get("type", "select")
        default = values.get(name, param_def.get("default"))
        options = param_def.get("options", [])
        rows_before = layout.rowCount()

        if param_type == "number":
            spin = QSpinBox()
            spin.setObjectName(name)
            spin.setRange(param_def.get("min", 0), param_def.get("max", 999999))
            spin.setValue(int(default) if default is not None else 1)
            spin.valueChanged.connect(self._on_param_changed)
            layout.addRow(label + ":", spin)
            widget: QWidget = spin
        elif param_type == "bool":
            check = QCheckBox()
            check.setObjectName(name)
            if isinstance(default, str):
                check.setChecked(to_bool(default))
            else:
                check.setChecked(bool(default))
            check.toggled.connect(self._on_param_changed)
            layout.addRow(label + ":", check)
            widget = check
        elif param_type == "text":
            placeholder = param_def.get("placeholder", "")
            if param_def.get("multiline", False):
                multiline = QPlainTextEdit()
                multiline.setMaximumHeight(120)
                multiline.setPlainText(
                    str(default) if default is not None else "")
                multiline.setObjectName(name)
                if placeholder:
                    multiline.setPlaceholderText(placeholder)
                multiline.textChanged.connect(self._on_param_changed)
                layout.addRow(QLabel(label + ":"))
                layout.addRow(multiline)
                widget = multiline
            else:
                line = QLineEdit()
                line.setText(str(default) if default is not None else "")
                line.setObjectName(name)
                if placeholder:
                    line.setPlaceholderText(placeholder)
                line.textChanged.connect(self._on_param_changed)
                layout.addRow(label + ":", line)
                widget = line
        elif param_type == "checkgroup":
            container = FlowContainer()
            container.setObjectName(name)
            flow = FlowLayout(container, spacing=6)
            flow.setContentsMargins(0, 0, 0, 0)
            container.set_flow_layout(flow)
            saved = default if isinstance(default, dict) else {}
            for opt in options:
                if isinstance(opt, dict):
                    key, opt_label = str(opt["value"]), str(
                        opt.get("label", opt["value"]))
                else:
                    key = opt_label = str(opt)
                check = QCheckBox(opt_label)
                check.setObjectName(key)
                check.setChecked(bool(saved.get(key, True)))
                check.toggled.connect(self._on_param_changed)
                self._owners[id(check)] = (form, name)
                flow.addWidget(check)
            layout.addRow(QLabel(label + ":"))
            layout.addRow(container)
            widget = container
        else:
            combo = AutoWidthComboBox()
            combo.setObjectName(name)
            if param_type == "select" and options:
                for opt in options:
                    if isinstance(opt, dict):
                        combo.addItem(str(opt["label"]), opt["value"])
                    else:
                        combo.addItem(str(opt), str(opt))
                if default is not None:
                    idx = combo.findData(str(default))
                    if idx >= 0:
                        combo.setCurrentIndex(idx)
            combo.currentIndexChanged.connect(self._on_param_changed)
            layout.addRow(label + ":", combo)
            widget = combo

        form.widgets[name] = widget
        self._owners.setdefault(id(widget), (form, name))
        form.rows[name] = list(range(rows_before, layout.rowCount()))

    # ─── 取值 / 保存 ──────────────────────────────────────

    def _collect(self, form: _ScriptForm) -> dict[str, Any]:
        params: dict[str, Any] = {}
        for param_def in form.definitions:
            name = str(param_def["name"])
            widget = form.widgets.get(name)
            if widget is None:
                continue
            if param_def.get("type") == "checkgroup":
                group: dict[str, bool] = {}
                for check in widget.findChildren(QCheckBox):
                    group[check.objectName()] = check.isChecked()
                params[name] = group
            elif isinstance(widget, QSpinBox):
                blocked = widget.blockSignals(True)
                try:
                    widget.interpretText()
                finally:
                    widget.blockSignals(blocked)
                params[name] = str(widget.value())
            elif isinstance(widget, QCheckBox):
                params[name] = widget.isChecked()
            elif isinstance(widget, QComboBox):
                data = widget.currentData()
                params[name] = data if data is not None else widget.currentText()
            elif isinstance(widget, QLineEdit):
                params[name] = widget.text()
            elif isinstance(widget, QPlainTextEdit):
                params[name] = widget.toPlainText()
        return params

    def _on_param_changed(self, *_args) -> None:
        owner = self._owners.get(id(self.sender()))
        if owner is None:
            return
        form, _name = owner
        self._apply_visibility(form, self._collect(form))
        if isinstance(self.sender(), (QLineEdit, QPlainTextEdit)):
            self._save_timer.start()
        else:
            self._save_form(form)

    def _flush_text_edits(self) -> None:
        self._save_timer.stop()
        self._save_all()

    def _save_all(self) -> None:
        for form in self._forms:
            self._save_form(form)

    def _save_form(self, form: _ScriptForm) -> None:
        values = self._collect(form)
        if not values:
            return
        patch = {
            key: value for key, value in values.items()
            if key not in form.baseline or form.baseline[key] != value
        }
        if not patch:
            form.baseline = values
            return
        try:
            self._write_patch(form.script_id, patch)
        except Exception as exc:  # noqa: BLE001 - 单个脚本失败不拖垮整页
            logger.warning(
                f"批量任务参数保存失败 {form.script_id}: {exc}")
            return
        form.baseline = values

    def _write_patch(self, script_id: str, patch: dict[str, Any]) -> None:
        if not self._scope:
            update_wf_config(script_id, patch)
            return
        existing = (
            get_user_workflow_params(self._scope, script_id, self._users_dir)
            or {}
        )
        existing.update(patch)
        set_user_workflow_params(
            self._scope, script_id, existing, self._users_dir)

    # ─── require 显隐 ─────────────────────────────────────

    def _apply_visibility(
        self, form: _ScriptForm, values: dict[str, Any],
    ) -> None:
        layout = form.layout
        if layout is None or not any(
            item.get("require") for item in form.definitions
        ):
            return
        try:
            visible = visible_parameter_names(form.definitions, values)
        except RequireError as exc:
            # 元数据校验期已挡住非法 require；真走到这里说明定义绕过了校验，
            # 此时全部展示，不能让表单变成空白。
            logger.warning(f"参数依赖求值失败，本次全部展示: {exc}")
            return
        for name, indices in form.rows.items():
            for index in indices:
                layout.setRowVisible(index, name in visible)
