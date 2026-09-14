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

        env_row = QHBoxLayout()
        self.check_android = QCheckBox("Android")
        self.check_desktop = QCheckBox("Windows")
        env_row.addWidget(self.check_android)
        env_row.addWidget(self.check_desktop)
        env_row.addStretch()
        form.addRow(tr("运行环境"), env_row)

        traits = QHBoxLayout()
        self.check_runnable = QCheckBox(tr("可独立运行"))
        self.check_batchable = QCheckBox(tr("可批量运行"))
        self.check_hidden = QCheckBox(tr("默认隐藏"))
        traits.addWidget(self.check_runnable)
        traits.addWidget(self.check_batchable)
        traits.addWidget(self.check_hidden)
        traits.addStretch()
        form.addRow(tr("脚本声明"), traits)
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
        env = meta.get("env") or []
        self.check_android.setChecked("android" in env)
        self.check_desktop.setChecked("desktop" in env)
        self.check_runnable.setChecked(bool(meta.get("runnable", False)))
        self.check_batchable.setChecked(bool(meta.get("batchable", False)))
        self.check_hidden.setChecked(bool(meta.get("hidden", False)))
        params = meta.get("parameters") or []
        self.edit_parameters.setPlainText(
            yaml.safe_dump(params, allow_unicode=True, sort_keys=False).rstrip("\n")
            if params else "[]"
        )

    def _set_enabled(self, enabled: bool) -> None:
        for widget in (
            self.edit_name, self.edit_note, self.combo_scope,
            self.check_android, self.check_desktop, self.check_runnable,
            self.check_batchable, self.check_hidden, self.edit_parameters,
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
            env = []
            if self.check_android.isChecked():
                env.append("android")
            if self.check_desktop.isChecked():
                env.append("desktop")
            if env:
                metadata["env"] = env
            metadata["runnable"] = self.check_runnable.isChecked()
            metadata["batchable"] = self.check_batchable.isChecked()
            metadata["hidden"] = self.check_hidden.isChecked()
            if parameters:
                metadata["parameters"] = parameters
            candidate = replace_front_matter(self._text, metadata)
            parse_metadata(candidate)  # 使用运行时同一套严格校验
        except Exception as exc:
            QMessageBox.warning(self, tr("元数据无效"), str(exc))
            return
        self._text = candidate
        self.text_applied.emit(candidate)
