"""图库空间的新建/编辑对话框（同一份实现，两种模式）。

- **新建**：key 可编辑（目录名与 `actives.space` / `plans[].space` 的稳定身份），
  简体中文与英文两行必填——缺一种语言的名字在另一种界面语言下就是裸露的 key；
- **编辑**：key 只读（禁用并说明原因：它同时是目录名与方案引用），只改展示名，
  全部留空表示回落到 key。

展示名按语言取值，形态与随包其他内容一致
（``{"zh_CN": "手游", "en_US": "Mobile"}``）。只写 ``name`` 一个键，磁盘上未知的
语言标签原样保留——对话框只能合并自己拥有的字段。
"""

from __future__ import annotations

from collections.abc import Collection, Mapping

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

#: 新建时两个必须填写的语言：缺一个就会在另一种界面语言下显示裸露的 key
_REQUIRED_LANGUAGES = ("zh_CN", "en_US")

_ILLEGAL_KEY_CHARS = set('/\\:*?"<>|')


def validate_space_key(key: str, existing: Collection[str] = ()) -> str | None:
    """校验新空间的 key，返回错误文案；合法返回 None。"""
    value = str(key or "").strip()
    if not value:
        return tr("空间 key 不能为空")
    if value.startswith(".") or set(value) & _ILLEGAL_KEY_CHARS:
        return tr("空间 key 不能包含路径分隔符或特殊字符")
    if value in existing:
        return tr("同名空间已存在")
    return None


def ordered_languages() -> list[dict[str, str]]:
    """随包语言，简体中文与英文排在最前，其余保持原有顺序。"""
    items = [item for item in available_languages() if item.get("code")]
    head = [item for code in _REQUIRED_LANGUAGES
            for item in items if item.get("code") == code]
    rest = [item for item in items if item.get("code") not in _REQUIRED_LANGUAGES]
    return head + rest


class SpaceNameDialog(QDialog):
    """新建或编辑图库空间；``creating`` 区分两种模式。

    - 新建：``space_key()`` 返回用户填的 key，``names()`` 返回必填的展示名；
    - 编辑：``space_key()`` 返回当前 key，``names()`` 可能为 ``None``（清空回落 key）。
    """

    def __init__(self, parent: QWidget | None, *, creating: bool = False,
                 space_key: str = "", current_name: object = None,
                 existing_keys: Collection[str] = ()):
        super().__init__(parent)
        self._creating = creating
        self._current_key = str(space_key or "")
        self._existing = set(existing_keys)
        self._edits: dict[str, QLineEdit] = {}
        self._unknown: dict[str, str] = {}

        self.setWindowTitle(tr("新建图库空间") if creating else tr("编辑图库空间"))
        self.setModal(True)

        root = QVBoxLayout(self)
        key_form = QFormLayout()
        self._key_edit = QLineEdit(self._current_key)
        self._key_edit.setMinimumWidth(_MIN_NAME_WIDTH)
        if creating:
            self._key_edit.setPlaceholderText(tr("小写字母、数字与下划线，如 android"))
            self._key_edit.textChanged.connect(self._clear_error)
        else:
            # 禁用以示「存在但不可改」，比藏起来更清楚；原因写进 tooltip 与下方说明
            self._key_edit.setEnabled(False)
            self._key_edit.setToolTip(tr("key 是目录名与方案里的引用，编辑时不可修改"))
        key_form.addRow(tr("空间 key") + ":", self._key_edit)
        root.addLayout(key_form)

        hint = QLabel(
            tr("key 是目录名与方案里的引用，创建后不可修改。简体中文与英文必须填写。")
            if creating else
            tr("key 是目录名与方案里的引用，不可修改；要换 key 请新建空间。"
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

        codes: set[str] = set()
        language_now = current_language()
        for item in ordered_languages():
            code = str(item["code"])
            codes.add(code)
            value = per_language.get(code, "")
            # 字面值（不分语言的旧名字）落在当前界面语言那一行
            if not per_language and plain and code == language_now:
                value = plain
            edit = QLineEdit(value)
            edit.setMinimumWidth(_MIN_NAME_WIDTH)
            edit.setPlaceholderText(self._current_key or code)
            edit.textChanged.connect(self._clear_error)
            self._edits[code] = edit
            label = f"{item.get('name', code)}（{code}）"
            form.addRow(label + ("" if code in _REQUIRED_LANGUAGES else ":"), edit)
        root.addLayout(form)

        # 磁盘上有、但不在随包语言表里的标签：原样保留，不在这里编辑
        for code, text in per_language.items():
            if code not in codes:
                self._unknown[code] = text

        self._error_label = QLabel()
        self._error_label.setStyleSheet("color: palette(highlight);")
        self._error_label.setWordWrap(True)
        self._error_label.hide()
        root.addWidget(self._error_label)

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
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    # ─── 取值 ────────────────────────────────────────────

    def space_key(self) -> str:
        """稳定 key：新建时取输入值，编辑时是当前 key。"""
        if self._creating:
            return self._key_edit.text().strip()
        return self._current_key

    def names(self) -> dict[str, str] | None:
        """要写盘的名字映射；编辑时全空返回 ``None``（表示清空、回落 key）。"""
        collected = {
            code: edit.text().strip()
            for code, edit in self._edits.items()
            if edit.text().strip()
        }
        collected.update(self._unknown)
        return collected or None

    # ─── 校验 ────────────────────────────────────────────

    def _clear_error(self, *_args) -> None:
        self._error_label.hide()

    def _show_error(self, message: str) -> None:
        self._error_label.setText(message)
        self._error_label.show()

    def _on_accept(self) -> None:
        if self._creating:
            problem = validate_space_key(self.space_key(), self._existing)
            if problem:
                self._show_error(problem)
                return
            missing = [code for code in _REQUIRED_LANGUAGES
                       if not self._edits[code].text().strip()]
            if missing:
                self._show_error(tr("简体中文与英文都必须填写"))
                return
        self.accept()
