"""批量执行 Tab —— 多用户 × 多脚本顺序执行

通用层页面（非插件），挂载于主窗口左侧 Tab「日常」之后。
每个用户轮次：游戏内账号切换（_switch_account.wf）→ 工具侧
session 切换 → 顺序执行所选脚本。

与宿主交互：run_batch() 启动、request_stop() 停止、
automation_state_changed 信号刷新按钮状态。
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .batch_runner import (
    ST_FAILED,
    ST_PENDING,
    ST_RUNNING,
    ST_SKIPPED,
    ST_SUCCESS,
    BatchScript,
    BatchUser,
)

# 状态 → 表格背景色
_STATUS_COLORS = {
    ST_PENDING: QColor("#f5f5f5"),
    ST_RUNNING: QColor("#e3f2fd"),
    ST_SUCCESS: QColor("#e8f5e9"),
    ST_FAILED: QColor("#ffebee"),
    ST_SKIPPED: QColor("#fff8e1"),
}

_STYLE_BTN_RUN = (
    "background-color: #4CAF50; color: white; font-weight: bold; padding: 8px;"
)
_STYLE_BTN_STOP = (
    "background-color: #f44336; color: white; font-weight: bold; padding: 8px;"
)
_STYLE_BTN_NOT_READY = (
    "background-color: #FFC107; color: #333; font-weight: bold; padding: 8px;"
)


class BatchTab(QWidget):
    """批量执行页面

    Args:
        host: MainWindow 实例（提供 run_batch / request_stop / 信号等）
    """

    def __init__(self, host):
        super().__init__()
        self._host = host
        self._running = False
        self._setup_ui()

        # 宿主状态信号
        host.automation_state_changed.connect(self._on_automation_state)
        host.user_changed.connect(lambda _: self._refresh_user_list())

        self._refresh_user_list()
        self._refresh_script_list()

    # ─── UI 构建 ─────────────────────────────────────────

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        # ── 用户列表 ──
        layout.addWidget(self._section_label("执行用户（勾选 & 排序）"))

        self._user_list = QListWidget()
        self._user_list.setMinimumHeight(90)
        layout.addWidget(self._user_list)

        btn_row = QHBoxLayout()
        self._btn_up = QPushButton("↑ 上移")
        self._btn_up.clicked.connect(lambda: self._move_item(-1))
        btn_row.addWidget(self._btn_up)
        self._btn_down = QPushButton("↓ 下移")
        self._btn_down.clicked.connect(lambda: self._move_item(1))
        btn_row.addWidget(self._btn_down)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        # ── 脚本列表 ──
        layout.addWidget(self._section_label("执行脚本（勾选）"))

        self._script_list = QListWidget()
        self._script_list.setMinimumHeight(90)
        layout.addWidget(self._script_list)

        # ── 选项 ──
        self._chk_skip_first = QCheckBox("首个用户跳过切换（已登录）")
        self._chk_skip_first.setChecked(True)
        layout.addWidget(self._chk_skip_first)

        # ── 执行按钮 ──
        self._btn_run = QPushButton("开始批量执行 (F9)")
        self._btn_run.setStyleSheet(_STYLE_BTN_RUN)
        self._btn_run.clicked.connect(self._on_run_clicked)
        layout.addWidget(self._btn_run)

        # ── 进度表 ──
        layout.addWidget(self._section_label("执行进度"))

        self._progress_table = QTableWidget(0, 3)
        self._progress_table.setHorizontalHeaderLabels(["用户", "脚本", "状态"])
        self._progress_table.horizontalHeader().setStretchLastSection(True)
        self._progress_table.setColumnWidth(0, 90)
        self._progress_table.setColumnWidth(1, 110)
        self._progress_table.setEditTriggers(
            QTableWidget.EditTrigger.NoEditTriggers)
        self._progress_table.verticalHeader().setVisible(False)
        layout.addWidget(self._progress_table, stretch=1)

    @staticmethod
    def _section_label(text: str) -> QLabel:
        label = QLabel(text)
        label.setStyleSheet("font-weight: bold; font-size: 12px; color: #333;")
        return label

    # ─── 列表刷新 ─────────────────────────────────────────

    def _refresh_user_list(self):
        """刷新用户勾选列表（保留当前勾选与顺序）"""
        # 记住旧顺序与勾选
        old_order: list[tuple[str, bool]] = []
        for i in range(self._user_list.count()):
            item = self._user_list.item(i)
            old_order.append((item.data(Qt.ItemDataRole.UserRole),
                              item.checkState() == Qt.CheckState.Checked))
        old_map = dict(old_order)

        self._user_list.blockSignals(True)
        self._user_list.clear()
        um = self._host._user_manager
        # 按旧顺序排前，新用户追加
        names = [n for n, _ in old_order if um.get_user(n)]
        names += [n for n in um.list_users() if n not in old_map]
        for name in names:
            user = um.get_user(name)
            suffix = ""
            if user and (user.game_account or user.game_character):
                suffix = f"（{user.game_account or '?'} / " \
                         f"{user.game_character or '?'}）"
            item = QListWidgetItem(f"{name}{suffix}")
            item.setData(Qt.ItemDataRole.UserRole, name)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            checked = old_map.get(name, False)
            item.setCheckState(
                Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
            self._user_list.addItem(item)
        self._user_list.blockSignals(False)

    def _refresh_script_list(self):
        """刷新脚本勾选列表（数据源与日常下拉一致）"""
        from ..workflows.discovery import list_exposed_scripts

        self._script_list.blockSignals(True)
        self._script_list.clear()
        try:
            configs = list_exposed_scripts()
        except Exception:
            configs = []
        for cfg in configs:
            item = QListWidgetItem(cfg["name"])
            item.setData(Qt.ItemDataRole.UserRole, cfg)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked)
            self._script_list.addItem(item)
        self._script_list.blockSignals(False)

    # ─── 列表操作 ─────────────────────────────────────────

    def _move_item(self, delta: int):
        """上移/下移当前选中项"""
        row = self._user_list.currentRow()
        if row < 0:
            return
        new_row = row + delta
        if new_row < 0 or new_row >= self._user_list.count():
            return
        item = self._user_list.takeItem(row)
        self._user_list.insertItem(new_row, item)
        self._user_list.setCurrentRow(new_row)

    def _checked_users(self) -> list[BatchUser]:
        """勾选的用户（按列表顺序）"""
        um = self._host._user_manager
        users = []
        for i in range(self._user_list.count()):
            item = self._user_list.item(i)
            if item.checkState() != Qt.CheckState.Checked:
                continue
            name = item.data(Qt.ItemDataRole.UserRole)
            user = um.get_user(name)
            if user:
                users.append(BatchUser(
                    name=user.name,
                    game_account=user.game_account,
                    game_character=user.game_character,
                ))
        return users

    def _checked_scripts(self) -> list[BatchScript]:
        """勾选的脚本（按列表顺序）"""
        scripts = []
        for i in range(self._script_list.count()):
            item = self._script_list.item(i)
            if item.checkState() != Qt.CheckState.Checked:
                continue
            cfg = item.data(Qt.ItemDataRole.UserRole)
            scripts.append(BatchScript(
                id=cfg["id"],
                name=cfg["name"],
                wf_file=cfg.get("wf_file", ""),
                class_name=cfg.get("class", ""),
            ))
        return scripts

    # ─── 执行控制 ─────────────────────────────────────────

    def f9_run(self):
        """F9 快捷键入口：运行中 → 停止；否则启动批量"""
        if self._host.is_running:
            self._host.request_stop()
            return
        self._start_batch()

    def _on_run_clicked(self):
        if self._running:
            self._host.request_stop()
            return
        self._start_batch()

    def _start_batch(self):
        users = self._checked_users()
        scripts = self._checked_scripts()
        if not users:
            self._host.append_log("[批量] 请至少勾选一个用户")
            return
        if not scripts:
            self._host.append_log("[批量] 请至少勾选一个脚本")
            return

        # 构建进度表
        self._build_progress_table(users, scripts)
        self._set_config_enabled(False)

        ok = self._host.run_batch(
            users, scripts,
            skip_first_switch=self._chk_skip_first.isChecked(),
        )
        if not ok:
            self._set_config_enabled(True)

    def _build_progress_table(self, users: list[BatchUser],
                              scripts: list[BatchScript]):
        """初始化进度表：用户×脚本 全量行"""
        self._progress_table.setRowCount(0)
        for user in users:
            for script in scripts:
                row = self._progress_table.rowCount()
                self._progress_table.insertRow(row)
                self._progress_table.setItem(
                    row, 0, QTableWidgetItem(user.name))
                self._progress_table.setItem(
                    row, 1, QTableWidgetItem(script.name))
                status_item = QTableWidgetItem(ST_PENDING)
                status_item.setBackground(_STATUS_COLORS[ST_PENDING])
                self._progress_table.setItem(row, 2, status_item)

    def _update_progress(self, username: str, script_id: str, status: str):
        """更新进度表中匹配行的状态"""
        # 找脚本显示名
        script_name = script_id
        for i in range(self._script_list.count()):
            cfg = self._script_list.item(i).data(Qt.ItemDataRole.UserRole)
            if cfg and cfg["id"] == script_id:
                script_name = cfg["name"]
                break

        for row in range(self._progress_table.rowCount()):
            u_item = self._progress_table.item(row, 0)
            s_item = self._progress_table.item(row, 1)
            if u_item and u_item.text() == username and \
               s_item and s_item.text() == script_name:
                status_item = QTableWidgetItem(status)
                color = _STATUS_COLORS.get(status)
                if color:
                    status_item.setBackground(color)
                self._progress_table.setItem(row, 2, status_item)
                self._progress_table.scrollToItem(status_item)
                break

    def _on_batch_finished(self, summary: dict):
        """批量全部结束（主线程）"""
        self._running = False
        self._set_config_enabled(True)
        self._refresh_run_button("ready")
        # 刷新用户列表（批量过程中 active_user 可能已变化）
        self._refresh_user_list()

    # ─── 状态联动 ─────────────────────────────────────────

    def _on_automation_state(self, state: str):
        """宿主自动化状态变化 → 刷新按钮"""
        self._running = (state == "running")
        self._refresh_run_button(state)
        if state == "running":
            self._set_config_enabled(False)
        else:
            self._set_config_enabled(True)

    def _refresh_run_button(self, state: str):
        if state == "running":
            self._btn_run.setText("停止 (F10)")
            self._btn_run.setStyleSheet(_STYLE_BTN_STOP)
        elif state == "not_ready":
            self._btn_run.setText("未连接")
            self._btn_run.setStyleSheet(_STYLE_BTN_NOT_READY)
        else:
            self._btn_run.setText("开始批量执行 (F9)")
            self._btn_run.setStyleSheet(_STYLE_BTN_RUN)

    def _set_config_enabled(self, enabled: bool):
        """运行期间锁定配置区域"""
        self._user_list.setEnabled(enabled)
        self._script_list.setEnabled(enabled)
        self._btn_up.setEnabled(enabled)
        self._btn_down.setEnabled(enabled)
        self._chk_skip_first.setEnabled(enabled)
