"""账户管理对话框 —— 登录、注销、改密码、账号信息与数据同步。

**线程归属**：所有网络操作（登录/恢复/改密/同步）的 QThread 都由应用级
``AccountHolder`` 持有，对话框只订阅它的信号。对话框是临时对象，绝不能作为
线程的父对象——否则关闭对话框会把仍在运行的线程一起销毁，Qt 直接 abort；
也不能在关闭路径里 ``wait()`` 阻塞 UI 线程。关掉对话框时同步继续在后台跑完，
结果留在 ``holder`` 里，重新打开即可看到。
"""
from __future__ import annotations

import threading
from datetime import datetime

from loguru import logger
from PyQt6.QtCore import QObject, Qt, QThread, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import QColor, QDesktopServices, QIcon, QPainter, QPen, QPixmap
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

from ..core.account import ACCOUNT_SERVICE, AccountError, AccountSession
from ..i18n import tr
from .button_styles import apply_button_style


def _eye_icon(visible: bool) -> QIcon:
    """密码框的小眼睛图标：睁眼（可见）/ 闭眼（隐藏）。

    QStyle 没有内置眼睛图标，字体图标又依赖系统字体，直接画两枚位图最稳。
    """
    pixmap = QPixmap(20, 20)
    pixmap.fill(QColor(0, 0, 0, 0))
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(painter.pen())
    pen.setWidthF(1.4)
    painter.setPen(pen)
    if visible:
        # 睁眼：眼眶椭圆 + 瞳孔
        painter.drawEllipse(2, 5, 16, 10)
        painter.setBrush(painter.pen().brush())
        painter.drawEllipse(8, 8, 4, 4)
    else:
        # 闭眼：一条下弯弧线
        painter.drawArc(2, 6, 16, 10, 30 * 16, 120 * 16)
    painter.end()
    return QIcon(pixmap)


class PasswordField:
    """密码输入框 + 眼睛图标切换按钮；widget() 返回可直接进表单的容器。"""

    def __init__(self, parent=None):
        self.editor = QLineEdit()
        self.editor.setEchoMode(QLineEdit.EchoMode.Password)
        self.toggle = QToolButton(self.editor)
        self.toggle.setIcon(_eye_icon(False))
        self.toggle.setCheckable(True)
        self.toggle.setAutoRaise(True)
        self.toggle.setFixedWidth(28)
        self.toggle.setToolTip(tr("显示密码"))
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
        self.toggle.setIcon(_eye_icon(visible))
        self.toggle.setToolTip(tr("隐藏密码") if visible else tr("显示密码"))

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


def readonly_instance() -> bool:
    """只读实例不做任何账号操作（登录态与同步数据只归主实例）。"""
    from ..core.access import is_readonly

    return is_readonly()


READONLY_ACCOUNT_HINT = "只读实例不能管理账户与同步数据，请在主实例中操作"


