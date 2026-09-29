"""激活码签发器 — 本机 GUI

只做签发与自检两件事。**不生成私钥**：那是一次性且不可逆的操作，放在随手点得到的
按钮后面迟早出事，留在同目录的 ``keygen.py`` 命令行里。

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
    QButtonGroup,
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
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .ledger import (
    DEFAULT_LEDGER_PATH,
    code_id_exists,
    list_records,
    make_code_id,
    next_seq,
    record_issue,
)
from .signing import (
    DEFAULT_KEY_PATH,
    IssueRequest,
    LicenseError,
    SigningKeyError,
    issue,
    key_status,
    load_levels,
    normalize_serial,
    serial_is_wellformed,
    verify,
)


def _add_months(start: date, months: int) -> date:
    """加 N 个月。用日历月而不是 30 天：用户说的「1 个月」是日历上的一个月。

    落到不存在的日期（1/31 加一个月）时退到当月最后一天，与常见日历控件一致。
    """
    total = start.month - 1 + months
    year = start.year + total // 12
    month = total % 12 + 1
    day = min(start.day, _days_in_month(year, month))
    return date(year, month, day)


def _days_in_month(year: int, month: int) -> int:
    import calendar
    return calendar.monthrange(year, month)[1]


_MUTED = "color: palette(mid);"
_OK = "color: #2e7d32;"
_WARN = "color: #ef6c00;"
_BAD = "color: #c62828;"


class IssuerWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("律匠 · 激活码签发器")
        self.resize(720, 760)

        tabs = QTabWidget()
        self.setCentralWidget(tabs)

        issue_tab = QWidget()
        layout = QVBoxLayout(issue_tab)
        layout.addWidget(self._build_key_box())
        layout.addWidget(self._build_issue_box())
        layout.addWidget(self._build_result_box())
        layout.addStretch()
        tabs.addTab(issue_tab, "签发")
        tabs.addTab(self._build_ledger_tab(), "台账")

        self._refresh_key_status()
        self._refresh_ledger()
        self._allocate_seq()
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
        hint = QLabel("本工具不生成私钥。需要新建或轮换请用同目录的 "
                      "keygen.py——那是不可逆操作，"
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

        # 绑定方式放第一行：它决定编号前缀，也决定下面要不要填序列号
        bind_row = QHBoxLayout()
        self._bind_serial_radio = QRadioButton("绑定机器序列号")
        self._bind_serial_radio.setChecked(True)
        self._bind_serial_radio.toggled.connect(self._refresh_bind_state)
        self._bind_none_radio = QRadioButton("免绑定（谁拿到谁能用）")
        bind_row.addWidget(self._bind_serial_radio)
        bind_row.addWidget(self._bind_none_radio)
        bind_row.addStretch()
        form.addRow("绑定方式：", bind_row)

        # 编号只读、自动生成：它是「前缀 + 台账流水号」算出来的，手改一下就可能
        # 与台账里已有的号撞上，而撞号之后「用户报编号查去向」就废了
        self._id_edit = QLineEdit()
        self._id_edit.setReadOnly(True)
        self._id_edit.setToolTip("按绑定方式与台账流水号自动生成，不可手改")
        form.addRow("码编号：", self._id_edit)

        self._serial_edit = QLineEdit()
        self._serial_edit.setPlaceholderText(
            "用户在「配置管理 → 功能激活」里复制给你的那串")
        self._serial_edit.textChanged.connect(self._refresh_serial_hint)
        form.addRow("序列号：", self._serial_edit)
        self._serial_hint = QLabel()
        self._serial_hint.setStyleSheet(_MUTED)
        form.addRow("", self._serial_hint)

        # 只列登记表里的等级，不提供自由填写：手打的名字客户端认不出来，
        # 签出来也开不了任何东西，而且要等用户回来才发现
        level_box = QVBoxLayout()
        self._level_checks: dict[str, QCheckBox] = {}
        levels = load_levels()
        for level in levels:
            check = QCheckBox(level.display)
            self._level_checks[level.name] = check
            level_box.addWidget(check)
        if levels:
            self._level_checks[levels[0].name].setChecked(True)
        else:
            empty = QLabel("等级表 levels.json 读不出来，无法签发。")
            empty.setWordWrap(True)
            empty.setStyleSheet(_BAD)
            level_box.addWidget(empty)
        form.addRow("授权等级：", level_box)

        # 先建日期框与提示标签，再建单选：勾选会立刻触发刷新回调，
        # 回调要用到这两个控件，顺序反了构造期就 AttributeError
        self._expiry_edit = QDateEdit()
        self._expiry_edit.setCalendarPopup(True)
        self._expiry_edit.setDate(QDate.currentDate().addYears(1))
        self._expiry_edit.dateChanged.connect(self._refresh_expiry_preview)
        self._expiry_hint = QLabel()
        self._expiry_hint.setWordWrap(True)

        expiry_box = QVBoxLayout()
        quick_row = QHBoxLayout()
        self._expiry_group = QButtonGroup(self)
        self._expiry_radios: dict[str, QRadioButton] = {}
        for key, text in (("1m", "1 个月"), ("3m", "3 个月"),
                          ("1y", "1 年"), ("forever", "永久"),
                          ("custom", "自定义")):
            radio = QRadioButton(text)
            self._expiry_group.addButton(radio)
            self._expiry_radios[key] = radio
            radio.toggled.connect(self._refresh_bind_state)
            quick_row.addWidget(radio)
        quick_row.addStretch()
        self._expiry_radios["1y"].setChecked(True)
        expiry_box.addLayout(quick_row)
        expiry_box.addWidget(self._expiry_edit)
        form.addRow("有效期：", expiry_box)
        form.addRow("", self._expiry_hint)

        self._note_edit = QLineEdit()
        self._note_edit.setPlaceholderText("发给谁，写进台账，不进激活码")
        form.addRow("备注：", self._note_edit)

        self._issue_btn = QPushButton("签发")
        self._issue_btn.clicked.connect(self._on_issue)
        form.addRow("", self._issue_btn)
        return box

    def _allocate_seq(self):
        """从台账取下一个流水号。两类码共用一条序列，所以数字全局唯一。"""
        try:
            self._pending_seq = next_seq()
        except Exception as exc:  # noqa: BLE001 - 台账不可用时不该连签发都做不了
            self._pending_seq = None
            self._id_edit.setPlaceholderText(f"台账不可用：{exc}")
        self._refresh_code_id()

    def _refresh_code_id(self):
        """按当前绑定方式渲染编号；流水号不变，只换前缀"""
        if getattr(self, "_pending_seq", None) is None:
            self._id_edit.clear()
            return
        bind = "serial" if self._bind_serial_radio.isChecked() else "none"
        self._id_edit.setText(make_code_id(bind, self._pending_seq))

    def _refresh_bind_state(self):
        bound = self._bind_serial_radio.isChecked()
        self._refresh_code_id()
        self._serial_edit.setEnabled(bound)
        self._serial_hint.setVisible(bound)
        self._refresh_serial_hint()

        # 「永久」只对绑机码开放：免绑定码是 bearer token，谁拿到谁能用，
        # 离线又没有吊销手段——永久的免绑定码一旦流出就再也收不回来了
        forever = self._expiry_radios["forever"]
        forever.setEnabled(bound)
        forever.setToolTip("" if bound else "免绑定码必须设有效期，不能签永久")
        if not bound and forever.isChecked():
            self._expiry_radios["1y"].setChecked(True)

        self._expiry_edit.setEnabled(self._expiry_radios["custom"].isChecked())
        self._refresh_expiry_preview()

    def _selected_expiry(self) -> date | None:
        """按快捷选项算出到期日；「永久」返回 None"""
        today = date.today()
        if self._expiry_radios["forever"].isChecked():
            return None
        if self._expiry_radios["1m"].isChecked():
            return _add_months(today, 1)
        if self._expiry_radios["3m"].isChecked():
            return _add_months(today, 3)
        if self._expiry_radios["1y"].isChecked():
            return _add_months(today, 12)
        qdate = self._expiry_edit.date()
        return date(qdate.year(), qdate.month(), qdate.day())

    def _refresh_expiry_preview(self):
        expires = self._selected_expiry()
        if expires is None:
            self._expiry_hint.setText("永久有效，客户端不会因时间失效。")
            self._expiry_hint.setStyleSheet(_MUTED)
            return
        days = (expires - date.today()).days
        self._expiry_hint.setText(
            f"到期日 {expires.isoformat()}（{days} 天后）。到期后客户端自动失效，"
            f"续期再签一张即可。")
        self._expiry_hint.setStyleSheet(_MUTED if days > 0 else _BAD)

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

    def _selected_levels(self) -> tuple[str, ...]:
        return tuple(n for n, c in self._level_checks.items() if c.isChecked())

    def _on_issue(self):
        expires = self._selected_expiry()
        if expires is not None and expires < date.today() + timedelta(days=1):
            QMessageBox.warning(self, "签发", "有效期必须晚于今天")
            return

        code_id = self._id_edit.text().strip()
        if code_id and code_id_exists(code_id):
            # 编号重复，「用户报编号查去向」就失效了——台账的价值正在于此
            confirm = QMessageBox.question(
                self, "编号重复",
                f"台账里已经有 {code_id} 了。重复编号会让按编号查询查出多条，"
                f"分不清用户手上的是哪一张。\n仍然使用这个编号？")
            if confirm != QMessageBox.StandardButton.Yes:
                return

        request = IssueRequest(
            code_id=code_id,
            bind_to_serial=self._bind_serial_radio.isChecked(),
            serial=self._serial_edit.text(),
            features=self._selected_levels(),
            expires=expires,
        )
        try:
            code = issue(request, Path(self._key_edit.text().strip()))
        except (ValueError, SigningKeyError) as exc:
            QMessageBox.warning(self, "签发失败", str(exc))
            return

        # 先落台账再显示：离线撤不掉一张发出去的码，唯一的把手是下次不续，
        # 而那要求你知道这张码是谁的。记账失败必须让人看见，不能静默吞掉。
        try:
            record_issue(
                seq=self._pending_seq,
                code_id=request.code_id.strip(),
                bind="serial" if request.bind_to_serial else "none",
                serial=(normalize_serial(request.serial)
                        if request.bind_to_serial else None),
                levels=request.features,
                expires=expires,
                code=code,
                note=self._note_edit.text().strip(),
            )
        except Exception as exc:  # noqa: BLE001 - 台账失败不该吞
            QMessageBox.warning(
                self, "台账写入失败",
                f"激活码已签出，但没能记进台账：{exc}\n请自行记录这张码的去向。")

        self._result_edit.setPlainText(code)
        self._verify_current(code)
        self._refresh_ledger()
        self._allocate_seq()
        self._note_edit.clear()

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

    def _build_ledger_tab(self) -> QWidget:
        """签发记录：离线撤不掉码，能查到「这张码是谁的」才谈得上下次不续"""
        tab = QWidget()
        vbox = QVBoxLayout(tab)

        self._ledger_table = QTableWidget(0, 7)
        self._ledger_table.setHorizontalHeaderLabels(
            ["编号", "绑定", "等级", "到期", "签发时间", "备注", "状态"])
        self._ledger_table.horizontalHeader().setStretchLastSection(True)
        self._ledger_table.setEditTriggers(
            QTableWidget.EditTrigger.NoEditTriggers)
        self._ledger_table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows)
        self._ledger_table.itemSelectionChanged.connect(
            self._on_ledger_selected)
        vbox.addWidget(self._ledger_table)

        row = QHBoxLayout()
        refresh_btn = QPushButton("刷新")
        refresh_btn.clicked.connect(self._refresh_ledger)
        row.addWidget(refresh_btn)
        resend_btn = QPushButton("复制选中的码")
        resend_btn.clicked.connect(self._on_copy_selected)
        row.addWidget(resend_btn)
        row.addStretch()
        vbox.addLayout(row)

        self._ledger_hint = QLabel(f"台账：{DEFAULT_LEDGER_PATH}")
        self._ledger_hint.setWordWrap(True)
        self._ledger_hint.setStyleSheet(_MUTED)
        vbox.addWidget(self._ledger_hint)

        note = QLabel("用户把码弄丢时从这里复制重发，不要重签——重签会多一个编号，"
                      "台账就对不上了。")
        note.setWordWrap(True)
        note.setStyleSheet(_MUTED)
        vbox.addWidget(note)
        return tab

    def _refresh_ledger(self):
        try:
            records = list_records()
        except Exception as exc:  # noqa: BLE001
            self._ledger_hint.setText(f"台账读取失败：{exc}")
            self._ledger_hint.setStyleSheet(_BAD)
            return
        self._ledger_records = records
        self._ledger_table.setRowCount(len(records))
        for row, rec in enumerate(records):
            expires = rec.expires.isoformat() if rec.expires else "永久"
            status = "已过期" if rec.expired else "有效"
            for col, text in enumerate((
                rec.code_id, rec.target, "、".join(rec.levels),
                expires, rec.issued_at, rec.note, status,
            )):
                self._ledger_table.setItem(row, col, QTableWidgetItem(text))
        self._ledger_table.resizeColumnsToContents()
        self._ledger_hint.setText(
            f"台账：{DEFAULT_LEDGER_PATH}　共 {len(records)} 条")
        self._ledger_hint.setStyleSheet(_MUTED)

    def _on_ledger_selected(self):
        pass  # 选中行本身不触发动作，避免误点就把剪贴板改掉

    def _on_copy_selected(self):
        rows = {i.row() for i in self._ledger_table.selectedIndexes()}
        if not rows:
            QMessageBox.information(self, "复制", "请先选中一行")
            return
        record = self._ledger_records[min(rows)]
        clipboard = QApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(record.code)
            QMessageBox.information(
                self, "复制", f"已复制 {record.code_id} 的激活码")

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
