"""批量「任务」页：按全局/用户快速编辑勾选脚本的任务参数。

数据源与日常任务完全一致，不额外维护副本：

- 全局参数：``session.json`` 的 ``wf_configs[script_id]``（``update_wf_config``）
- 用户独立参数：用户元数据的 ``workflow_params[script_id]``
  （``get/set_user_workflow_params``）

编辑即落盘：非文本控件立即保存，文本输入按 250ms 合并保存。用户作用域下
只把改动过的字段合并进该用户的独立参数，其余字段继续跟随全局值。

参数控件由 :class:`~lvjiang.ui.widgets.task_param_form.TaskParamForm` 绘制，
与日常页、工具「任务参数」对话框共用同一份实现。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ...core.config.wf_configs import get_wf_config, update_wf_config
from ...core.task_params import (
    merge_task_params,
    parameters_for_env,
)
from ...core.users.document import (
    get_user_workflow_params,
    set_user_workflow_params,
)
from ...i18n import tr
from ...workflows.discovery import script_display_name
from ..widgets.combo_box import AutoWidthComboBox
from ..widgets.task_param_form import TaskParamForm


@dataclass
class _ScriptForm:
    """一个脚本的参数表单及其保存基线。"""

    script_id: str
    name: str
    definitions: list[dict]
    form: TaskParamForm
    baseline: dict[str, Any] = field(default_factory=dict)


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
        box_layout = QVBoxLayout(box)
        box_layout.setContentsMargins(10, 8, 10, 8)
        param_form = TaskParamForm(box)
        box_layout.addWidget(param_form)
        param_form.set_parameters(definitions, values, editable=True)
        form = _ScriptForm(
            script_id=script_id, name=name, definitions=definitions,
            form=param_form,
        )
        # 基线必须取「控件实际值」而不是 merge 结果：数字参数在合并结果里是
        # int、控件读出来是 str，拿 merge 结果当基线会把默认值误判成改动。
        form.baseline = param_form.collect()
        param_form.value_changed.connect(
            lambda _values, target=form: self._save_form(target))
        param_form.text_edited.connect(lambda _values: self._save_timer.start())
        self._forms.append(form)
        self._body_layout.addWidget(box)

    # ─── 取值 / 保存 ──────────────────────────────────────

    def _collect(self, form: _ScriptForm) -> dict[str, Any]:
        return form.form.collect()

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