class _CombinedReport:
    """用户表 + 文档通道 + 总览账本的合并结果。

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
        rows = [(tr("定义"), 0, 0, 0)]
        if self.users is not None:
            rows.append((tr("用户"), self._count(self.users.pushed),
                         self._count(self.users.pulled), len(self.users.errors)))
        rows.append((tr("文档"), self._count(self.documents.pushed),
                     self._count(self.documents.pulled), len(self.documents.errors)))
        rows.append((tr("总览"), self._count(self.profile.pushed),
                     self._count(self.profile.pulled), len(self.profile.errors)))
        return rows

    def distinct_errors(self) -> list[str]:
        seen: list[str] = []
        for error in self.errors:
            text = str(error).strip()
            if text and text not in seen:
                seen.append(text)
        return seen

    def describe(self) -> str:
        """日志用一行摘要：各通道 上行/下行/失败。"""
        parts = [f"{name} 上行{up} 下行{down}" + (f" 失败{fail}" if fail else "")
                 for name, up, down, fail in self.rows()]
        text = "，".join(parts)
        distinct = self.distinct_errors()
        if distinct:
            text += f"；失败 {len(distinct)} 项（{distinct[0]}…）"
        return text


class AccountHolder(QObject):
    """应用级登录态与后台任务持有者：共享一个 AccountSession，缓存状态。

    主窗口启动时 restore_async() 后台恢复并预取；对话框直接渲染缓存状态。
    所有线程归它所有，因此对话框开合不影响在跑的任务。
    """

    changed = pyqtSignal(object)              # summary dict 或 None
    operation_finished = pyqtSignal(bool, object)   # 登录/登出/改密
    sync_finished = pyqtSignal(bool, object)        # 数据同步
    sync_progress = pyqtSignal(dict)                # 分步进度

    def __init__(self):
        super().__init__()
        self.session = AccountSession()
        self.summary: dict | None = None
        self.done = False
        self.sync_attempted = False
        self.last_sync_report = None
        #: 最近一次同步**成功**的时刻——对话框跨开关期间保留，落「最近同步时间」
        self.last_sync_at: str = ""
        #: 最近一轮同步是否由分钟节拍自动触发——抑制覆盖确认弹窗与红字报错
        self.last_sync_auto = False
        self._worker: _AccountWorker | None = None
        self._sync_worker: _AccountWorker | None = None
        self._beat_worker: _AccountWorker | None = None
        self._sync_cancel = threading.Event()
        # 登录期间两条独立节拍：每分钟自动同步一轮上推新数据；每 10 分钟
        # 心跳一次刷新设备在线（在线窗口 20 分钟，容错两跳，避免无谓消耗）。
        self._sync_timer = QTimer(self)
        self._sync_timer.setInterval(60_000)
        self._sync_timer.timeout.connect(lambda: self.sync_async(auto=True))
        self._heartbeat_timer = QTimer(self)
        self._heartbeat_timer.setInterval(600_000)
        self._heartbeat_timer.timeout.connect(self._on_heartbeat)

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
        if readonly_instance():
            summary = None              # 只读实例永不启动节拍
        for timer in (self._sync_timer, self._heartbeat_timer):
            if summary:
                timer.start()
            else:
                timer.stop()
        self.changed.emit(summary)

    # ─── 心跳 ─────────────────────────────────────────

    def _on_heartbeat(self) -> None:
        """每 10 分钟一跳：仅刷新设备活跃与探测会话健康（同步由独立分钟节拍负责）。"""
        if readonly_instance() or self.summary is None or self._beat_worker is not None:
            return

        def beat_done(ok: bool, result, *, _worker: _AccountWorker | None = None) -> None:
            # completed 信号只带 (ok, result)；worker 经闭包比对（信号重载
            # 无法直接传第三个参数，此前按三参声明直接 TypeError 崩溃）
            if _worker is not None and self._beat_worker is _worker:
                self._beat_worker = None
            if not ok and isinstance(result, AccountError) \
                    and result.code == "token_expired":
                self.apply(None)              # 登录态已死，停止节拍并回登录页

        worker = _AccountWorker(self.session.heartbeat)
        def on_completed(ok: bool, result) -> None:
            beat_done(ok, result, _worker=worker)
            self._beat_worker = None          # 跳完即清，允许下一跳
        worker.completed.connect(on_completed)
        worker.finished.connect(worker.deleteLater)
        self._beat_worker = worker
        worker.start()

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
                self.last_sync_at = datetime.now().strftime("%H:%M:%S")
                describe = getattr(result, "describe", None)
                text = describe() if describe else str(result)
                # 分钟节拍下零变更是常态（全 0 无失败）：INFO 一天刷几百行
                # 噪音，降 debug；有实际动作才值得进 INFO。
                rows_method = getattr(result, "rows", None)
                rows = rows_method() if callable(rows_method) else (rows_method or [])
                changed = bool(getattr(result, "errors", None)) or any(
                    up or down for _name, up, down, _fail in rows)
                (logger.info if changed else logger.debug)(
                    "[account] 同步完成：{}", text)
            else:
                logger.warning("[account] 同步未完成：{}", result)
            self.sync_finished.emit(ok, result)
            return
        if ok:
            if result is None:
                self.apply(None)
            else:
                self.apply(result)
                # 登录成功不再自动同步：同步必须先经「初始同步」建立状态
        else:
            self.operation_finished.emit(False, result)

    # ─── 对外入口 ───────────────────────────────────────

    def restore_async(self) -> None:
        """启动时后台恢复登录态；只在第一次调用时真正执行。

        只读实例**不自动登录**：登录态与同步数据只归主实例，否则两个进程会
        交叉覆盖 account.json 的水位与脏标记。
        """
        if readonly_instance():
            self.apply(None)
            return
        if self.done or self.busy:
            return
        if not self.session.has_stored_session():
            self.apply(None)                   # 凭据库无登录态：不联网
            return
        self._start(self.session.restore, "operation")

    def login_async(self, login_name: str, password: str) -> bool:
        if readonly_instance():
            return False
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

    def sync_async(self, allow_overwrite: bool = False, *, auto: bool = False) -> bool:
        """一轮完整同步（用户表 + 文档通道 + 总览账本）；同一时刻只跑一份。

        ``auto=True`` 表示由分钟节拍自动触发：覆盖确认弹窗与红字报错被抑制
        （半夜的例行同步不该弹窗吵人），结果仍进表格与日志。
        未建立同步状态（没做过初始同步）时不执行——这是硬门禁。
        """
        from ..core.sync.initial import sync_established
        if readonly_instance() or not sync_established():
            return False
        from ..core.sync.engine import sync_documents
        token = self.session.access_token
        if not token:
            return False
        self.sync_attempted = True
        self.last_sync_auto = auto

        self._sync_cancel.clear()

        def action():
            from ..core.profile.snapshot_sync import sync_all
            from ..core.sync.definition_sync import sync_definition
            from ..core.sync.users_sync import sync_users
            def progress(data):
                self.sync_progress.emit(dict(data))

            stop = self._sync_cancel.is_set
            definition_report = sync_definition(token, on_progress=progress)
            users_report = sync_users(token, on_progress=progress, stop=stop)
            document_report = sync_documents(token, on_progress=progress, stop=stop)
            profile_report = sync_all(token, allow_overwrite=allow_overwrite,
                                      on_progress=progress, stop=stop)
            report = _CombinedReport(document_report, profile_report, users_report)
            report.errors.extend(definition_report.errors)
            return report

        return self._start(action, "sync")

    def initial_sync_async(self) -> bool:
        """初始同步：一次性建立本实例的同步状态（云端空则上传+建快照，
        云端有数据则备份本地三件套后拉远端）。"""
        from ..core.sync.initial import initial_sync
        token = self.session.access_token
        if readonly_instance() or not token:
            return False
        self.sync_attempted = True
        self.last_sync_auto = False
        self._sync_cancel.clear()

        def action():
            def progress(data):
                self.sync_progress.emit(dict(data))
            return initial_sync(token, on_progress=progress,
                                stop=self._sync_cancel.is_set)

        return self._start(action, "sync")

    def clear_sync_async(self, password: str) -> bool:
        """清除同步：清空云端同步数据 + 本机水位与状态，回到未建立。"""
        from ..core.sync.initial import clear_local_sync_state, set_sync_established
        if readonly_instance():
            return False

        def action():
            self.session.clear_sync(password)
            clear_local_sync_state()
            set_sync_established(False)
            self.last_sync_report = None
            self.last_sync_at = ""
            return self.session.summary()

        return self._start(action, "operation")

    def cancel_sync(self) -> None:
        """请求停止当前同步：步骤间与批次间协作式检查。"""
        self._sync_cancel.set()


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

        # 一行临时反馈（正在登录…／登录失败）：固定高度常驻，空文本不占额外空间，
        # 避免登录时从无到有把整个对话框顶跳一下。
        self.status = QLabel()
        self.status.setFixedHeight(20)
        self.status.setStyleSheet("color: #c62828; font-size: 12px;")

        # ════ 未登录：居中的紧凑登录卡（不摊满整个对话框） ════
        # 对话框标题已是「账户管理」，卡内不再重复「账户登录」标题。
        self.form_host = QWidget()
        outer = QVBoxLayout(self.form_host)
        outer.addStretch(2)
        card = QVBoxLayout()
        card.setAlignment(Qt.AlignmentFlag.AlignHCenter)
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
        self.login_input.setPlaceholderText(tr("账户"))
        self.password_field = PasswordField()
        self.password_field.editor.setPlaceholderText(tr("密码"))
        self.password_field.editor.returnPressed.connect(self._login)
        form.addRow(tr("账户"), self.login_input)
        form.addRow(tr("密码"), self.password_field.widget())
        self.login_button = QPushButton(tr("登录"))
        apply_button_style(self.login_button)
        self.login_button.setMinimumWidth(120)
        self.login_button.clicked.connect(self._login)
        register_button = QPushButton(tr("注册"))
        apply_button_style(register_button)
        register_button.setMinimumWidth(120)
        register_button.setToolTip(tr("在浏览器中打开注册页面"))
        register_button.clicked.connect(self._open_register_page)
        button_row = QHBoxLayout()
        button_row.addStretch()
        button_row.addWidget(self.login_button)
        button_row.addWidget(register_button)
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

        self.sync_table = QTableWidget(4, 6)
        self.sync_table.setHorizontalHeaderLabels(
            [tr("通道"), tr("进度"), tr("上行"), tr("下行"), tr("失败"), tr("功能")])
        _tune_table(self.sync_table, fixed={2: 72, 3: 72, 4: 72, 5: 104},
                    row_height=36, stretch_column=1)
        for row, name in enumerate((tr("定义"), tr("用户"), tr("文档"), tr("总览"))):
            item = QTableWidgetItem(name)
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.sync_table.setItem(row, 0, item)
            for column in (1, 2, 3, 4, 5):
                cell = QTableWidgetItem("")
                cell.setFlags(cell.flags() & ~Qt.ItemFlag.ItemIsEditable)
                cell.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.sync_table.setItem(row, column, cell)
        # 只有总览行有「创建快照」，其余三行功能列留白。
        snapshot_host = QWidget()
        snap_layout = QHBoxLayout(snapshot_host)
        snap_layout.setContentsMargins(4, 0, 4, 0)
        snap_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.snapshot_button = QPushButton(tr("创建快照"))
        apply_button_style(self.snapshot_button, variant="neutral")
        self.snapshot_button.setToolTip(tr(
            "把本机当前全部角色数据上传为云端总览基线。\n"
            "初始同步会自动创建一次；之后需要时手动点。"))
        self.snapshot_button.clicked.connect(self._refresh_snapshot)
        snap_layout.addWidget(self.snapshot_button)
        self.sync_table.setCellWidget(3, 5, snapshot_host)
        sync_layout.addWidget(self.sync_table, stretch=1)

        # 错误行：固定高度常驻，只换文字，不随失败出现/消失顶跳布局。
        self.sync_error = QLabel()
        self.sync_error.setFixedHeight(20)
        self.sync_error.setStyleSheet("color: #c62828; font-size: 12px;")
        sync_layout.addWidget(self.sync_error)

        self._step_rows = {tr("定义"): 0, tr("用户"): 1, tr("文档"): 2, tr("总览"): 3}
        # 同步层发的 step 是英文键（definition/roster/documents/overview），
        # 表格行键是中文通道名——不映射的话进度列永远对不上行
        self._step_key_rows = {"definition": self._step_rows[tr("定义")],
                               "roster": self._step_rows[tr("用户")],
                               "documents": self._step_rows[tr("文档")],
                               "overview": self._step_rows[tr("总览")]}

        # 行内状态（正在同步…／最近同步时间）
        self.sync_status = QLabel()
        self.sync_status.setStyleSheet("color: palette(mid); font-size: 12px;")

        sync_actions = QHBoxLayout()
        # 同一个按钮位：未建立 → 初始同步；已建立 → 清除同步（危险红）
        self.initial_clear_button = QPushButton(tr("初始同步"))
        apply_button_style(self.initial_clear_button)
        self.initial_clear_button.setToolTip(tr(
            "首次使用：建立本机与云端的同步状态。云端已有数据时，会先把本地\n"
            "用户、总览定义与总览数据备份到 config/session/_backup-* 再拉取云端。"))
        self.initial_clear_button.clicked.connect(self._initial_or_clear)
        sync_actions.addWidget(self.initial_clear_button)
        sync_actions.addSpacing(12)
        sync_actions.addWidget(self.sync_status)
        sync_actions.addStretch()
        self.stop_sync_button = QPushButton(tr("停止"))
        apply_button_style(self.stop_sync_button, variant="neutral")
        self.stop_sync_button.setFixedWidth(72)
        # 隐藏时保留占位，避免同步开始/结束时「立即同步」左右跳。
        stop_policy = self.stop_sync_button.sizePolicy()
        stop_policy.setRetainSizeWhenHidden(True)
        self.stop_sync_button.setSizePolicy(stop_policy)
        self.stop_sync_button.clicked.connect(self.holder.cancel_sync)
        self.stop_sync_button.setVisible(False)
        self.sync_button = QPushButton(tr("立即同步"))
        apply_button_style(self.sync_button)
        self.sync_button.clicked.connect(self._sync_now)
        sync_actions.addWidget(self.stop_sync_button)
        sync_actions.addWidget(self.sync_button)
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

        # 临时反馈固定在对话框最底部一行（左下角），不插在内容中间漂移
        layout.addWidget(self.status)

        self._connect(self.holder.changed, self._on_holder_changed)
        self._connect(self.holder.operation_finished, self._on_operation_failed)
        self._connect(self.holder.sync_finished, self._on_sync_finished)
        self._connect(self.holder.sync_progress, self._on_sync_progress)

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
        """一行临时反馈（正在登录…／失败原因）：固定高度常驻，只换文字。"""
        self.status.setText(text)

    def _on_operation_failed(self, ok: bool, result) -> None:
        if ok or not self._alive():
            return
        self._render(self.holder.summary)
        self._set_status(str(result))

    def _render(self, summary: dict | None) -> None:
        self._set_busy(self.holder.busy)
        if summary is None:
            # 未登录时头部不占位：登录卡本身就是状态，不写「未登录」占位文字
            self.account_label.setText("")
        else:
            # 登录成功即清掉「正在登录…」——临时反馈不留在已就绪的界面上
            self._set_status("")
            self._show_logged_in(summary)
        self.change_password_button.setVisible(summary is not None)
        self.logout_button.setVisible(summary is not None)
        self.tabs.setVisible(summary is not None)
        self.form_host.setVisible(summary is None)
        self._render_sync()

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
        """同步页反馈：失败原因进固定红字行；其余进按钮行的状态标签。"""
        if error:
            self.sync_error.setText(text)
            self.sync_error.setToolTip(text)
            return
        self.sync_error.setText("")
        self.sync_status.setText(text)

    def _on_sync_progress(self, data: dict) -> None:
        """分步进度：进度列实时显示细节，上行/下行随批累加。"""
        if not self._alive():
            return
        if data.get("reload_users"):
            self._reload_users()
        row = self._step_key_rows.get(data.get("step"))
        if row is None:
            return
        cell = self.sync_table.item(row, 1)
        if cell is not None:
            cell.setText(str(data.get("detail") or ""))
        for column, key in ((2, "pushed"), (3, "pulled")):
            if key in data:
                item = self.sync_table.item(row, column)
                if item is not None:
                    item.setText(str(data[key] or 0))

    def _render_sync(self) -> None:
        from ..core.sync.initial import sync_established
        established = sync_established()
        report = self.holder.last_sync_report
        if self.holder.syncing:
            self._set_sync_note(tr("正在同步…"))
        elif report is not None:
            # 结果在表格里；状态标签固定显示最近同步时间，确认点过就跑过
            self._set_sync_note(
                tr("最近同步时间：{}").format(self.holder.last_sync_at)
                if self.holder.last_sync_at else "")
            self._render_report(report)
        else:
            self._set_sync_note(
                tr("最近同步时间：{}").format(self.holder.last_sync_at)
                if self.holder.last_sync_at else "")
            self._fill_sync_table(None)
        running = self.holder.syncing or self.holder.busy
        self.sync_button.setEnabled(established and not running)
        self.sync_button.setToolTip(
            "" if established else tr("请先点「初始同步」建立同步状态"))
        self.initial_clear_button.setEnabled(not running
                                             and self.holder.summary is not None)
        apply_button_style(self.initial_clear_button,
                           variant="danger" if established else "action")
        self.initial_clear_button.setText(
            tr("清除同步") if established else tr("初始同步"))
        self.snapshot_button.setEnabled(established and not running
                                        and self.holder.summary is not None)
        self.stop_sync_button.setVisible(self.holder.syncing)

    def _fill_sync_table(self, rows: list[tuple[str, int | None, int | None, int | None]] | None) -> None:
        """四个通道行固定存在；没有结果时数值列留空。

        行按**通道名**对号入座，不按列表下标——刷新快照的报告只有单行
        「总览」，按下标写会把第 0 行「定义」的通道名覆盖成「总览」闪一下。
        """
        if not rows:
            rows = [(name, None, None, None)
                    for name in (tr("定义"), tr("用户"), tr("文档"), tr("总览"))]
        for name, pushed, pulled, failed in rows:
            row = self._step_rows.get(name)
            if row is None:
                continue
            def cell_text(value, failed_count=False):
                if value is None:
                    return ""
                if failed_count and not value:
                    return ""
                return str(value)

            for column, value in enumerate((
                    name, "", cell_text(pushed), cell_text(pulled),
                    cell_text(failed, failed_count=True))):
                item = QTableWidgetItem(value)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                if column != 1:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.sync_table.setItem(row, column, item)

    def _render_report(self, report) -> None:
        """统一渲染报告：完整同步是 _CombinedReport（rows() 方法），初始同步
        是 InitialSyncReport（rows 属性），刷新快照是裸 ProfileSyncReport。"""
        rows_method = getattr(report, "rows", None)
        if callable(rows_method):
            rows = rows_method()
            errors = report.distinct_errors()
        else:
            rows = getattr(report, "rows", None) or [
                (tr("总览"), report.pushed, report.pulled, len(report.errors))]
            errors = [str(e) for e in getattr(report, "errors", [])
                      if str(e).strip()]
        self._fill_sync_table(rows)
        if errors:
            shown = errors[:2]
            text = "；".join(shown)
            if len(errors) > len(shown):
                text += tr("；等 {} 项").format(len(errors))
            self.sync_error.setText(text)
            self.sync_error.setToolTip(text)
        else:
            self.sync_error.setText("")

    def _set_busy(self, busy: bool) -> None:
        for widget in (self.login_button, self.change_password_button,
                       self.logout_button, self.logout_all_button):
            widget.setEnabled(not busy)

    # ─── 动作 ───────────────────────────────────────────

    def _login(self) -> None:
        login_name = self.login_input.text().strip()
        password = self.password_field.editor.text()
        if not login_name:
            self._set_status(tr("请输入账户"))
            return
        if not password:
            self._set_status(tr("请输入密码"))
            return
        self._set_status(tr("正在登录…"))
        self.password_field.editor.clear()
        self.holder.login_async(login_name, password)

    def _open_register_page(self) -> None:
        """注册在网页端完成（自助注册），PC 只负责打开浏览器。"""
        url = f"{ACCOUNT_SERVICE}/#/register"
        if not QDesktopServices.openUrl(QUrl(url)):
            # 兜底：Qt 的 ShellExecute 失败时（如权限上下文不一致），用标准库再试
            import webbrowser
            logger.warning(f"QDesktopServices.openUrl 失败，改用 webbrowser 打开注册页：{url}")
            webbrowser.open(url)

    def _logout(self) -> None:
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
        from ..core.sync.initial import sync_established
        if not sync_established():
            self._set_sync_note(tr("请先点「初始同步」建立同步状态"))
            return
        if self.holder.sync_async():
            self._render_sync()

    def _initial_or_clear(self) -> None:
        from ..core.sync.initial import sync_established
        if sync_established():
            self._clear_sync()
            return
        # 云端有数据时必须先确认：会备份本地并拉取云端。
        try:
            state = self.holder.session.sync_state()
        except AccountError as error:
            self._set_sync_note(str(error), error=True)
            return
        if not state.get("empty"):
            answer = QMessageBox.question(
                self, tr("初始同步"),
                tr("云端已有数据。继续将先把本地用户、总览定义与总览数据备份到 "
                   "config/session/_backup-*，再拉取云端数据。是否继续？"),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            if answer != QMessageBox.StandardButton.Yes:
                return
        if self.holder.initial_sync_async():
            self._render_sync()

    def _clear_sync(self) -> None:
        """清空云端同步数据 + 本机同步状态；红色警告 + 密码确认。"""
        field = PasswordField(self)
        dialog = QDialog(self)
        dialog.setWindowTitle(tr("清除同步"))
        form = QFormLayout(dialog)
        warning = QLabel(tr(
            "将清空云端的定义、用户、文档与总览数据（账号与登录态保留）。\n"
            "此操作不可恢复；本地数据不会被删除。"))
        warning.setStyleSheet("color: #c62828;")
        form.addRow(warning)
        form.addRow(tr("密码"), field.widget())
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        password = field.editor.text()
        if not password:
            self._set_sync_note(tr("请输入密码"))
            return
        self._set_sync_note(tr("正在清除同步…"))
        self.holder.clear_sync_async(password)

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
        auto = self.holder.last_sync_auto
        if not ok:
            # 自动同步的失败只进日志，不刷红字（夜间断网不该满屏报错）
            if not auto:
                self._set_sync_note(str(result), error=True)
            return
        self._reload_users()
        pending = getattr(getattr(result, "profile", result), "pending", None) or []
        if pending:
            if auto:
                # 心跳自动同步不弹确认窗：提示用户手动处理即可
                self._set_sync_note(
                    tr("检测到云端覆盖待确认；点「立即同步」处理。"))
            else:
                self._confirm_overwrite(pending)

    def _reload_users(self) -> None:
        """同步拉回用户后让主窗口的用户管理器重读名册并刷新用户选择器。"""
        parent = self.parent()
        manager = getattr(parent, "user_manager", None)
        if manager is not None and hasattr(manager, "reload"):
            try:
                manager.reload()
                refresh = getattr(parent, "_refresh_user_combo", None)
                if callable(refresh):
                    refresh()
                profile_tab = getattr(parent, "_profile_tab", None)
                tab_refresh = getattr(profile_tab, "refresh", None)
                if callable(tab_refresh):
                    tab_refresh()
            except Exception:  # noqa: BLE001 - 刷新失败不能把同步结果判死
                logger.warning("同步后刷新用户管理器失败", exc_info=True)

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
