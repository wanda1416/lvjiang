"""批量执行页的执行控制、轮次导航与状态联动逻辑。"""

from __future__ import annotations

import json
from typing import cast

from loguru import logger
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QFormLayout,
    QFrame,
    QLabel,
    QScrollArea,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from lvjiang.ui.execution.feedback import (
    apply_idle_start,
    idle_start_state,
    show_running_feedback,
)

from ...core.batch.config import (
    BatchConfigItem,
    declares_unit_prepare,
)
from ...i18n import tr
from ..main.run_control import (
    STATE_PAUSING,
    STATE_STOPPING,
)
from ..widgets.button_styles import (
    apply_execution_button_style,
)
from ..widgets.misc import add_top_aligned_row
from .batch_runner import (
    ST_NOT_RUN,
    ST_RUNNING,
    BatchRunSpec,
    BatchScript,
    PlannedTask,
)
from .batch_widgets import (
    _BatchProgressState,
    _status_color,
)


class _BatchExecutionMixin:
    _params_popup: QFrame | None

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
        self._start_feedback.clear_failure()
        state = idle_start_state(self._host)
        if state != "idle":
            self._refresh_run_button(state)
            self._host.append_log(f"[批量] {self._start_feedback.text()}")
            return
        # 参数防抖还没落盘时先落一次，保证快照与界面显示的是同一份值。
        self._flush_lifecycle_params()
        usernames = self._get_enabled_usernames()
        scripts = self._checked_scripts()

        if not usernames:
            self._show_start_error(tr("暂无启用的单元，请到「单元」页勾选"))
            return
        if not scripts:
            self._show_start_error(tr("请至少勾选一个脚本"))
            return

        config = self._item
        if config is not None and config.execution_unit_key != "user":
            # 属性单元的一个单元值可能对应多名用户，必须由条目准备 wf 选定并回传
            # 用户名。不声明这条协议的 wf 跑起来只会让每个单元都以同一个协议错误
            # 被丢弃，整批空跑，所以在动客户端之前就拦住。
            from PyQt6.QtWidgets import QMessageBox
            if not config.workflows.prepare_item:
                self._show_start_error(tr("请在「工具 → 批量配置」中配置条目准备工作流"))
                QMessageBox.warning(
                    self, tr("无法开始批量任务"),
                    tr("当前按属性调度，请先在「工具 → 批量配置」中配置条目准备 wf。"),
                )
                return
            if not declares_unit_prepare(config.workflows.prepare_item):
                self._show_start_error(tr("条目准备工作流不支持当前属性调度，请到批量配置中修改"))
                QMessageBox.warning(
                    self, tr("无法开始批量任务"),
                    tr("当前按「{key}」调度，但条目准备工作流「{wf}」不会选定并返回"
                       "本单元的用户名。请改用 batch/prepare_item_by_attr.wf，"
                       "或在自写的准备工作流里声明 #% batch_unit_prepare: true "
                       "并返回 username。").format(
                        key=config.execution_unit_key,
                        wf=config.workflows.prepare_item),
                )
                return
        if config is None:
            self._show_start_error(tr("暂无配置组，请先通过「工具 → 批量配置」添加"))
            return

        # 允许竖屏会先把客户端收回登录主页，只有条目准备能再把人送进游戏；
        # 缺它时整批会在登录页上逐条失败，所以在动客户端之前就拦住。
        if (
            self._draft.allow_portrait
            and config.workflows.recover_unattended
            and not config.workflows.prepare_item
        ):
            from PyQt6.QtWidgets import QMessageBox
            self._show_start_error(tr("允许竖屏需要先配置条目准备工作流"))
            QMessageBox.warning(
                self, tr("无法开始批量任务"),
                tr("「允许竖屏」启动时会先由异常恢复 wf 把客户端收回登录主页，"
                   "需要条目准备工作流再把人送回游戏。请先在「工具 → 批量配置」"
                   "为该配置组配置条目准备 wf。"),
            )
            return

        # 设备执行占用原本只在每个条目准备时获取，手机端正在跑任务时整批会逐条失败
        # 并重复同一句原因。这里先确认一次，占用就整批不启动。
        busy = self._device_busy()
        if busy:
            from PyQt6.QtWidgets import QMessageBox
            self._show_start_error(tr("手机端正在执行任务，请暂停或者结束任务"))
            QMessageBox.warning(
                self, tr("无法开始批量任务"),
                tr("手机端正在执行任务，请暂停或者结束任务。\n\n设备返回：{detail}").format(
                    detail=busy),
            )
            return

        self._build_progress_table(
            usernames, config, scripts, rounds=self._draft.rounds)
        self._set_config_enabled(False)

        # 定义 + 草稿 → 不可变快照。之后再改配置组、改用户资料或在本页重新勾选，
        # 都不会影响这一批：调度器只认这份快照。「用户声明跳过」在这一刻读一次
        # 用户资料并冻结；运行中改声明不会改变本批。
        from ...core.batch.exclusions import collect_exclusions

        attr_mode = config.execution_unit_key != "user"
        manager = getattr(self._host, "_user_manager", None)
        excluded = collect_exclusions(
            list(config.usernames) if attr_mode else usernames,
            [script.id for script in scripts],
            getattr(manager, "_users_dir", None),
        )
        spec = BatchRunSpec.build(
            config, self._draft, entries=usernames, scripts=scripts,
            candidate_usernames=(
                list(config.usernames)
                if attr_mode else None),
            excluded=excluded,
        )
        if excluded:
            total = sum(len(tasks) for tasks in excluded.values())
            names = "、".join(
                f"{user}×{task_id}" for user, tasks in excluded.items()
                for task_id in tasks)
            self._host.append_log(
                f"[批量] 已按用户声明跳过 {total} 格：{names}")
        ok = self._host.run_batch(spec)
        if not ok:
            self._set_config_enabled(True)

    def _build_progress_table(
        self, usernames: list[str], config: BatchConfigItem | None,
        scripts: list[BatchScript], *, rounds: int = 1,
    ):
        """初始化进度表：条目×脚本 全量行。

        轮次不展开成额外行：同一批行复用，历史轮结果存在运行状态里按轮索引。
        这样 20 用户 × 5 脚本 × 10 轮也不会变成 1000 行。
        """
        attr_mode = config is not None and config.execution_unit_key != "user"
        entries = list(usernames)
        script_pairs = [(str(script.id), str(script.name)) for script in scripts]
        rounds_total = max(1, int(rounds))
        self._progress_build = (entries, script_pairs, rounds_total, attr_mode)
        self._render_progress_rows(_BatchProgressState(
            entries=entries, scripts=script_pairs, plans={},
            rounds_total=rounds_total, attr_mode=attr_mode,
        ))
        self._refresh_round_header(None)
        # Rows may make the vertical scrollbar appear, changing viewport width.
        QTimer.singleShot(0, self._set_progress_column_widths)

    @staticmethod
    def _row_round(state: _BatchProgressState, run_idx: int) -> int:
        """该行当前应显示的轮次：用户模式全局同轮，属性模式各单元独立。"""
        if state.attr_mode:
            return state.unit_rounds.get(run_idx, 1)
        return max(state.unit_rounds.values(), default=1)

    @staticmethod
    def _row_label(state: _BatchProgressState, run_idx: int) -> str:
        """条目列只放条目名：轮次由底部导航表达，不混进条目文本。"""
        return state.entries[run_idx]

    def _set_status_item(self, row: int, status: str, *, scroll: bool = False):
        item = QTableWidgetItem(status)
        item.setToolTip(status)
        color = _status_color(status)
        if color is not None:
            item.setBackground(color)
        self._progress_table.setItem(row, 2, item)
        if scroll:
            self._progress_table.scrollToItem(item)

    def _render_progress_rows(self, state: _BatchProgressState) -> None:
        """按「当前轮」或选中的「历史轮」重建表格。"""
        self._progress_table.setUpdatesEnabled(False)
        try:
            self._progress_table.setRowCount(0)
            self._progress_row_index = {}
            self._progress_row_context = {}
            for run_idx in range(len(state.entries)):
                round_number = (state.viewing_round
                                or self._row_round(state, run_idx))
                for script_id, script_name in state.scripts:
                    row = self._progress_table.rowCount()
                    self._progress_row_index[(run_idx, script_id)] = row
                    self._progress_row_context[row] = (run_idx, script_id)
                    self._progress_table.insertRow(row)
                    label = self._row_label(state, run_idx)
                    label_item = QTableWidgetItem(label)
                    label_item.setToolTip(label)
                    self._progress_table.setItem(row, 0, label_item)
                    script_item = QTableWidgetItem(script_name)
                    script_item.setToolTip(script_name)
                    self._progress_table.setItem(row, 1, script_item)
                    status = state.statuses.get(
                        (round_number, run_idx, script_id))
                    if status is None:
                        # 缺失一律是「未执行」：待执行只由真实事件写入——轮到这个
                        # 条目、调度器开始做条目准备时才会推进，无法从"没有状态"
                        # 反推出来。
                        status = ST_NOT_RUN
                    self._set_status_item(row, status)
        finally:
            self._progress_table.setUpdatesEnabled(True)

    # ─── 轮次导航 ─────────────────────────────────────────

    def _set_round_controls_visible(self, visible: bool) -> None:
        for widget in (
            self._round_prev_btn, self._round_label,
            self._round_next_btn, self._round_latest_btn,
        ):
            widget.setVisible(visible)

    @staticmethod
    def _display_round(state: _BatchProgressState) -> int:
        """导航当前停留的轮次：0 表示跟随最新，其它为固定轮次。"""
        if state.viewing_round:
            return state.viewing_round
        return max(state.unit_rounds.values(), default=1)

    def _refresh_round_header(
        self, state: _BatchProgressState | None,
    ) -> None:
        if state is None or state.rounds_total <= 1:
            self._set_round_controls_visible(False)
            return
        self._set_round_controls_visible(True)
        number = self._display_round(state)
        self._round_label.setText(tr("第 {round}/{rounds} 轮").format(
            round=number, rounds=state.rounds_total))
        self._round_prev_btn.setEnabled(number > 1)
        self._round_next_btn.setEnabled(number < state.rounds_total)
        # ⏭ 的按下态就是「正在跟随最新」：下一轮开始视图会自动跟上。
        self._round_latest_btn.setChecked(state.viewing_round == 0)

    def _show_round(self, number: int) -> None:
        """切到指定轮次；0 表示回到最新并恢复自动跟随。"""
        state = self._run_progress.get(self._visible_run_id)
        if state is None:
            return
        if number:
            number = max(1, min(number, state.rounds_total))
            # 停在当前最新轮时仍然固定，不自动跟随；只有 >> 才恢复跟随。
            state.viewing_round = number
        else:
            state.viewing_round = 0
        self._render_progress_rows(state)
        self.apply_task_plan(state.plans)
        self._refresh_round_header(state)
        if not state.viewing_round:
            self._scroll_to_running_row()

    def _on_prev_round(self) -> None:
        state = self._run_progress.get(self._visible_run_id)
        if state is None:
            return
        self._show_round(max(1, self._display_round(state) - 1))

    def _on_next_round(self) -> None:
        state = self._run_progress.get(self._visible_run_id)
        if state is None:
            return
        self._show_round(min(state.rounds_total,
                             self._display_round(state) + 1))

    def _on_latest_round(self) -> None:
        self._show_round(0)

    def _scroll_to_running_row(self) -> None:
        """跟随最新时把视图定位到当前正在跑的行，避免要手动往下翻。"""
        for row in range(self._progress_table.rowCount()):
            item = self._progress_table.item(row, 2)
            if item is not None and item.text() == ST_RUNNING:
                self._progress_table.scrollToItem(item)
                return

    def apply_task_plan(
        self, plan: dict[tuple[int, str], PlannedTask],
    ) -> None:
        """绑定本轮执行快照，并标记使用用户独立参数的组合。"""
        self._progress_task_plan = plan
        for key, planned in plan.items():
            row = self._progress_row_index.get(key)
            if row is None:
                continue
            item = self._progress_table.item(row, 1)
            if item is None:
                continue
            prefix = "* " if planned.parameter_source == "user" else ""
            item.setText(prefix + planned.script.name)
            item.setToolTip(
                (tr("* 表示当前用户使用独立参数\n")
                 if planned.parameter_source == "user" else "")
                + tr("点击查看本轮任务参数"))

    def bind_run(self, task_run_id: str) -> None:
        """将当前刚初始化的进度表绑定到运行实例。"""
        entries, scripts, rounds_total, attr_mode = self._progress_build
        state = _BatchProgressState(
            entries=list(entries),
            scripts=list(scripts),
            plans=dict(self._progress_task_plan),
            rounds_total=rounds_total,
            attr_mode=attr_mode,
        )
        self._run_progress[task_run_id] = state
        self._visible_run_id = task_run_id
        # 上一条运行的表格内容还留在控件里；先按新状态整表重绘，第一帧就是对的。
        self._render_progress_rows(state)
        self._refresh_round_header(state)

    def show_run(self, task_run_id: str) -> None:
        self._progress_table.setUpdatesEnabled(False)
        try:
            self._show_run_contents(task_run_id)
        finally:
            self._progress_table.setUpdatesEnabled(True)

    def _show_run_contents(self, task_run_id: str) -> None:
        """恢复任务进度内容；调用方负责暂停表格重绘。"""
        state = self._run_progress.get(task_run_id)
        if state is None:
            self._visible_run_id = ""
            self._progress_table.setRowCount(0)
            self._progress_row_index = {}
            self._progress_row_context = {}
            self._progress_task_plan = {}
            self._refresh_round_header(None)
            self._running = False
            self._refresh_run_button("idle")
            self._set_config_enabled(True)
            return
        self._visible_run_id = task_run_id
        self._progress_task_plan = dict(state.plans)
        self._render_progress_rows(state)
        self.apply_task_plan(state.plans)
        self._refresh_round_header(state)
        self._running = not state.terminal
        self._refresh_run_button("running" if self._running else "idle")
        self._set_config_enabled(not self._running)

    def apply_run_task_plan(
        self, task_run_id: str, plan: dict[tuple[int, str], PlannedTask],
    ) -> None:
        state = self._run_progress.get(task_run_id)
        if state is not None:
            state.plans = dict(plan)
        if self._visible_run_id == task_run_id:
            self.apply_task_plan(plan)

    def apply_run_selected_unit_plan(
        self, task_run_id: str, run_idx: int,
        plan: dict[tuple[int, str], PlannedTask],
    ) -> None:
        state = self._run_progress.get(task_run_id)
        if state is not None:
            state.plans.update(plan)
        if self._visible_run_id == task_run_id:
            self.apply_selected_unit_plan(run_idx, plan)

    def apply_selected_unit_plan(
        self, _run_idx: int, plan: dict[tuple[int, str], PlannedTask],
    ) -> None:
        """属性单元确定角色后，显示该角色冻结的任务参数。"""
        self._progress_task_plan.update(plan)
        for key, planned in plan.items():
            row = self._progress_row_index.get(key)
            item = self._progress_table.item(row, 1) if row is not None else None
            if item is not None:
                item.setText(
                    ("* " if planned.parameter_source == "user" else "")
                    + planned.script.name)
                item.setToolTip(
                    tr("执行用户：{username}\n点击查看本轮任务参数").format(
                        username=planned.username))

    @staticmethod
    def _format_param_value(value) -> str:
        if isinstance(value, bool):
            return tr("是") if value else tr("否")
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=False)
        return str(value)

    def _on_progress_cell_clicked(self, row: int, column: int) -> None:
        """点击脚本名称时显示当前用户与任务的参数快照。"""
        if column != 1:
            return
        key = self._progress_row_context.get(row)
        planned = self._progress_task_plan.get(key) if key is not None else None
        if planned is None:
            return
        if self._params_popup is not None:
            self._params_popup.close()

        popup = QFrame(cast(QWidget, self), Qt.WindowType.Popup)
        popup.setFrameShape(QFrame.Shape.StyledPanel)
        popup.setMinimumWidth(300)
        popup.setMaximumWidth(480)
        outer = QVBoxLayout(popup)
        outer.setContentsMargins(12, 10, 12, 10)
        outer.setSpacing(8)
        outer.addWidget(QLabel(f"<b>{planned.script.name}</b>"))
        source = (tr("用户独立参数") if planned.parameter_source == "user"
                  else tr("全局任务参数"))
        detail = QLabel(
            tr("执行用户：{username}\n参数来源：{source}").format(
                username=planned.username, source=source))
        detail.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        outer.addWidget(detail)

        body = QWidget()
        form = QFormLayout(body)
        form.setContentsMargins(0, 0, 0, 0)
        definitions = {
            str(item.get("name")): str(item.get("label") or item.get("name"))
            for item in (planned.script.parameters or [])
            if isinstance(item, dict) and item.get("name")
        }
        if planned.params:
            for name, value in planned.params.items():
                value_label = QLabel(self._format_param_value(value))
                value_label.setWordWrap(True)
                value_label.setTextInteractionFlags(
                    Qt.TextInteractionFlag.TextSelectableByMouse)
                add_top_aligned_row(
                    form, definitions.get(name, name) + "：", value_label)
        else:
            form.addRow(QLabel(tr("该任务没有可配置参数")))
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setMaximumHeight(260)
        scroll.setWidget(body)
        outer.addWidget(scroll)

        item = self._progress_table.item(row, column)
        if item is None:
            return
        rect = self._progress_table.visualItemRect(item)
        viewport = cast(QWidget, self._progress_table.viewport())
        position = viewport.mapToGlobal(rect.bottomLeft())
        popup.adjustSize()
        popup.move(position)
        popup.show()
        self._params_popup = popup

    def update_run_progress(
        self, task_run_id: str, round_number: int, run_idx: int,
        entry_label: str, script_id: str, status: str,
    ) -> None:
        """执行侧唯一的进度入口：轮次是显式维度，历史轮不会被覆盖。"""
        state = self._run_progress.get(task_run_id)
        if state is None:
            return
        state.statuses[(round_number, run_idx, script_id)] = status
        entered_round = round_number > state.unit_rounds.get(run_idx, 0)
        if entered_round:
            state.unit_rounds[run_idx] = round_number
        if self._visible_run_id != task_run_id:
            return
        if entered_round and not state.viewing_round:
            # 轮次边界：整表按 statuses 重绘一次，而不是只改收到事件的这一格。
            # 逐格补丁的逻辑要求"每格都收到一条复位事件"，丢一条或晚到一条就会
            # 永久留着上一轮的文本；整表重绘让缺失状态自然回落到「未执行」。
            self._render_progress_rows(state)
            self.apply_task_plan(state.plans)
            self._refresh_round_header(state)
            self._scroll_to_running_row()
            return
        if entered_round:
            # 正在只读查看历史轮：只更新导航，不打断当前视图。
            self._refresh_round_header(state)
        if state.viewing_round and state.viewing_round != round_number:
            # 正在只读查看历史轮：状态继续入库，但不打断当前视图。
            return
        row = self._progress_row_index.get((run_idx, script_id))
        if row is None or row >= self._progress_table.rowCount():
            logger.warning(
                f"批量进度无处安放，已忽略: round={round_number} "
                f"run_idx={run_idx} script_id={script_id} label={entry_label!r}"
            )
            return
        self._set_status_item(row, status, scroll=not state.viewing_round)

    def _refresh_status_colors(self, _theme: str) -> None:
        """实时切换主题后重绘现有进度行。"""
        for row in range(self._progress_table.rowCount()):
            item = self._progress_table.item(row, 2)
            if item is None:
                continue
            color = _status_color(item.text())
            if color is not None:
                item.setBackground(color)

    def on_batch_finished(self, summary: dict):
        """批量全部结束（由 host 调用）"""
        self._running = False
        self._set_config_enabled(True)
        self._refresh_run_button("idle")

    def finish_run(self, task_run_id: str) -> None:
        state = self._run_progress.get(task_run_id)
        if state is not None:
            state.terminal = True
        if self._visible_run_id == task_run_id:
            if state is not None:
                # 结束后当前轮里没跑到的行改为「未执行」，历史轮汇总也用它。
                self._render_progress_rows(state)
                self.apply_task_plan(state.plans)
                self._refresh_round_header(state)
            self.on_batch_finished({})

    def refresh_config(self):
        """外部配置变更后调用，刷新配置 + 脚本 + 行列表"""
        self._refresh_config_combo()
        self._refresh_group_contents()

    def refresh_scripts(self):
        """脚本发现/暴露层变更后调用，只刷新脚本候选列表。

        候选列表、显示名和 wf 路径都来自 ``list_exposed_scripts()``。不跟着
        「脚本配置」重新拉一遍的话，批量页会一直拿着上次启动时的快照：改过
        的显示名不更新，取消暴露的脚本仍留在列表里并且照跑不误。
        """
        self._refresh_script_list()
        self._refresh_task_params_page()
        if hasattr(self, "_start_feedback"):
            self._refresh_start_feedback()

    # ─── 状态联动 ─────────────────────────────────────────

    def _on_automation_state(self, state: str):
        """宿主自动化状态变化 → 刷新按钮"""
        active_states = ("running", STATE_PAUSING, "paused", STATE_STOPPING)
        self._running = state in active_states
        self._refresh_run_button(state)
        if state in active_states:
            self._set_config_enabled(False)
        else:
            self._set_config_enabled(True)

    def _on_pause_resume_clicked(self):
        """暂停/恢复按钮点击 → 转发给宿主"""
        self._host.request_pause_resume()

    def _device_busy(self) -> str:
        """宿主提供设备预检时才询问；测试宿主、窗口目标都按空闲处理。"""
        check = getattr(self._host, "device_execution_busy", None)
        return check() if callable(check) else ""

    def _show_start_error(self, message: str) -> None:
        self._start_feedback.show_failure(message)
        self._host.append_log(f"[批量] {message}")

    def _start_reason(self) -> str:
        if hasattr(self, "_item") and self._item is None:
            return tr("暂无配置组，请先通过「工具 → 批量配置」添加")
        if hasattr(self, "_script_order") and not self._script_order:
            return tr("请到「脚本」页至少勾选一个脚本")
        if hasattr(self, "_user_order") and not self._user_order:
            return tr("请到「单元」页至少勾选一个执行单元")
        return ""

    def _refresh_start_feedback(self) -> None:
        if not self._running:
            self._start_feedback.clear_failure()
            self._refresh_run_button(idle_start_state(self._host))

    def _refresh_run_button(self, state: str):
        from ..execution.hotkeys import hotkey_label
        from ..main.run_control import other_task_running_label
        hk = self._host._user_config.hotkeys
        if label := other_task_running_label(self._host, "batch"):
            self._btn_run.setToolTip("")
            show_running_feedback(getattr(self, "_start_feedback", None), label)
            self._btn_run.setText(label)
            self._btn_run.setEnabled(False)
            apply_execution_button_style(self._btn_run, "disabled")
            self._btn_pause_resume.setText(tr("暂停"))
            self._btn_pause_resume.setEnabled(False)
            apply_execution_button_style(self._btn_pause_resume, "disabled")
            return
        if state == STATE_STOPPING:
            self._btn_run.setText(tr("停止中"))
            self._btn_run.setEnabled(False)
            apply_execution_button_style(self._btn_run, "stopping")
        elif state in ("running", STATE_PAUSING, "paused"):
            self._btn_run.setText(hotkey_label(tr("停止"), hk.stop))
            self._btn_run.setEnabled(True)
            apply_execution_button_style(self._btn_run, "stop")
        else:
            apply_idle_start(self._btn_run, getattr(self, "_start_feedback", None), self._host,
                             state, hotkey_label(tr("开始执行"), hk.start), _BatchExecutionMixin._start_reason(self))
        if state in ("running", STATE_PAUSING, "paused", STATE_STOPPING):
            self._btn_run.setToolTip("")
            show_running_feedback(getattr(self, "_start_feedback", None))
        # 刷新暂停/恢复按钮
        if state == "running":
            self._btn_pause_resume.setText(hotkey_label(tr("暂停"), hk.pause))
            self._btn_pause_resume.setEnabled(True)
            apply_execution_button_style(self._btn_pause_resume, "pause")
        elif state == STATE_PAUSING:
            self._btn_pause_resume.setText(tr("暂停中"))
            self._btn_pause_resume.setEnabled(False)
            apply_execution_button_style(self._btn_pause_resume, "pausing")
        elif state == "paused":
            self._btn_pause_resume.setText(hotkey_label(tr("恢复"), hk.pause))
            self._btn_pause_resume.setEnabled(True)
            apply_execution_button_style(self._btn_pause_resume, "run")
        else:
            self._btn_pause_resume.setText(tr("暂停"))
            self._btn_pause_resume.setEnabled(False)
            apply_execution_button_style(self._btn_pause_resume, "disabled")

    def _set_config_enabled(self, enabled: bool):
        """运行期间锁定配置组、脚本、用户和参数。"""
        self._script_list.setEnabled(enabled)
        self._btn_script_all.setEnabled(enabled)
        self._btn_script_invert.setEnabled(enabled)
        self._config_combo.setEnabled(enabled)
        self._btn_user_all.setEnabled(enabled)
        self._btn_user_invert.setEnabled(enabled)
        self._user_list.setEnabled(enabled)
        self._rounds_spin.setEnabled(enabled)
        self._unattended_check.setEnabled(
            enabled and bool(
                self._item is not None
                and self._item.workflows.recover_unattended))
        self._allow_portrait_check.setEnabled(
            enabled and bool(
                self._item is not None
                and self._item.workflows.recover_unattended
                and self._item.workflows.prepare_item))
        self._lifecycle_panel.setEnabled(enabled)
