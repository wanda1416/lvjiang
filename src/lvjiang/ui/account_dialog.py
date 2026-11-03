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
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
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
    """用户表 + 文档通道 + profile 账本的合并结果。

    界面按「通道 / 上行 / 下行 / 失败」四列展示，不再拼一段长文案：
    多通道拼句既读不出哪一项失败，也把提示、结果、错误混成一坨。
    """

    def __init__(self, documents, profile, users=None):
        self.documents = documents
        self.profile = profile
        self.users = users
        self.errors = [*documents.errors, *profile.errors,
                       *(users.errors if users else [])]

    @staticmethod
    def _count(value) -> int:
        return len(value) if isinstance(value, (list, tuple)) else int(value or 0)

    def rows(self) -> list[tuple[str, int, int, int]]:
        """各通道 ``(名称, 上行, 下行, 失败)``。"""
        rows = []
        if self.users is not None:
            rows.append((tr("名册"), self._count(self.users.pushed),
                         self._count(self.users.pulled), len(self.users.errors)))
        rows.append((tr("文档"), self._count(self.documents.pushed),
                     self._count(self.documents.pulled), len(self.documents.errors)))
        rows.append((tr("profile"), self._count(self.profile.pushed),
                     self._count(self.profile.pulled), len(self.profile.errors)))
        return rows

    def distinct_errors(self) -> list[str]:
        seen: list[str] = []
        for error in self.errors:
            text = str(error).strip()
            if text and text not in seen:
                seen.append(text)
        return seen


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

    def revoke_device_async(self, device_id: str) -> bool:
        """下线单台设备；成功后刷新 summary（设备列表随之更新）。"""
        def action():
            self.session.revoke_device(device_id)
            return self.session.summary()
        return self._start(action, "operation")

    def sync_async(self, allow_overwrite: bool = False) -> bool:
        """一轮完整同步（用户表 + 文档通道 + profile 账本）；同一时刻只跑一份。"""
        from ..core.sync.engine import sync_documents
        token = self.session.access_token
        if not token:
            return False
        self.sync_attempted = True

        def action():
            from ..core.profile.snapshot_sync import sync_all
            from ..core.sync.users_sync import sync_users
            users_report = sync_users(token)
            document_report = sync_documents(token)
            profile_report = sync_all(token, allow_overwrite=allow_overwrite)
            return _CombinedReport(document_report, profile_report, users_report)

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


def _tune_table(table: QTableWidget, *, fixed: dict[int, int], row_height: int,
                stretch_column: int | None = None) -> None:
    """统一表格外观：去竖表头、定宽列、紧凑行高。

    默认的 ResizeToContents 会随内容长度反复跳动（按钮列尤其难看）。
    给定宽后列宽稳定；``stretch_column`` 为 None 时表格宽度收成各列之和，
    不会把短内容列拉成一大片空白。
    """
    side = table.verticalHeader()
    assert side is not None                   # Qt 的桩把访问器标成可能为 None
    side.setVisible(False)
    side.setDefaultSectionSize(row_height)
    table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
    table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
    table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    table.setShowGrid(False)
    table.setAlternatingRowColors(False)
    table.setWordWrap(False)
    table.setTextElideMode(Qt.TextElideMode.ElideRight)
    table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

    header = table.horizontalHeader()
    assert header is not None                 # Qt 的桩把访问器标成可能为 None
    header.setStretchLastSection(False)
    header.setHighlightSections(False)
    header.setMinimumSectionSize(48)
    for column, width in fixed.items():
        header.setSectionResizeMode(column, QHeaderView.ResizeMode.Fixed)
        table.setColumnWidth(column, width)
    if stretch_column is not None:
        header.setSectionResizeMode(stretch_column, QHeaderView.ResizeMode.Stretch)
    else:
        table.setFixedWidth(sum(fixed.values()) + 2 * len(fixed) + 2)


class AccountManagerDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.holder = account_holder()
        self.setWindowTitle(tr("账户管理"))
        self.setMinimumSize(760, 560)
        self.resize(760, 560)
        self._connections: list[tuple[object, object]] = []
        layout = QVBoxLayout(self)

        # ════ 第一行：左侧账户身份 + 右侧「修改密码 / 退出登录」 ════
        head_row = QHBoxLayout()
        self.account_label = QLabel()
        self.account_label.setStyleSheet("font-size: 15px; font-weight: 600;")
        head_row.addWidget(self.account_label, stretch=1)
        self.change_password_button = QPushButton(tr("修改密码…"))
        apply_button_style(self.change_password_button, variant="neutral")
        self.change_password_button.clicked.connect(self._change_password)
        self.logout_button = QPushButton(tr("退出登录"))
        apply_button_style(self.logout_button, variant="neutral")
        self.logout_button.clicked.connect(self._logout)
        head_row.addWidget(self.change_password_button)
        head_row.addWidget(self.logout_button)
        layout.addLayout(head_row)

        # 一行临时反馈（正在登录…／登录失败）：空时不占位，不常驻任何状态数字。
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setStyleSheet("color: #c62828; font-size: 12px;")
        self.status.setVisible(False)
        layout.addWidget(self.status)

        # ════ 未登录：居中的紧凑登录卡（不摊满整个对话框） ════
        self.form_host = QWidget()
        outer = QVBoxLayout(self.form_host)
        outer.addStretch(2)
        card = QVBoxLayout()
        card.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        title = QLabel(tr("账户登录"))
        title.setStyleSheet("font-size: 17px; font-weight: 600;")
        title.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        card.addWidget(title)
        subtitle = QLabel(tr("登录后可同步角色名册、文档与总览数据"))
        subtitle.setStyleSheet("color: palette(mid); font-size: 12px;")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        card.addWidget(subtitle)
        card.addSpacing(12)

        form_body = QWidget()
        form_body.setMaximumWidth(340)
        form = QFormLayout(form_body)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.login_input = QLineEdit()
        self.login_input.setPlaceholderText(tr("账户名（由管理员创建）"))
        self.password_field = PasswordField()
        self.password_field.editor.setPlaceholderText(tr("密码"))
        self.password_field.editor.returnPressed.connect(self._login)
        form.addRow(tr("账户名"), self.login_input)
        form.addRow(tr("密码"), self.password_field.widget())
        self.login_button = QPushButton(tr("登录"))
        apply_button_style(self.login_button)
        self.login_button.setMinimumWidth(120)
        self.login_button.clicked.connect(self._login)
        button_row = QHBoxLayout()
        button_row.addStretch()
        button_row.addWidget(self.login_button)
        button_row.addStretch()
        form.addRow(button_row)
        card.addWidget(form_body)
        outer.addLayout(card)
        outer.addStretch(3)
        layout.addWidget(self.form_host, stretch=1)

        # ════ 已登录：两个页签（数据同步 / 设备管理） ════
        self.tabs = QTabWidget()
        self.tabs.setVisible(False)
        layout.addWidget(self.tabs, stretch=1)

        # ── Tab 1：数据同步（更常用，放第一） ──
        # 结构与设备管理一致：表格常驻并撑满，操作按钮放底部一行。
        # 三个通道行固定存在（同步前显示 —），不做「点同步表格消失」这种跳动。
        sync_tab = QWidget()
        sync_layout = QVBoxLayout(sync_tab)

        self.sync_table = QTableWidget(3, 4)
        self.sync_table.setHorizontalHeaderLabels(
            [tr("通道"), tr("上行"), tr("下行"), tr("失败")])
        _tune_table(self.sync_table, fixed={1: 96, 2: 96, 3: 96},
                    row_height=36, stretch_column=0)
        for row, name in enumerate((tr("名册"), tr("文档"), tr("profile"))):
            item = QTableWidgetItem(name)
            self.sync_table.setItem(row, 0, item)
            for column in (1, 2, 3):
                cell = QTableWidgetItem("—")
                cell.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.sync_table.setItem(row, column, cell)
        sync_layout.addWidget(self.sync_table, stretch=1)

        # 错误行：只在真失败时出现，其余时候不占位。
        self.sync_error = QLabel()
        self.sync_error.setWordWrap(True)
        self.sync_error.setStyleSheet("color: #c62828; font-size: 12px;")
        self.sync_error.setVisible(False)
        sync_layout.addWidget(self.sync_error)

        # 临时状态（正在同步…）：空文本不占位。
        self.sync_caption = QLabel()
        self.sync_caption.setStyleSheet("color: palette(mid); font-size: 12px;")
        self.sync_caption.setVisible(False)
        sync_layout.addWidget(self.sync_caption)

        sync_actions = QHBoxLayout()
        self.sync_button = QPushButton(tr("立即同步"))
        apply_button_style(self.sync_button)
        self.sync_button.clicked.connect(self._sync_now)
        sync_actions.addWidget(self.sync_button)
        self.snapshot_button = QPushButton(tr("刷新快照"))
        apply_button_style(self.snapshot_button, variant="neutral")
        self.snapshot_button.setToolTip(tr(
            "把本机当前全部角色数据上传为云端基线。\n"
            "新设备接入、或云端被重建后需要先做一次；日常同步不用点。"))
        self.snapshot_button.clicked.connect(self._refresh_snapshot)
        sync_actions.addWidget(self.snapshot_button)
        self.clear_sync_button = QPushButton(tr("清除同步"))
        apply_button_style(self.clear_sync_button, variant="neutral")
        self.clear_sync_button.setToolTip(
            tr("清除本地同步进度（远端水位/本机水位/快照编号），不动本地数据；"
               "换账号或云端重建后使用"))
        self.clear_sync_button.clicked.connect(self._clear_sync)
        sync_actions.addWidget(self.clear_sync_button)
        sync_actions.addStretch()
        sync_layout.addLayout(sync_actions)
        self.tabs.addTab(sync_tab, tr("数据同步"))

        # ── Tab 2：设备管理（列表 + 逐行登出 + 底部退出所有设备） ──
        device_tab = QWidget()
        device_layout = QVBoxLayout(device_tab)
        self.devices = QTableWidget(0, 4)
        self.devices.setHorizontalHeaderLabels(
            [tr("设备"), tr("平台"), tr("最近活跃"), tr("操作")])
        _tune_table(self.devices, fixed={1: 84, 2: 168, 3: 96},
                    row_height=40, stretch_column=0)
        device_layout.addWidget(self.devices, stretch=1)

        device_actions = QHBoxLayout()
        self.logout_all_button = QPushButton(tr("退出所有设备"))
        apply_button_style(self.logout_all_button, variant="neutral")
        self.logout_all_button.clicked.connect(self._logout_all)
        device_actions.addWidget(self.logout_all_button)
        device_actions.addStretch()
        device_layout.addLayout(device_actions)
        self.tabs.addTab(device_tab, tr("设备管理"))

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

    def _set_status(self, text: str) -> None:
        """一行临时反馈（正在登录…／失败原因）：空文本时整行不占位。"""
        self.status.setText(text)
        self.status.setVisible(bool(text))

    def _on_operation_failed(self, ok: bool, result) -> None:
        if ok or not self._alive():
            return
        self._render(self.holder.summary)
        self._set_status(str(result))

    def _render(self, summary: dict | None) -> None:
        self._set_busy(self.holder.busy)
        if summary is None:
            self.account_label.setText(tr("未登录"))
        else:
            self._show_logged_in(summary)
        self.change_password_button.setVisible(summary is not None)
        self.logout_button.setVisible(summary is not None)
        self.tabs.setVisible(summary is not None)
        self.form_host.setVisible(summary is None)

    def _show_logged_in(self, summary: dict) -> None:
        account = summary["account"]
        display = account.get("display_name") or ""
        self.account_label.setText(account["login"] + (f"（{display}）" if display else ""))
        self._render_devices(summary)

    def _render_devices(self, summary: dict) -> None:
        """设备列表；每行末尾一个「登出」按钮，允许单独下线某台设备。"""
        devices = summary.get("devices") or []
        mine = summary.get("instance_id") or ""
        self.devices.setRowCount(len(devices))
        for row, device in enumerate(devices):
            name = device.get("name") or device["id"][:8]
            if device.get("id") == mine:
                name = f"{name}（本机）"
            values = (name, device.get("platform") or "—", device.get("last_seen") or "—")
            for column, text in enumerate(values):
                item = QTableWidgetItem(str(text))
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                # 除设备名外都居中，数字/短文本列看起来才齐。
                if column:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.devices.setItem(row, column, item)

            host = QWidget()
            cell = QHBoxLayout(host)
            cell.setContentsMargins(6, 0, 6, 0)
            kick = QPushButton(tr("登出"))
            apply_button_style(kick, variant="neutral")
            kick.setFixedWidth(64)          # 固定宽度：不让按钮把列撑变形
            device_id = device.get("id")
            kick.clicked.connect(
                lambda _=False, did=device_id: self._revoke_device(did))
            cell.addWidget(kick)
            self.devices.setCellWidget(row, 3, host)

    def _set_sync_note(self, text: str, *, error: bool = False) -> None:
        """同步页的临时反馈（正在同步…／失败原因）：空文本时两行都不占位。"""
        if error:
            self.sync_error.setText(text)
            self.sync_error.setVisible(True)
        self.sync_caption.setText(text if not error else "")
        self.sync_caption.setVisible(bool(text) and not error)

    def _render_sync(self) -> None:
        report = self.holder.last_sync_report
        if self.holder.syncing:
            self._set_sync_note(tr("正在同步…"))
            self.sync_error.setVisible(False)
        elif report is not None:
            self._set_sync_note("")               # 结果都在表格里，不留统计行
            self._render_report(report)
        else:
            self._set_sync_note("")
            self._fill_sync_table(None)
            self.sync_error.setVisible(False)
        running = self.holder.syncing or self.holder.busy
        self.sync_button.setEnabled(not running)
        self.clear_sync_button.setEnabled(not running)
        self.snapshot_button.setEnabled(not running and self.holder.summary is not None)

    def _fill_sync_table(self, rows: list[tuple[str, int | None, int | None, int | None]] | None) -> None:
        """三个通道行固定存在；没有结果时数值列显示 —，表格不消失。"""
        if not rows:
            rows = [(name, None, None, None)
                    for name in (tr("名册"), tr("文档"), tr("profile"))]
        for index, (name, pushed, pulled, failed) in enumerate(rows):
            def cell_text(value, failed_count=False):
                if value is None:
                    return "—"
                if failed_count and not value:
                    return "—"
                return str(value)

            for column, value in enumerate((
                    name, cell_text(pushed), cell_text(pulled),
                    cell_text(failed, failed_count=True))):
                item = QTableWidgetItem(value)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                if column:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.sync_table.setItem(index, column, item)

    def _render_report(self, report) -> None:
        self._fill_sync_table(report.rows())
        errors = report.distinct_errors()
        if errors:
            shown = errors[:2]
            text = "；".join(shown)
            if len(errors) > len(shown):
                text += tr("；等 {} 项").format(len(errors))
            self.sync_error.setText(text)
            self.sync_error.setVisible(True)
        else:
            self.sync_error.setVisible(False)

    def _set_busy(self, busy: bool) -> None:
        for widget in (self.login_button, self.change_password_button,
                       self.logout_button, self.logout_all_button):
            widget.setEnabled(not busy)

    # ─── 动作 ───────────────────────────────────────────

    def _login(self) -> None:
        login_name = self.login_input.text().strip()
        password = self.password_field.editor.text()
        if not login_name:
            self._set_status(tr("请输入账户名"))
            return
        if not password:
            self._set_status(tr("请输入密码"))
            return
        self._set_status(tr("正在登录…"))
        self.password_field.editor.clear()
        self.holder.login_async(login_name, password)

    def _logout(self) -> None:
        self._set_status(tr("正在退出…"))
        self.holder.logout_async()

    def _revoke_device(self, device_id: str) -> None:
        """单独下线某台设备（会话鉴权，服务端只允许踢自己账号下的设备）。"""
        if not device_id:
            return
        mine = (self.holder.summary or {}).get("instance_id") or ""
        note = (tr("这是本机：下线后需要重新登录。") if device_id == mine else "")
        if QMessageBox.question(
                self, tr("设备登出"),
                tr("确定让该设备下线？该设备上的全部会话会立即失效。") + note,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        self.holder.revoke_device_async(device_id)

    def _logout_all(self) -> None:
        self._set_status(tr("正在退出所有设备…"))
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
            self._set_status(tr("新密码至少 8 位"))
            return
        self._set_status(tr("正在修改密码…"))
        self.holder.change_password_async(old_password, new_password)

    def _sync_now(self) -> None:
        if self.holder.sync_async():
            self._render_sync()

    def _clear_sync(self) -> None:
        """用户显式清除本地同步进度。同步发现进度异常时也引导到这里，不自动改。"""
        from ..core.profile.snapshot_sync import clear_sync_state

        answer = QMessageBox.question(
            self, tr("清除同步"),
            tr("清除本地同步进度后，下次同步会重新从云端快照开始。本地数据不会被删除。"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        clear_sync_state()
        self._set_sync_note(tr("已清除本地同步进度。点「立即同步」重新建立。"))

    def _refresh_snapshot(self) -> None:
        """把本机当前 profile 值作为新快照上传，绑到最新远端 history id。

        新设备此后从这里取基线回放；已有设备各自按自己的水位继续，不受影响。
        """
        from ..core.profile.snapshot_sync import refresh_snapshot
        if self.holder.syncing or self.holder.busy:
            return
        self._set_sync_note(tr("正在刷新快照…"))
        self.holder._start(lambda: refresh_snapshot(self.holder.session.access_token), "sync")

    def _on_sync_finished(self, ok: bool, result) -> None:
        if not self._alive():
            return
        self._render_sync()
        if not ok:
            self._set_sync_note(str(result), error=True)
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
            self._set_sync_note(
                tr("已取消覆盖：本机数据未改动，profile 同步未启用。"))
            return
        self._set_sync_note(tr("正在备份并应用云端快照…"))
        self.holder.sync_async(allow_overwrite=True)
