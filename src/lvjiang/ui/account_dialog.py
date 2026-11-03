"""账户管理对话框 —— 登录、注销、改密码、账号信息与数据同步。

**线程归属**：所有网络操作（登录/恢复/改密/同步）的 QThread 都由应用级
``AccountHolder`` 持有，对话框只订阅它的信号。对话框是临时对象，绝不能作为
线程的父对象——否则关闭对话框会把仍在运行的线程一起销毁，Qt 直接 abort；
也不能在关闭路径里 ``wait()`` 阻塞 UI 线程。关掉对话框时同步继续在后台跑完，
结果留在 ``holder`` 里，重新打开即可看到。
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
    QMessageBox,
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
    """一次性后台动作：**不设父对象**，由持有者保引用，结束后自删。

    刻意不接受 parent：QObject 父对象被销毁时会连带销毁子 QThread，若线程
    仍在运行，Qt 会以 "QThread: Destroyed while thread is still running"
    直接 abort 进程。生命周期一律由 ``AccountHolder`` 管理。
    """

    completed = pyqtSignal(bool, object)   # (成功与否, 结果或 AccountError)

    def __init__(self, action):
        super().__init__()
        self._action = action

    def run(self):
        try:
            self.completed.emit(True, self._action())
        except AccountError as error:
            self.completed.emit(False, error)
        except Exception as error:   # 凭据库异常等非协议错误也要落到界面
            self.completed.emit(False, AccountError(f"{tr('操作未完成')}：{error}"))


class _CombinedReport:
    """文档通道 + profile 账本的合并结果，界面只关心总量与错误。"""

    def __init__(self, documents, profile):
        self.documents = documents
        self.profile = profile
        self.errors = [*documents.errors, *profile.errors]

    def describe(self) -> str:
        parts = [f"文档 {self.documents.describe()}", f"profile {self.profile.describe()}"]
        if self.errors:
            parts.append(f"失败 {len(self.errors)}")
        return "；".join(parts)


class AccountHolder(QObject):
    """应用级登录态与后台任务持有者：共享一个 AccountSession，缓存状态。

    主窗口启动时 restore_async() 后台恢复并预取；对话框直接渲染缓存状态。
    所有线程归它所有，因此对话框开合不影响在跑的任务。
    """

    changed = pyqtSignal(object)              # summary dict 或 None
    operation_finished = pyqtSignal(bool, object)   # 登录/登出/改密
    sync_finished = pyqtSignal(bool, object)        # 数据同步

    def __init__(self):
        super().__init__()
        self.session = AccountSession()
        self.summary: dict | None = None
        self.done = False
        self.sync_attempted = False
        self.last_sync_report = None
        self._worker: _AccountWorker | None = None
        self._sync_worker: _AccountWorker | None = None

    # ─── 状态 ───────────────────────────────────────────

    @property
    def busy(self) -> bool:
        return self._worker is not None

    @property
    def syncing(self) -> bool:
        return self._sync_worker is not None

    def apply(self, summary: dict | None) -> None:
        self.done = True
        self.summary = summary
        self.changed.emit(summary)

    # ─── 后台任务 ───────────────────────────────────────

    def _start(self, action, kind: str) -> bool:
        """启动一个后台动作；同类任务已在跑时拒绝并返回 False。"""
        attr = "_worker" if kind == "operation" else "_sync_worker"
        if getattr(self, attr) is not None:
            return False
        worker = _AccountWorker(action)
        worker.completed.connect(
            lambda ok, result, w=worker: self._finished(kind, w, ok, result))
        worker.finished.connect(worker.deleteLater)
        setattr(self, attr, worker)
        worker.start()
        return True

    def _finished(self, kind: str, worker, ok: bool, result) -> None:
        attr = "_worker" if kind == "operation" else "_sync_worker"
        if getattr(self, attr) is worker:
            setattr(self, attr, None)
        if kind == "sync":
            if ok:
                self.last_sync_report = result
                describe = getattr(result, "describe", None)
                logger.info("[account] 同步完成：{}", describe() if describe else result)
            else:
                logger.warning("[account] 同步未完成：{}", result)
            self.sync_finished.emit(ok, result)
            return
        if ok:
            if result is None:
                self.apply(None)
            else:
                self.apply(result)
                if not self.sync_attempted:
                    self.sync_async()          # 登录成功后自动同步一次
        else:
            self.operation_finished.emit(False, result)

    # ─── 对外入口 ───────────────────────────────────────

    def restore_async(self) -> None:
        """启动时后台恢复登录态；只在第一次调用时真正执行。"""
        if self.done or self.busy:
            return
        if not self.session.has_stored_session():
            self.apply(None)                   # 凭据库无登录态：不联网
            return
        self._start(self.session.restore, "operation")

    def login_async(self, login_name: str, password: str) -> bool:
        return self._start(lambda: self.session.login(login_name, password), "operation")

    def logout_async(self) -> bool:
        return self._start(self.session.logout, "operation")

    def logout_all_async(self) -> bool:
        return self._start(self.session.logout_all, "operation")

    def change_password_async(self, old_password: str, new_password: str) -> bool:
        def action():
            self.session.change_password(old_password, new_password)
            return self.session.summary()
        return self._start(action, "operation")

    def sync_async(self, allow_overwrite: bool = False) -> bool:
        """一轮完整同步（文档通道 + profile 账本）；同一时刻只跑一份。"""
        from ..core.sync.engine import sync_documents
        token = self.session.access_token
        if not token:
            return False
        self.sync_attempted = True

        def action():
            from ..core.profile.snapshot_sync import sync_all
            document_report = sync_documents(token)
            profile_report = sync_all(token, allow_overwrite=allow_overwrite)
            return _CombinedReport(document_report, profile_report)

        return self._start(action, "sync")


_holder: AccountHolder | None = None


def account_holder() -> AccountHolder:
    global _holder
    if _holder is None:
        _holder = AccountHolder()
    return _holder


def ensure_account_restored() -> None:
    """主窗口启动时调用：后台恢复登录态并预取数据。"""
    account_holder().restore_async()


class AccountManagerDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.holder = account_holder()
        self.setWindowTitle(tr("账户管理"))
        self.setMinimumWidth(460)
        self._connections: list[tuple[object, object]] = []
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

        # ── 数据同步 ──
        sync_row = QHBoxLayout()
        self.sync_button = QPushButton(tr("立即同步"))
        apply_button_style(self.sync_button, variant="neutral")
        self.sync_button.clicked.connect(self._sync_now)
        self.snapshot_button = QPushButton(tr("刷新快照"))
        apply_button_style(self.snapshot_button, variant="neutral")
        self.snapshot_button.clicked.connect(self._refresh_snapshot)
        sync_row.addWidget(self.sync_button)
        sync_row.addWidget(self.snapshot_button)
        sync_row.addStretch()
        layout.addLayout(sync_row)
        self.sync_status = QLabel()
        self.sync_status.setWordWrap(True)
        layout.addWidget(self.sync_status)

        self._connect(self.holder.changed, self._on_holder_changed)
        self._connect(self.holder.operation_finished, self._on_operation_failed)
        self._connect(self.holder.sync_finished, self._on_sync_finished)

        self._render(self.holder.summary)
        self._render_sync()

    # ─── 信号连接（关闭时统一断开） ─────────────────────

    def _connect(self, signal, slot) -> None:
        signal.connect(slot)
        self._connections.append((signal, slot))

    def done(self, result: int) -> None:
        # 只断开订阅，**不等待线程**：后台任务由 holder 拥有，对话框先走即可。
        for signal, slot in self._connections:
            try:
                signal.disconnect(slot)
            except (TypeError, RuntimeError):
                pass
        self._connections.clear()
        super().done(result)

    # ─── 渲染 ───────────────────────────────────────────

    def _alive(self) -> bool:
        """槽函数可能在对话框销毁后才收到已排队的信号。"""
        try:
            return self.isVisible() or self.parent() is not None
        except RuntimeError:
            return False

    def _on_holder_changed(self, summary) -> None:
        if not self._alive():
            return
        self._render(summary)

    def _on_operation_failed(self, ok: bool, result) -> None:
        if ok or not self._alive():
            return
        if self.holder.summary is not None:
            self._render(self.holder.summary)
            self.status.setText(str(result))
        else:
            self._render(None)
            self.status.setText(str(result))

    def _render(self, summary: dict | None) -> None:
        self._set_busy(self.holder.busy)
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

    def _render_sync(self) -> None:
        report = self.holder.last_sync_report
        if self.holder.syncing:
            self.sync_status.setText(tr("正在同步文档…"))
        elif report is not None:
            parts = [tr("上次同步") + "：" + report.describe()]
            if report.errors:
                parts.append("；".join(report.errors[:3]))
            self.sync_status.setText("".join(parts))
        else:
            self.sync_status.setText(
                tr("尚未同步。登录后自动同步一次；换设备前建议先「刷新快照」。"))
        running = self.holder.syncing or self.holder.busy
        self.sync_button.setEnabled(not running)
        self.snapshot_button.setEnabled(not running and self.holder.summary is not None)

    def _set_busy(self, busy: bool) -> None:
        for widget in (self.login_button, self.change_password_button,
                       self.logout_button, self.logout_all_button):
            widget.setEnabled(not busy)

    # ─── 动作 ───────────────────────────────────────────

    def _login(self) -> None:
        login_name = self.login_input.text().strip()
        password = self.password_field.editor.text()
        if not login_name:
            self.status.setText(tr("请输入账户名"))
            return
        if not password:
            self.status.setText(tr("请输入密码"))
            return
        self.status.setText(tr("正在登录…"))
        self.password_field.editor.clear()
        self.holder.login_async(login_name, password)

    def _logout(self) -> None:
        self.status.setText(tr("正在退出…"))
        self.holder.logout_async()

    def _logout_all(self) -> None:
        self.status.setText(tr("正在退出所有设备…"))
        self.holder.logout_all_async()

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
        self.status.setText(tr("正在修改密码…"))
        self.holder.change_password_async(old_password, new_password)

    def _sync_now(self) -> None:
        if self.holder.sync_async():
            self._render_sync()

    def _refresh_snapshot(self) -> None:
        """把本机当前 profile 值作为新快照上传，绑到最新远端 history id。

        新设备此后从这里取基线回放；已有设备各自按自己的水位继续，不受影响。
        """
        from ..core.profile.snapshot_sync import refresh_snapshot
        if self.holder.syncing or self.holder.busy:
            return
        self.sync_status.setText(tr("正在刷新快照…"))
        self.holder._start(lambda: refresh_snapshot(self.holder.session.access_token), "sync")

    def _on_sync_finished(self, ok: bool, result) -> None:
        if not self._alive():
            return
        self._render_sync()
        if not ok:
            self.sync_status.setText(str(result))
            return
        pending = getattr(getattr(result, "profile", result), "pending", None) or []
        if pending:
            self._confirm_overwrite(pending)

    def _confirm_overwrite(self, pending: list[dict]) -> None:
        """非初始设备：必须显式确认才会覆盖本地数据（覆盖前自动整份备份）。"""
        lines = [
            f"{row['username']}　本地 profile {row['entries']} 条 / 历史 {row['history']} 条"
            f"　（云端来自 {row['origin_name']}）"
            for row in pending
        ]
        detail = "\n".join(lines)
        answer = QMessageBox.question(
            self, tr("当前设备不是初始同步设备"),
            tr("下列用户的数据将以云端快照为准，本机数据会被覆盖：") + "\n\n"
            + detail + "\n\n"
            + tr("确认后会先把本机 profile 数据备份到 config/session/_sync-backup-<时间戳>/，"
                 "再应用云端快照；可在该目录恢复。"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            self.sync_status.setText(
                tr("已取消覆盖：本机数据未改动，profile 同步未启用。"))
            return
        self.sync_status.setText(tr("正在备份并应用云端快照…"))
        self.holder.sync_async(allow_overwrite=True)
