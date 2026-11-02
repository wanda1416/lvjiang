"""账户管理对话框 —— 登录、注销、改密码与账号信息查看。

数据同步及其状态属下一阶段，此处不出现同步入口；对话框只承载认证与
账号信息。网络操作走一次性 QThread，不阻塞界面。
"""
from __future__ import annotations

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..core.account import AccountError, AccountSession
from ..i18n import tr
from .button_styles import apply_button_style


class _AccountWorker(QThread):
    """一次性网络操作：动作在 run() 中执行，结果经信号回 UI 线程。"""

    completed = pyqtSignal(bool, object)   # (成功与否, 结果或 AccountError)

    def __init__(self, action, parent):
        super().__init__(parent)
        self._action = action

    def run(self):
        try:
            self.completed.emit(True, self._action())
        except AccountError as error:
            self.completed.emit(False, error)
        except Exception as error:   # 凭据库异常等非协议错误也要落到界面
            self.completed.emit(False, AccountError(f"{tr('操作未完成')}：{error}"))


class AccountManagerDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.session = AccountSession()
        self.worker = None
        self.setWindowTitle(tr("账户管理"))
        self.setMinimumWidth(460)
        layout = QVBoxLayout(self)

        self.status = QLabel(tr("正在检查登录状态…"))
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        # ── 登录表单 ──
        self.form_host = QWidget()
        form = QFormLayout(self.form_host)
        self.login_input = QLineEdit()
        self.login_input.setPlaceholderText(tr("账户名（由管理员创建）"))
        self.password_input = QLineEdit()
        self.password_input.setPlaceholderText(tr("密码"))
        self.password_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.password_input.returnPressed.connect(self._login)
        form.addRow(tr("账户名"), self.login_input)
        form.addRow(tr("密码"), self.password_input)
        self.login_button = QPushButton(tr("登录"))
        apply_button_style(self.login_button)
        self.login_button.clicked.connect(self._login)
        button_row = QHBoxLayout()
        button_row.addWidget(self.login_button)
        button_row.addStretch()
        form.addRow(button_row)
        layout.addWidget(self.form_host)

        # ── 已登录信息 ──
        self.info_host = QWidget()
        info = QVBoxLayout(self.info_host)
        self.account_label = QLabel()
        self.account_label.setWordWrap(True)
        info.addWidget(self.account_label)
        self.devices = QTableWidget(0, 3)
        self.devices.setHorizontalHeaderLabels([tr("设备"), tr("平台"), tr("最近活跃")])
        self.devices.horizontalHeader().setStretchLastSection(True)
        self.devices.verticalHeader().setVisible(False)
        self.devices.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.devices.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        info.addWidget(self.devices)
        actions = QHBoxLayout()
        self.change_password_button = QPushButton(tr("修改密码…"))
        apply_button_style(self.change_password_button, variant="neutral")
        self.change_password_button.clicked.connect(self._change_password)
        self.logout_button = QPushButton(tr("退出登录"))
        apply_button_style(self.logout_button, variant="neutral")
        self.logout_button.clicked.connect(self._logout)
        self.logout_all_button = QPushButton(tr("退出所有设备"))
        apply_button_style(self.logout_all_button, variant="neutral")
        self.logout_all_button.clicked.connect(self._logout_all)
        for button in (self.change_password_button, self.logout_button, self.logout_all_button):
            actions.addWidget(button)
        actions.addStretch()
        info.addLayout(actions)
        self.info_host.setVisible(False)
        layout.addWidget(self.info_host)

        note = QLabel(tr("数据同步将在后续版本开放；当前为账号认证与信息查看。"))
        note.setWordWrap(True)
        layout.addWidget(note)

        self._restore()

    def done(self, result: int) -> None:
        # 关闭时若网络操作还在跑，等它退出，避免 QThread 随对话框销毁。
        if self.worker is not None:
            self.worker.wait(5000)
        super().done(result)

    # ─── 后台执行 ───────────────────────────────────────

    def _run(self, action, status_text: str) -> None:
        if self.worker is not None:
            return
        self._set_busy(True, status_text)
        self.worker = _AccountWorker(action, self)
        self.worker.completed.connect(self._finished)
        self.worker.finished.connect(self.worker.deleteLater)
        self.worker.start()

    def _set_busy(self, busy: bool, status_text: str = "") -> None:
        for widget in (self.login_button, self.change_password_button,
                       self.logout_button, self.logout_all_button):
            widget.setEnabled(not busy)
        if status_text:
            self.status.setText(status_text)

    def _finished(self, ok: bool, result) -> None:
        self.worker = None
        self._set_busy(False)
        if not ok:
            self.status.setText(str(result))
            self._show_login_form()
        elif result is None:
            # restore 无本地登录态：安静地呈现登录表单
            self.status.setText(tr("当前未登录"))
            self._show_login_form()
        else:
            self._show_logged_in(result)

    # ─── 动作 ───────────────────────────────────────────

    def _login(self) -> None:
        if self.worker is not None:
            return
        login_name = self.login_input.text().strip()
        password = self.password_input.text()
        if not login_name:
            self.status.setText(tr("请输入账户名"))
            return
        if not password:
            self.status.setText(tr("请输入密码"))
            return
        self._run(lambda: self.session.login(login_name, password), tr("正在登录…"))
        self.password_input.clear()

    def _restore(self) -> None:
        self._run(self.session.restore, tr("正在检查登录状态…"))

    def _logout(self) -> None:
        self._run(self.session.logout, tr("正在退出…"))

    def _logout_all(self) -> None:
        self._run(self.session.logout_all, tr("正在退出所有设备…"))

    def _change_password(self) -> None:
        old_input = QLineEdit()
        old_input.setEchoMode(QLineEdit.EchoMode.Password)
        new_input = QLineEdit()
        new_input.setEchoMode(QLineEdit.EchoMode.Password)
        dialog = QDialog(self)
        dialog.setWindowTitle(tr("修改密码"))
        form = QFormLayout(dialog)
        form.addRow(tr("当前密码"), old_input)
        form.addRow(tr("新密码（至少 8 位）"), new_input)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        old_password, new_password = old_input.text(), new_input.text()
        if len(new_password) < 8:
            self.status.setText(tr("新密码至少 8 位"))
            return

        def action():
            self.session.change_password(old_password, new_password)
            return self.session.summary()

        self._run(action, tr("正在修改密码…"))

    # ─── 展示 ───────────────────────────────────────────

    def _show_login_form(self) -> None:
        self.info_host.setVisible(False)
        self.form_host.setVisible(True)

    def _show_logged_in(self, summary: dict) -> None:
        account = summary["account"]
        display = account.get("display_name") or "—"
        self.account_label.setText(
            f"{tr('账户名')}：{account['login']}　{tr('显示名')}：{display}")
        devices = summary.get("devices") or []
        self.devices.setRowCount(len(devices))
        for row, device in enumerate(devices):
            values = (device.get("name") or device["id"][:8],
                      device.get("platform") or "—", device.get("last_seen") or "—")
            for column, text in enumerate(values):
                item = QTableWidgetItem(str(text))
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.devices.setItem(row, column, item)
        active = summary.get("active_sessions")
        self.status.setText(f"{tr('已登录')}。{tr('活跃会话')}：{active}")
        self.form_host.setVisible(False)
        self.info_host.setVisible(True)
