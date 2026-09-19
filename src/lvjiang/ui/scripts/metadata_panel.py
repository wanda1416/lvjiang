"""当前 WF 文件的结构化 front-matter 编辑面板。"""

from __future__ import annotations

from typing import Any

import yaml
from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ...core.config.resolver import load_available_envs
from ...i18n import tr
from ...workflows.metadata import parse_metadata
from ..button_styles import apply_button_style


def replace_front_matter(text: str, metadata: dict[str, Any]) -> str:
    """只替换文件开头连续的 ``#%`` 块，正文逐字保留。"""
    lines = text.splitlines(keepends=True)
    index = 0
    while index < len(lines) and lines[index].lstrip().startswith("#%"):
        index += 1
    body = "".join(lines[index:])
    dumped = yaml.safe_dump(
        metadata, allow_unicode=True, sort_keys=False,
        default_flow_style=False,
    ).rstrip("\n")
    header = "\n".join(f"#% {line}" for line in dumped.splitlines())
    if body and not body.startswith("\n"):
        header += "\n"
    return header + "\n" + body


class MetadataPanel(QWidget):
    """元数据表单；应用后把完整文本交回编辑器，不自行落盘。"""

    text_applied = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._text = ""
        self._editable = False
        self._setup_ui()

    def _setup_ui(self) -> None:
        root = QVBoxLayout(self)
        hint = QLabel(tr(
            "这里编辑的是脚本自身的 #% 声明。修改会写回代码缓冲区，"
            "仍需点击上方“保存”才会落盘；用户显示偏好请在“脚本配置”页修改。"
        ))
        hint.setWordWrap(True)
        hint.setStyleSheet("color: palette(mid);")
        root.addWidget(hint)

        form = QFormLayout()
        self.edit_id = QLineEdit()
        self.edit_id.setReadOnly(True)
        self.edit_id.setToolTip(tr("脚本 id 是稳定身份，新建后不在表单中修改"))
        self.edit_name = QLineEdit()
        self.edit_note = QLineEdit()
        self.combo_scope = QComboBox()
        self.combo_scope.addItem(tr("日常"), "daily")
        self.combo_scope.addItem(tr("专用"), "dedicated")
        form.addRow("id", self.edit_id)
        form.addRow(tr("名称"), self.edit_name)
        form.addRow(tr("说明"), self.edit_note)
        form.addRow(tr("脚本性质"), self.combo_scope)

        # 环境不是常量：来自系统参数 app.yaml 的 envs，与主界面环境下拉框
        # 同源。env 是列表（脚本可同时声明多个环境），所以用复选框组而不是
        # 单选下拉框。
        self._env_row = QHBoxLayout()
        self._env_checks: dict[str, QCheckBox] = {}
        # stretch 先加入，后续无论构造期还是加载未知环境，都统一插在它前面。
        self._env_row.addStretch()
        for key, display in load_available_envs():
            self._add_env_check(key, display)
        form.addRow(tr("运行环境"), self._env_row)

        traits = QHBoxLayout()
        self.check_runnable = QCheckBox(tr("可独立运行"))
        self.check_batchable = QCheckBox(tr("可批量运行"))
        self.check_hidden = QCheckBox(tr("默认隐藏"))
        traits.addWidget(self.check_runnable)
        traits.addWidget(self.check_batchable)
        traits.addWidget(self.check_hidden)
        traits.addStretch()
        form.addRow(tr("脚本声明"), traits)
        self.edit_batch_check = QLineEdit()
        self.edit_batch_check.setPlaceholderText(tr("可选，例如 check_batch"))
        self.edit_batch_check.setToolTip(tr(
            "批量调度在条目准备前调用的当前 WF 内部子过程；直接运行不会调用"))
        form.addRow(tr("批量检查子过程"), self.edit_batch_check)
        root.addLayout(form)

        root.addWidget(QLabel(tr("参数定义（YAML 列表）")))
        self.edit_parameters = QPlainTextEdit()
        self.edit_parameters.setPlaceholderText(
            "- name: example\n  label: 示例参数\n  type: bool\n  default: false"
        )
        root.addWidget(self.edit_parameters, 1)

        actions = QHBoxLayout()
        actions.addStretch()
        self.btn_reload = QPushButton(tr("从代码重新加载"))
        self.btn_apply = QPushButton(tr("应用到代码"))
        apply_button_style(self.btn_reload, variant="neutral")
        apply_button_style(self.btn_apply)
        self.btn_reload.clicked.connect(self._reload)
        self.btn_apply.clicked.connect(self._apply)
        self.check_batchable.toggled.connect(
            lambda checked: self.check_runnable.setChecked(True) if checked else None
        )
        self.check_runnable.toggled.connect(
            lambda checked: self.check_batchable.setChecked(False) if not checked else None
        )
        actions.addWidget(self.btn_reload)
        actions.addWidget(self.btn_apply)
        root.addLayout(actions)

    def _add_env_check(self, key: str, display: str) -> QCheckBox:
        check = QCheckBox(display)
        check.setToolTip(key)
        self._env_checks[key] = check
        index = max(self._env_row.count() - 1, 0)
        self._env_row.insertWidget(index, check)
        return check

    def _set_env(self, env: list[str]) -> None:
        """按脚本声明勾选环境。声明了系统参数里没有的环境时也要显示出来，
        否则“应用到代码”会把它悄悄丢掉。"""
        for key in env:
            if key not in self._env_checks:
                self._add_env_check(key, f"{key}（系统参数中未定义）")
        for key, check in self._env_checks.items():
            check.setChecked(key in env)

    def _selected_env(self) -> list[str]:
        return [key for key, check in self._env_checks.items() if check.isChecked()]

    def load_text(self, text: str, *, editable: bool, fallback_id: str = "") -> None:
        self._text = text
        self._editable = editable
        try:
            meta = parse_metadata(text)
        except Exception as exc:  # 表单绝不能覆盖无法解析的原始声明
            self._set_enabled(False)
            self.edit_parameters.setPlainText(tr("元数据解析失败：{exc}").format(exc=exc))
            return
        self._set_enabled(editable)
        self.edit_id.setText(str(meta.get("id") or fallback_id))
        self.edit_name.setText(str(meta.get("name") or ""))
        self.edit_note.setText(str(meta.get("note") or ""))
        self.combo_scope.setCurrentIndex(
            max(self.combo_scope.findData(meta.get("scope") or "daily"), 0)
        )
        self._set_env(list(meta.get("env") or []))
        self.check_runnable.setChecked(bool(meta.get("runnable", False)))
        self.check_batchable.setChecked(bool(meta.get("batchable", False)))
        self.check_hidden.setChecked(bool(meta.get("hidden", False)))
        self.edit_batch_check.setText(str(meta.get("batch_check") or ""))
        params = meta.get("parameters") or []
        self.edit_parameters.setPlainText(
            yaml.safe_dump(params, allow_unicode=True, sort_keys=False).rstrip("\n")
            if params else "[]"
        )

    def _set_enabled(self, enabled: bool) -> None:
        for widget in (
            self.edit_name, self.edit_note, self.combo_scope,
            *self._env_checks.values(), self.check_runnable,
            self.check_batchable, self.check_hidden, self.edit_parameters,
            self.edit_batch_check,
            self.btn_apply,
        ):
            widget.setEnabled(enabled)
        self.btn_reload.setEnabled(bool(self._text))

    def _reload(self) -> None:
        self.load_text(
            self._text, editable=self._editable,
            fallback_id=self.edit_id.text(),
        )

    def _apply(self) -> None:
        try:
            parameters = yaml.safe_load(self.edit_parameters.toPlainText() or "[]")
            if not isinstance(parameters, list):
                raise ValueError(tr("参数定义必须是 YAML 列表"))
            metadata: dict[str, Any] = {}
            if self.edit_id.text().strip():
                metadata["id"] = self.edit_id.text().strip()
            if self.edit_name.text().strip():
                metadata["name"] = self.edit_name.text().strip()
            if self.edit_note.text().strip():
                metadata["note"] = self.edit_note.text().strip()
            metadata["scope"] = self.combo_scope.currentData() or "daily"
            env = self._selected_env()
            if env:
                metadata["env"] = env
            metadata["runnable"] = self.check_runnable.isChecked()
            metadata["batchable"] = self.check_batchable.isChecked()
            metadata["hidden"] = self.check_hidden.isChecked()
            if self.edit_batch_check.text().strip():
                metadata["batch_check"] = self.edit_batch_check.text().strip()
            if parameters:
                metadata["parameters"] = parameters
            candidate = replace_front_matter(self._text, metadata)
            parse_metadata(candidate)  # 使用运行时同一套严格校验
        except Exception as exc:
            QMessageBox.warning(self, tr("元数据无效"), str(exc))
            return
        self._text = candidate
        self.text_applied.emit(candidate)
