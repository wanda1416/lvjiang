"""激活码签发器 — 本机 GUI

只做签发与自检两件事。**不生成私钥**：那是一次性且不可逆的操作，放在随手点得到的
按钮后面迟早出事，留在 ``scripts/issue_license.py keygen`` 里。

界面分两块：上半签发，下半自检。签完的码会自动用主程序内置的公钥验一遍——发出去
之前就知道它一定能被客户端接受，而不是等用户回来说「激活码无效」。
"""

from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

from PyQt6.QtCore import QDate, Qt
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDateEdit,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from .signing import (
    DEFAULT_KEY_PATH,
    KNOWN_FEATURES,
    IssueRequest,
    LicenseError,
    SigningKeyError,
    issue,
    key_status,
    serial_is_wellformed,
    verify,
)

_MUTED = "color: palette(mid);"
_OK = "color: #2e7d32;"
_WARN = "color: #ef6c00;"
_BAD = "color: #c62828;"


class IssuerWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("律匠 · 激活码签发器")
        self.resize(720, 760)

        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.addWidget(self._build_key_box())
        layout.addWidget(self._build_issue_box())
        layout.addWidget(self._build_result_box())
        layout.addStretch()

        self._refresh_key_status()
        self._refresh_bind_state()

    # ─── 私钥 ──────────────────────────────────────────────

    def _build_key_box(self) -> QGroupBox:
        box = QGroupBox("签发私钥")
        vbox = QVBoxLayout(box)
        row = QHBoxLayout()
        self._key_edit = QLineEdit(str(DEFAULT_KEY_PATH))
        self._key_edit.textChanged.connect(self._refresh_key_status)
        row.addWidget(self._key_edit)
        browse = QPushButton("浏览…")
        browse.clicked.connect(self._on_browse_key)
        row.addWidget(browse)
        vbox.addLayout(row)
        self._key_status = QLabel()
        self._key_status.setWordWrap(True)
        vbox.addWidget(self._key_status)
        hint = QLabel("本工具不生成私钥。需要新建或轮换请用 "
                      "scripts/issue_license.py keygen——那是不可逆操作，"
                      "不该放在随手点得到的按钮后面。")
        hint.setWordWrap(True)
        hint.setStyleSheet(_MUTED)
        vbox.addWidget(hint)
        return box

    def _on_browse_key(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择私钥文件", str(Path.home()), "文本文件 (*.txt);;所有文件 (*)")
        if path:
            self._key_edit.setText(path)

    def _refresh_key_status(self):
        ok, message = key_status(Path(self._key_edit.text().strip()))
        self._key_status.setText(message)
        self._key_status.setStyleSheet(_OK if ok else _BAD)

    # ─── 签发 ──────────────────────────────────────────────

    def _build_issue_box(self) -> QGroupBox:
        box = QGroupBox("签发")
        form = QFormLayout(box)

        self._id_edit = QLineEdit()
        self._id_edit.setPlaceholderText("例如 L0042，记进你的台账")
        form.addRow("码编号：", self._id_edit)

        bind_row = QHBoxLayout()
        self._bind_serial_radio = QRadioButton("绑定机器序列号")
        self._bind_serial_radio.setChecked(True)
        self._bind_serial_radio.toggled.connect(self._refresh_bind_state)
        self._bind_none_radio = QRadioButton("免绑定（谁拿到谁能用）")
        bind_row.addWidget(self._bind_serial_radio)
        bind_row.addWidget(self._bind_none_radio)
        bind_row.addStretch()
        form.addRow("绑定方式：", bind_row)

        self._serial_edit = QLineEdit()
        self._serial_edit.setPlaceholderText(
            "用户在「配置管理 → 功能激活」里复制给你的那串")
        self._serial_edit.textChanged.connect(self._refresh_serial_hint)
        form.addRow("序列号：", self._serial_edit)
        self._serial_hint = QLabel()
        self._serial_hint.setStyleSheet(_MUTED)
        form.addRow("", self._serial_hint)

        feature_box = QVBoxLayout()
        self._feature_checks: dict[str, QCheckBox] = {}
        for name, label in KNOWN_FEATURES.items():
            check = QCheckBox(f"{label}　[{name}]")
            self._feature_checks[name] = check
            feature_box.addWidget(check)
        if not KNOWN_FEATURES:
            empty = QLabel("主程序目前没有功能接入门禁，登记表是空的。"
                           "可以先用下面的输入框签发，等功能接入后即刻生效。")
            empty.setWordWrap(True)
            empty.setStyleSheet(_MUTED)
            feature_box.addWidget(empty)
        # 自由填写：功能可能先签发、后接入；也便于签发登记表之外的试验性功能
        self._extra_features = QLineEdit()
        self._extra_features.setPlaceholderText("其他功能名，逗号分隔")
        feature_box.addWidget(self._extra_features)
        form.addRow("开放功能：", feature_box)

        expiry_row = QHBoxLayout()
        self._expiry_check = QCheckBox("设置有效期")
        self._expiry_check.toggled.connect(self._refresh_bind_state)
        expiry_row.addWidget(self._expiry_check)
        self._expiry_edit = QDateEdit()
        self._expiry_edit.setCalendarPopup(True)
        self._expiry_edit.setDate(QDate.currentDate().addYears(1))
        expiry_row.addWidget(self._expiry_edit)
        expiry_row.addStretch()
        form.addRow("有效期：", expiry_row)

        self._expiry_hint = QLabel()
        self._expiry_hint.setWordWrap(True)
        form.addRow("", self._expiry_hint)

        self._issue_btn = QPushButton("签发")
        self._issue_btn.clicked.connect(self._on_issue)
        form.addRow("", self._issue_btn)
        return box

    def _refresh_bind_state(self):
        bound = self._bind_serial_radio.isChecked()
        self._serial_edit.setEnabled(bound)
        self._serial_hint.setVisible(bound)
        self._expiry_edit.setEnabled(self._expiry_check.isChecked())
        self._refresh_serial_hint()

        if not bound and not self._expiry_check.isChecked():
            # 免绑定码是 bearer token，谁拿到谁能用；离线方案没有吊销手段，
            # 有效期是唯一的把手
            self._expiry_hint.setText(
                "⚠ 免绑定码没有有效期：它会被转发，而且离线无法吊销。建议设一个。")
            self._expiry_hint.setStyleSheet(_WARN)
        elif not bound:
            self._expiry_hint.setText("到期后客户端自动失效，续期再签一张即可。")
            self._expiry_hint.setStyleSheet(_MUTED)
        else:
            self._expiry_hint.setText("")

    def _refresh_serial_hint(self):
        if not self._bind_serial_radio.isChecked():
            return
        text = self._serial_edit.text().strip()
        if not text:
            self._serial_hint.setText("")
            return
        if serial_is_wellformed(text):
            self._serial_hint.setText("✓ 校验位正确")
            self._serial_hint.setStyleSheet(_OK)
        else:
            self._serial_hint.setText(
                "✗ 校验位不对，多半抄漏或抄错了一位——让用户重新复制")
            self._serial_hint.setStyleSheet(_BAD)

    def _selected_features(self) -> tuple[str, ...]:
        picked = [n for n, c in self._feature_checks.items() if c.isChecked()]
        for raw in self._extra_features.text().replace("，", ",").split(","):
            name = raw.strip()
            if name and name not in picked:
                picked.append(name)
        return tuple(picked)

    def _on_issue(self):
        expires: date | None = None
        if self._expiry_check.isChecked():
            qdate = self._expiry_edit.date()
            expires = date(qdate.year(), qdate.month(), qdate.day())
            if expires < date.today() + timedelta(days=1):
                QMessageBox.warning(self, "签发", "有效期必须晚于今天")
                return

        request = IssueRequest(
            code_id=self._id_edit.text(),
            bind_to_serial=self._bind_serial_radio.isChecked(),
            serial=self._serial_edit.text(),
            features=self._selected_features(),
            expires=expires,
        )
        try:
            code = issue(request, Path(self._key_edit.text().strip()))
        except (ValueError, SigningKeyError) as exc:
            QMessageBox.warning(self, "签发失败", str(exc))
            return

        self._result_edit.setPlainText(code)
        self._verify_current(code)

    # ─── 结果与自检 ────────────────────────────────────────

    def _build_result_box(self) -> QGroupBox:
        box = QGroupBox("激活码")
        vbox = QVBoxLayout(box)
        self._result_edit = QPlainTextEdit()
        self._result_edit.setPlaceholderText(
            "签发结果会出现在这里；也可以粘一张已有的码点「自检」")
        self._result_edit.setFixedHeight(110)
        vbox.addWidget(self._result_edit)

        row = QHBoxLayout()
        copy_btn = QPushButton("复制")
        copy_btn.clicked.connect(self._on_copy)
        row.addWidget(copy_btn)
        check_btn = QPushButton("自检")
        check_btn.clicked.connect(
            lambda: self._verify_current(self._result_edit.toPlainText()))
        row.addWidget(check_btn)
        row.addStretch()
        vbox.addLayout(row)

        self._verify_label = QLabel()
        self._verify_label.setWordWrap(True)
        self._verify_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        vbox.addWidget(self._verify_label)

        ledger = QLabel("记一笔台账：编号 → 发给谁 → 日期。"
                        "离线撤不掉，但下次可以不续。")
        ledger.setWordWrap(True)
        ledger.setStyleSheet(_MUTED)
        vbox.addWidget(ledger)
        return box

    def _on_copy(self):
        code = self._result_edit.toPlainText().strip()
        if not code:
            return
        clipboard = QApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(code)

    def _verify_current(self, code: str):
        """用主程序内置的公钥验一遍——发出去之前就确认客户端一定收得下"""
        code = code.strip()
        if not code:
            self._verify_label.setText("")
            return
        try:
            license_ = verify(code)
        except LicenseError as exc:
            self._verify_label.setText(f"✗ 自检未通过：{exc}")
            self._verify_label.setStyleSheet(_BAD)
            return
        bind = "免绑定" if not license_.is_bound else f"绑定 {license_.serial}"
        expires = license_.expires.isoformat() if license_.expires else "永久"
        self._verify_label.setText(
            f"✓ 自检通过（{len(code)} 字符）\n"
            f"编号 {license_.code_id} | {bind} | 有效期 {expires}\n"
            f"功能 {'、'.join(license_.features) or '无'}")
        self._verify_label.setStyleSheet(_OK)


def run() -> int:
    app = QApplication(sys.argv)
    window = IssuerWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(run())
