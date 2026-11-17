"""图库空间展示名编辑对话框。

只改展示名，**不改 key**：key 是目录名与 ``actives.space`` / ``plans[].space``
里的稳定身份，换 key 等于换空间（要换请新建）。展示名允许按语言取值，
形态与随包其他内容一致（``{"zh_CN": "手游", "en_US": "Mobile"}``）；
全部留空表示「回落到 key 显示」。

只写 ``name`` 一个键，其余同语言条目、磁盘上未知的语言标签都原样保留——
对话框只能合并自己拥有的字段。
"""

from __future__ import annotations

from collections.abc import Mapping

from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)

from ...i18n import available_languages, current_language, tr
from ..widgets.button_styles import apply_button_style

#: 输入框至少容纳 12 个全角字符，常见展示名不会只露出一截
_MIN_NAME_WIDTH = 180


class SpaceNameDialog(QDialog):
    """编辑某一图库空间的多语言展示名；``names()`` 返回要落盘的值。"""

    def __init__(self, parent: QWidget | None, *, space_key: str,
                 current_name: object):
        super().__init__(parent)
        self._space_key = space_key
        self._edits: dict[str, QLineEdit] = {}
        self._unknown: dict[str, str] = {}

        self.setWindowTitle(tr("编辑图库空间"))
        self.setModal(True)

        root = QVBoxLayout(self)
        key_row = QFormLayout()
        key_label = QLabel(space_key)
        key_label.setTextInteractionFlags(
            key_label.textInteractionFlags().TextSelectableByMouse)
        key_row.addRow(tr("空间 key") + ":", key_label)
        root.addLayout(key_row)

        hint = QLabel(tr(
            "key 是目录名与方案里的引用，不可修改；要换 key 请新建空间。"
            "展示名留空则直接显示 key。"))
        hint.setWordWrap(True)
        hint.setStyleSheet("color: palette(mid);")
        root.addWidget(hint)

        form = QFormLayout()
        per_language: dict[str, str] = {}
        plain = ""
        if isinstance(current_name, Mapping):
            per_language = {str(k): str(v) for k, v in current_name.items()
                            if isinstance(v, str)}
        elif isinstance(current_name, str):
            plain = current_name

        codes = set()
        language_now = current_language()
        for item in available_languages():
            code = item.get("code", "")
            if not code:
                continue
            codes.add(code)
            value = per_language.get(code, "")
            # 字面值（不分语言的旧名字）落在当前界面语言那一行
            if not per_language and plain and code == language_now:
                value = plain
            edit = QLineEdit(value)
            edit.setMinimumWidth(_MIN_NAME_WIDTH)
            edit.setPlaceholderText(space_key)
            self._edits[code] = edit
            form.addRow(f"{item.get('name', code)}（{code}）:", edit)
        root.addLayout(form)

        # 磁盘上有、但不在随包语言表里的标签：原样保留，不在这里编辑
        for code, text in per_language.items():
            if code not in codes:
                self._unknown[code] = text

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel)
        save_btn = buttons.button(QDialogButtonBox.StandardButton.Save)
        cancel_btn = buttons.button(QDialogButtonBox.StandardButton.Cancel)
        if save_btn is not None:
            save_btn.setText(tr("保存"))
            apply_button_style(save_btn)
        if cancel_btn is not None:
            cancel_btn.setText(tr("取消"))
            apply_button_style(cancel_btn, variant="neutral")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def names(self) -> dict[str, str] | None:
        """要写盘的名字映射；全空返回 ``None``（表示清空、回落 key）。"""
        collected = {
            code: edit.text().strip()
            for code, edit in self._edits.items()
            if edit.text().strip()
        }
        collected.update(self._unknown)
        return collected or None
