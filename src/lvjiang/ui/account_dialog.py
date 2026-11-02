"""账户管理对话框 —— 登录、注销、改密码与账号信息查看。

登录态是应用级的：主窗口启动时就后台恢复会话（凭据库里的 refresh 令牌），
打开本对话框直接呈现已登录视图，不闪登录框。网络操作走一次性 QThread。
数据同步及其状态属下一阶段，此处不出现同步入口。
"""
from __future__ import annotations

from loguru import logger
from PyQt6.QtCore import QObject, Qt, QThread, pyqtSignal
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
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..core.account import AccountError, AccountSession
from ..i18n import tr
from .button_styles import apply_button_style


class PasswordField:
    """密码输入框 + 「显示」切换按钮；widget() 返回可直接进表单的容器。"""

    def __init__(self, parent=None):
        self.editor = QLineEdit()
        self.editor.setEchoMode(QLineEdit.EchoMode.Password)
        self.toggle = QToolButton(self.editor)
        self.toggle.setText(tr("显示"))
        self.toggle.setCheckable(True)
        self.toggle.setAutoRaise(True)
        self.toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.toggle.setFixedWidth(40)
        self.toggle.toggled.connect(self._toggle_echo)
        self.host = QWidget(parent)
        row = QHBoxLayout(self.host)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)
        row.addWidget(self.editor, stretch=1)
        row.addWidget(self.toggle)

    def _toggle_echo(self, visible: bool) -> None:
        self.editor.setEchoMode(
            QLineEdit.EchoMode.Normal if visible else QLineEdit.EchoMode.Password)
        self.toggle.setText(tr("隐藏") if visible else tr("显示"))

    def widget(self) -> QWidget:
        return self.host


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


class AccountHolder(QObject):
    """应用级登录态：共享一个 AccountSession，缓存最近一次 summary。

    主窗口启动时 restore_async() 后台恢复；此后任何打开的对话框都直接
    渲染缓存状态，不再让用户面对「正在检查登录状态」的空等。
    """

    changed = pyqtSignal(object)   # summary dict 或 None

    def __init__(self):
        super().__init__()
        self.session = AccountSession()
        self.summary: dict | None = None
        self.done = False
        self.worker: _AccountWorker | None = None
        self.sync_attempted = False
        self.last_sync_report = None
        self.sync_worker: _AccountWorker | None = None

    @property
    def busy(self) -> bool:
        return self.worker is not None

    def restore_async(self) -> None:
        """启动时后台恢复登录态；只在第一次调用时真正执行。"""
        if self.done or self.busy:
            return
        if not self.session.has_stored_session():
            # 凭据库里没有登录态：不联网，直接落「未登录」
            self.apply(None)
            return
        self.worker = _AccountWorker(self.session.restore, self)
        self.worker.completed.connect(self._restore_finished)
        self.worker.start()

    def _restore_finished(self, ok: bool, result) -> None:
        self.worker = None
        self.apply(result if ok else None)
        if result is not None:
            self.sync_in_background()

    def sync_in_background(self) -> None:
        """启动/登录后的静默自动同步：日志记账，结果供对话框展示。"""
        from ..core.sync.engine import sync_documents
        token = self.session.access_token
        if not token or self.sync_worker is not None:
            return
        self.sync_attempted = True
        self.sync_worker = _AccountWorker(lambda: sync_documents(token), self)
        self.sync_worker.completed.connect(self._sync_done)
        self.sync_worker.start()

    def _sync_done(self, ok: bool, result) -> None:
        worker, self.sync_worker = self.sync_worker, None
        if worker is not None:
            worker.deleteLater()
        if ok:
            self.last_sync_report = result
            logger.info("[account] {}", result.describe())
        else:
            logger.warning("[account] 自动同步未完成：{}", result)

    def apply(self, summary: dict | None) -> None:
        """写入最新状态（登录/登出/刷新后的 summary）并广播。"""
        self.done = True
        self.summary = summary
        self.changed.emit(summary)


_holder: AccountHolder | None = None


def account_holder() -> AccountHolder:
    global _holder
    if _holder is None:
        _holder = AccountHolder()
    return _holder


def ensure_account_restored() -> None:
    """主窗口启动时调用：后台恢复登录态并预取账号数据。"""
    account_holder().restore_async()


class AccountManagerDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.holder = account_holder()
        self.worker = None
        self.setWindowTitle(tr("账户管理"))
        self.setMinimumWidth(460)
        layout = QVBoxLayout(self)

        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        # ── 登录表单 ──
        self.form_host = QWidget()
        form = QFormLayout(self.form_host)
        self.login_input = QLineEdit()
        self.login_input.setPlaceholderText(tr("账户名（由管理员创建）"))
        self.password_field = PasswordField()
        self.password_field.editor.setPlaceholderText(tr("密码"))
        self.password_field.editor.returnPressed.connect(self._login)
        form.addRow(tr("账户名"), self.login_input)
        form.addRow(tr("密码"), self.password_field.widget())
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

        # ── 数据同步（文档通道）──
        sync_row = QHBoxLayout()
        self.sync_button = QPushButton(tr("立即同步"))
        apply_button_style(self.sync_button, variant="neutral")
        self.sync_button.clicked.connect(self._sync_now)
        sync_row.addWidget(self.sync_button)
        sync_row.addStretch()
        layout.addLayout(sync_row)
        self.sync_status = QLabel(tr("尚未同步。登录后自动同步一次；换页面前手动同步可立即互通。"))
        self.sync_status.setWordWrap(True)
        layout.addWidget(self.sync_status)

        # 启动恢复若已完成，立即按缓存渲染；否则跟随恢复结果刷新。
        self.holder.changed.connect(self._on_holder_changed)
        if self.holder.done:
            self._render(self.holder.summary)
        elif self.holder.busy:
            self.status.setText(tr("正在检查登录状态…"))

    def done(self, result: int) -> None:
        # 关闭时若网络操作还在跑，等它退出，避免 QThread 随对话框销毁。
        if self.worker is not None:
            self.worker.wait(5000)
        self.holder.changed.disconnect(self._on_holder_changed)
        super().done(result)

    # ─── 状态渲染 ───────────────────────────────────────

    def _on_holder_changed(self, summary) -> None:
        self._render(summary)

    def _render(self, summary: dict | None) -> None:
        self._set_busy(False)
        if summary is None:
            self.status.setText(tr("当前未登录"))
            self.info_host.setVisible(False)
            self.form_host.setVisible(True)
        else:
            self._show_logged_in(summary)

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

    # ─── 后台执行 ───────────────────────────────────────

    def _run(self, action, status_text: str) -> None:
        if self.worker is not None or self.holder.busy:
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
            # 失败不打断已有登录态：已登录就留在信息页，只提示错误。
            if self.holder.summary is not None:
                self.status.setText(str(result))
                self._show_logged_in(self.holder.summary)
            else:
                self.status.setText(str(result))
                self._render(None)
            return
        self.holder.apply(result)
        if result is not None and not self.holder.sync_attempted:
            # 登录/启动恢复成功后自动同步一次（后台，不阻塞渲染）。
            self._sync_now(quiet=True)

    # ─── 动作 ───────────────────────────────────────────

    def _login(self) -> None:
        if self.worker is not None or self.holder.busy:
            return
        login_name = self.login_input.text().strip()
        password = self.password_field.editor.text()
        if not login_name:
            self.status.setText(tr("请输入账户名"))
            return
        if not password:
            self.status.setText(tr("请输入密码"))
            return
        self._run(lambda: self.holder.session.login(login_name, password), tr("正在登录…"))
        self.password_field.editor.clear()

    def _logout(self) -> None:
        self._run(self.holder.session.logout, tr("正在退出…"))

    def _logout_all(self) -> None:
        self._run(self.holder.session.logout_all, tr("正在退出所有设备…"))

    def _change_password(self) -> None:
        old_field = PasswordField(self)
        new_field = PasswordField(self)
        dialog = QDialog(self)
        dialog.setWindowTitle(tr("修改密码"))
        form = QFormLayout(dialog)
        form.addRow(tr("当前密码"), old_field.widget())
        form.addRow(tr("新密码（至少 8 位）"), new_field.widget())
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        old_password, new_password = old_field.editor.text(), new_field.editor.text()
        if len(new_password) < 8:
            self.status.setText(tr("新密码至少 8 位"))
            return

        def action():
            self.holder.session.change_password(old_password, new_password)
            return self.holder.session.summary()

        self._run(action, tr("正在修改密码…"))

    # ─── 数据同步 ───────────────────────────────────────

    def _sync_now(self, quiet: bool = False) -> None:
        from ..core.sync.engine import sync_documents
        token = self.holder.session.access_token
        # 启动自动同步还在跑时直接跳过：它结束会刷新同一份状态，重复跑白费流量。
        if not token or self.worker is not None or self.holder.sync_worker is not None:
            return
        self.holder.sync_attempted = True
        if not quiet:
            self._set_busy(True, tr("正在同步…"))
        self.sync_button.setEnabled(False)
        if not quiet:
            self.sync_status.setText(tr("正在同步文档…"))
        self.worker = _AccountWorker(lambda: sync_documents(token), self)
        self.worker.completed.connect(self._sync_finished)
        self.worker.finished.connect(self.worker.deleteLater)
        self.worker.start()

    def _sync_finished(self, ok: bool, result) -> None:
        self.worker = None
        self._set_busy(False)
        self.sync_button.setEnabled(True)
        if not ok:
            self.sync_status.setText(str(result))
            return
        self.holder.last_sync_report = result
        parts = [tr("上次同步") + "：", result.describe()]
        if result.errors:
            parts.append("；".join(result.errors[:3]))
        self.sync_status.setText("".join(parts))
