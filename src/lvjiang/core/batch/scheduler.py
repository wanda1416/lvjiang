"""批量执行编排器

通用批处理执行器：按批次/条目生命周期调用 wf。
控制层只解释标准结果 status/message/state，不理解 state 内的业务字段。

线程模型：宿主提供线程，单个 BatchScheduler 串行执行，与主窗口
“同一时刻仅一个自动化”约束一致。

批量配置和 BatchScript 不携带用户参数值。BatchScheduler 构造时根据
“任务 ID + 用户”生成全部执行参数快照，运行过程中不再回读配置。

结构：数据结构与进度常量在 :mod:`.models`，生命周期/脚本阶段在
:mod:`.stages`，无人值守恢复在 :mod:`.unattended`；本模块只剩一轮批量的编排、
执行计划冻结与结果落盘。
"""

from __future__ import annotations

import copy
import json
import time
from contextlib import ExitStack, nullcontext
from datetime import datetime
from typing import Any, Callable

from loguru import logger

from ...errors import log_failure
from ...i18n import tr
from ...workflows.engine import DeviceWorkflowEngineBuilder, WorkflowEngine
from ...workflows.errors import WorkflowAbort
from ...workflows.result_protocol import (
    RESULT_FAILED,
    RESULT_SKIPPED,
    RESULT_STOPPED,
    RESULT_SUCCESS,
)
from ..android.recovery import ChannelFaultError
from ..config.users import SessionManager
from ..fs_util import task_result_path
from .config import (
    lifecycle_parameter_definitions,
)
from .models import (
    ST_CHANNEL_FAULT,
    ST_FAILED,
    ST_NOT_RUN,
    ST_PENDING,
    ST_RUNNING,
    ST_SKIPPED,
    ST_STOPPED,
    ST_SUCCESS,
    BatchCheckResult,
    BatchContext,
    BatchEvent,
    BatchRunSpec,
    BatchScript,
    BatchStageResult,
    PlannedTask,
    UnattendedInterrupt,
)
from .report import BatchReport
from .stages import StageExecutionMixin
from .unattended import UnattendedMixin

#: 转出口：外部（ondevice 批量、UI 批量页）按 ``batch.scheduler`` 导入这些
#: 名字，拆分后仍从这里取。
__all__ = [
    "ST_PENDING", "ST_RUNNING", "ST_SUCCESS", "ST_FAILED", "ST_SKIPPED",
    "ST_STOPPED", "ST_CHANNEL_FAULT", "ST_NOT_RUN",
    "RESULT_SUCCESS", "RESULT_FAILED", "RESULT_SKIPPED", "RESULT_STOPPED",
    "BatchEvent", "BatchScript", "PlannedTask", "BatchRunSpec", "BatchContext",
    "BatchStageResult", "BatchCheckResult", "UnattendedInterrupt",
    "BatchScheduler",
]


class BatchScheduler(StageExecutionMixin, UnattendedMixin):
    """批量执行工作线程

    Signals:
        progress(round_number, run_idx, entry_label, script_id, status):
            进度更新。轮次是显式字段：进度页据此区分「本轮」与「历史轮」，
            不需要再从标签文本里反解，也不需要用标签定位行。
        log(str): 日志行（由 host 转发到日志面板）
        finished_all(dict): 全部结束，携带汇总
    """
    # (round_number, run_idx, 条目标签, script_id, 状态)：run_idx 是条目
    # 序列中的位置，进度表按它直接定位，不靠标签文本匹配（标签会重名）。

    progress: Any
    selected_task_plan: Any
    log: Any
    finished_all: Any
    def __init__(
        self,
        spec: BatchRunSpec,
        ctx: BatchContext,
        session_manager: SessionManager,
        stop_check: Callable[[], bool],
        *,
        task_stop_check: Callable[[], bool] | None = None,
        task_stop_clear: Callable[[], None] | None = None,
    ):
        if not hasattr(self, "progress"):
            self.progress = BatchEvent()
            self.selected_task_plan = BatchEvent()
            self.log = BatchEvent()
            self.finished_all = BatchEvent()
        self._spec = spec
        self._usernames = list(spec.entries)
        self._candidate_usernames = list(spec.candidate_usernames)
        self._scripts = copy.deepcopy(list(spec.scripts))
        self._ctx = ctx
        self._session_manager = session_manager
        # 批次级停止（F10 / 主停止按钮）与单任务级停止分开：弹窗里的「停止任务」
        # 只置 task_stop，引擎据此中止当前脚本，调度器继续跑同一条目的下一项。
        self._stop_check = stop_check
        self._task_stop_check = task_stop_check or stop_check
        self._task_stop_clear = task_stop_clear or (lambda: None)
        self._stopped = False
        #: 通道级故障（画面流/ADB/代理不可恢复）导致的中止：与用户停止、
        #: 条目内异常都不同，报告与历史必须分开记。
        self._aborted = False
        self._channel_fault = ""
        self._run_started_at = 0.0
        self._task_plan: dict[tuple[int, str], PlannedTask] = {}
        self._member_task_plan: dict[tuple[str, str], PlannedTask] = {}
        self._unit_members: dict[str, list[str]] = {}
        self._user_attributes: dict[str, dict[str, str]] = {}
        self._workflow_configs: dict[str, dict] = {}
        self._dedicated_configs: dict[tuple[str, str], dict] = {}
        self._lifecycle_params: dict[str, dict] = {}
        #: 最近一次无人值守中止的弹窗文本；恢复后清空（见 _recover_unattended）。
        self._unattended_hit = ""
        self._build_execution_plan()


    def _abort_channel_fault(self, fault, *, report, label: str, script, run_idx: int,
                             round_number: int, attempt: int, task_run,
                             username: str, started_at=None) -> None:
        """通道级故障：详细记日志、按「通道中断」记账，并中止整批。

        与另外两类失败的区别：任务内异常只废掉当前条目，无人值守中断还会跑
        恢复 wf 继续下一项；通道坏了以后每一项都只会拿旧帧空转，所以这里直接
        整批终止，且不触发无人值守恢复流程。
        """
        detail = str(fault) or "执行通道恢复失败"
        self._aborted = True
        self._channel_fault = detail
        elapsed = max(0.0, time.monotonic() - self._run_started_at)
        message = (
            f"[通道故障] 批量中止：{label} → {script.name}"
            f"（第 {attempt} 次尝试，用户={username or '-'}，启动后 {elapsed:.0f}s）：{detail}")
        logger.error(message)
        self.log.emit(message)
        self.progress.emit(round_number, run_idx, label, script.id, ST_CHANNEL_FAULT)
        report.record_channel_fault(detail)
        report.end_script(ST_CHANNEL_FAULT)
        if task_run is not None:
            try:
                task_run.finish(status="channel_fault",
                                error_message=detail)
            except Exception as history_exc:  # noqa: BLE001
                logger.warning(f"任务历史收尾失败，继续退出批量任务: {history_exc}")
        if started_at is not None:
            try:
                self._save_result(username or "unknown", script, {
                    "error": detail, "exception_type": "ChannelFaultError",
                }, started_at)
            except Exception as output_exc:  # noqa: BLE001
                logger.warning(f"通道故障结果保存失败: {output_exc}")


    def _task_was_stopped(self) -> bool:
        """当前脚本被单独结束（弹窗「停止任务」），而整批没有要求停止。"""
        return self._task_stop_check() and not self._stop_check()


    def _history_target_kwargs(self) -> dict[str, str]:
        return {
            "target_id": self._ctx.target_id,
            "target_kind": self._ctx.target_kind,
            "target_label": self._ctx.target_label,
        }


    def _task_history_target_kwargs(self) -> dict[str, str]:
        return {
            **self._history_target_kwargs(),
            "environment": self._ctx.run_env,
            "layout": self._ctx.layout_name,
            "input_kind": self._ctx.input_kind,
        }


    def _build_execution_plan(self) -> None:
        """在工作线程启动前冻结全部任务参数和用户配置。"""
        from ..config.wf_configs import get_wf_config
        from ..task_params import merge_task_params
        from ..users.document import load_user_metadata

        shared = {
            script.id: get_wf_config(script.id)
            for script in self._scripts
        }
        self._workflow_configs = copy.deepcopy(shared)
        users = {}
        attr_mode = self._spec.execution_unit_key != "user"
        member_names = self._candidate_usernames if attr_mode else self._usernames
        for username in member_names:
            user = load_user_metadata(username, self._session_manager._users_dir)
            users[username] = user
            self._user_attributes[username] = (
                dict(user.attributes) if user is not None else {})
        if attr_mode:
            groups: dict[str, list[str]] = {}
            for username in member_names:
                value = str(self._user_attributes[username].get(
                    self._spec.execution_unit_key, "")).strip()
                if value:
                    groups.setdefault(value, []).append(username)
            self._unit_members = {
                value: list(groups.get(value, [])) for value in self._usernames
            }
        for run_idx, username in enumerate(member_names):
            user = users.get(username)
            for script in self._scripts:
                override = (
                    user.workflow_params.get(script.id)
                    if user is not None and script.id in user.workflow_params
                    else None)
                params, source = merge_task_params(
                    script.parameters or [], shared[script.id], override)
                if script.class_name:
                    from ...workflows.implementations import get_workflow_class
                    loader = getattr(get_workflow_class(script.class_name), "CONFIG_SNAPSHOT_LOADER", None)
                    if loader is not None:
                        self._dedicated_configs[(username, script.id)] = copy.deepcopy(loader(username, self._session_manager._users_dir))
                planned = PlannedTask(
                    run_idx, username, script, params, source)
                if attr_mode:
                    self._member_task_plan[(username, script.id)] = planned
                else:
                    self._task_plan[(run_idx, script.id)] = planned
        for phase, definitions in lifecycle_parameter_definitions(
            self._spec.workflows,
        ).items():
            params, _source = merge_task_params(
                definitions, self._spec.workflow_params.get(phase, {}), None)
            self._lifecycle_params[phase] = params


    def task_plan_snapshot(self) -> dict[tuple[int, str], PlannedTask]:
        """供进度页展示与本轮执行完全一致的参数快照。"""
        return copy.deepcopy(self._task_plan)


    def run(self):
        self._run_started_at = time.monotonic()
        self._aborted = False
        self._channel_fault = ""
        with logger.contextualize(
            task_run_id=self._ctx.task_run_id,
            target_id=self._ctx.target_id,
            target_kind=self._ctx.target_kind,
            target_label=self._ctx.target_label,
        ):
            self._run_with_context()


    def _run_with_context(self):
        self._execution_lease = None
        self._user_scope = ExitStack()
        self._batch_run = None
        try:
            self._run_locked()
        except Exception as exc:
            log_failure("批量执行失败", exc)
            self._log(f"[批量] 执行或保存失败: {exc}", "error")
            if self._batch_run is not None:
                try:
                    self._batch_run.finish(status="failed", error_message=str(exc))
                except Exception:
                    logger.exception("批量失败历史收尾失败")
            self.finished_all.emit({"entries": {}, "stopped": False, "error": str(exc)})
        finally:
            self._user_scope.close()
            if self._execution_lease is not None:
                self._execution_lease.release()
                self._execution_lease = None


    def _run_locked(self):
        if self._spec.execution_unit_key != "user":
            self._run_locked_by_attr()
            return
        from ..history.daily import try_create_batch_run
        batch_run = try_create_batch_run(
            config_name=self._spec.name,
            **self._history_target_kwargs(),
            input_snapshot={
                "usernames": list(self._usernames),
                "scripts": [
                    {"task_id": item.id, "task_name": item.name,
                     "scope": item.scope}
                    for item in self._scripts
                ],
                "rounds": self._spec.rounds,
                "workflows": self._spec.workflows.to_dict(),
                "workflow_params": copy.deepcopy(self._lifecycle_params),
                "skip_lifecycle_for_single_item": (
                    self._spec.skip_lifecycle_for_single_item),
            },
        )
        batch_run_id = batch_run.batch_run_id if batch_run is not None else ""
        self._batch_run = batch_run
        summary: dict = {
            "batch_run_id": batch_run_id,
            "entries": {}, "stopped": False, "lifecycle": {},
        }
        rounds = self._spec.rounds
        visits = [
            (round_number, run_idx, username)
            for round_number in range(1, rounds + 1)
            for run_idx, username in enumerate(self._usernames)
        ]
        total = len(visits)
        portrait_start = self._needs_portrait_recovery()
        use_lifecycle = (
            not (
                len(self._usernames) == 1
                and self._spec.skip_lifecycle_for_single_item
            )
            or portrait_start
        )
        if portrait_start and not self._spec.workflows.prepare_item:
            # 启动会先把客户端收回登录主页；没有条目准备就没人把它送回游戏，
            # 整批只会在登录页上逐条失败。UI 与快照都会先拦，这里是外部入口的兜底。
            raise RuntimeError(
                "允许竖屏需要条目准备工作流：启动会先回到登录主页，"
                "没有条目准备无法把客户端送回游戏。"
                "请先在「工具 → 批量配置」为该配置组配置条目准备 wf")
        self._log(f"[批量] 开始：{len(self._usernames)} 用户 × "
                      f"{len(self._scripts)} 脚本 × {rounds} 轮")
        self._log("[批量] 执行用户：" + "、".join(self._usernames))
        self._log("[批量] 执行任务：" + "、".join(
            script.name for script in self._scripts))
        if portrait_start:
            self._log("[批量] 启动画面为竖屏：本次强制使用批量生命周期，"
                      "先执行异常恢复再按条目进入游戏")
        elif not use_lifecycle:
            self._log("[批量] 单用户直通：跳过批量生命周期工作流")
        for run_idx, username in enumerate(self._usernames):
            for script in self._scripts:
                planned = self._task_plan[(run_idx, script.id)]
                params = json.dumps(planned.params, ensure_ascii=False, sort_keys=True)
                source = "用户独立" if planned.parameter_source == "user" else "全局任务"
                self._log(
                    f"[批量计划] 用户={username} 任务={script.name} "
                    f"参数来源={source} 输入参数={params}"
                )

        # 初始化报告
        report = BatchReport(
            config_name=self._spec.name,
            scripts=[(s.id, s.name) for s in self._scripts],
            workflows=(self._spec.workflows.to_dict()
                       if use_lifecycle else {}),
            total_rows=total,
            batch_run_id=batch_run_id,
        )
        report.start_batch()

        batch_state: dict = {}
        if portrait_start:
            batch_state = self._recover_to_login_at_start(batch_state)
        can_run = True
        if use_lifecycle:
            setup = self._run_stage(
                "batch_setup", self._spec.workflows.batch_setup,
                -1, "", batch_state, round_number=0,
            )
            batch_state = setup.state if setup.state is not None else batch_state
            summary["lifecycle"]["batch_setup"] = setup.status
            can_run = setup.status == RESULT_SUCCESS
            if not can_run:
                self._stopped = setup.status == RESULT_STOPPED
                self._log(self._stage_message(tr("批次准备"), setup))
                for round_number, run_idx, username in visits:
                    label = (username if rounds == 1 else
                             f"第 {round_number} 轮 · {username}")
                    summary["entries"][label] = {
                        "prepare": ST_SKIPPED,
                        "finish": ST_SKIPPED,
                        "scripts": {s.id: ST_SKIPPED for s in self._scripts},
                    }
                    report.start_entry(label, username)
                    report.record_prepare(ST_SKIPPED)
                    report.end_entry()
                    for script in self._scripts:
                        self.progress.emit(
                            round_number, run_idx, label, script.id, ST_SKIPPED)

        for visit_index, (round_number, run_idx, username) in enumerate(visits):
            if not can_run:
                break
            if self._stop_check():
                self._stopped = True
                break

            if run_idx == 0:
                # 轮次边界先全量复位，再去做任何条目准备/检查：进度页在这一刻
                # 就能把上一轮的结果归档并清空，不必等第一个条目真正开始。
                self._log(f"[批量] 开始第 {round_number}/{rounds} 轮")
                for pending_idx, pending_user in enumerate(self._usernames):
                    for pending_script in self._scripts:
                        self.progress.emit(
                            round_number, pending_idx, pending_user,
                            pending_script.id, ST_PENDING,
                        )

            label = (username if rounds == 1 else
                     f"第 {round_number} 轮 · {username}")
            if self._execution_lease is not None:
                self._user_scope.close()
                self._execution_lease.release()
                self._execution_lease = None
            if username:
                from ..access import AccessDeniedError, acquire_user
                try:
                    self._execution_lease = acquire_user(
                        username, self._session_manager._users_dir)
                except AccessDeniedError as exc:
                    reason = self._describe_user_conflict(username, exc)
                    entry_result = {
                        "prepare": ST_SKIPPED,
                        "finish": ST_SKIPPED,
                        "skip_reason": reason,
                        "scripts": {
                            script.id: ST_SKIPPED for script in self._scripts
                        },
                    }
                    summary["entries"][label] = entry_result
                    report.start_entry(label, username)
                    report.record_prepare(ST_SKIPPED)
                    report.record_skip_reason(reason)
                    report.end_entry()
                    for script in self._scripts:
                        self.progress.emit(
                            round_number, run_idx, label, script.id, ST_SKIPPED)
                    self._log(f"[批量] {label} 跳过: {reason}")
                    continue
                self._user_scope.enter_context(self._execution_lease.authorized())
            self._log(f"[批量] ── [{visit_index + 1}/{total}] {label} ──")
            entry_result: dict = {
                "prepare": ST_SKIPPED,
                "finish": ST_SKIPPED,
                "scripts": {},
            }
            summary["entries"][label] = entry_result

            report.start_entry(label, username)

            # 每个用户、每轮都重新询问业务 wf 自己声明的可执行性。判定发生在
            # 条目准备之前，因此 profile 已满足条件时不会为了“发现无需执行”
            # 而启动或登录客户端。
            check_results: list[tuple[BatchScript, BatchCheckResult]] = []
            for script in self._scripts:
                if self._stop_check():
                    self._stopped = True
                    break
                planned = self._task_plan[(run_idx, script.id)]
                checked = self._check_script(
                    script, username, params=planned.params)
                check_results.append((script, checked))

            if self._stopped:
                report.end_entry()
                break
            if not any(
                checked.status == RESULT_SUCCESS
                for _script, checked in check_results
            ):
                for script, checked in check_results:
                    ui_status = self._result_to_ui_status(checked.status)
                    entry_result["scripts"][script.id] = ui_status
                    self.progress.emit(
                        round_number, run_idx, label, script.id, ui_status)
                    report.start_script(script.id, script.name)
                    report.end_script(ui_status)
                    reason = checked.message or checked.status
                    self._log(
                        f"[批量] {label} → {script.name} "
                        f"{'跳过' if checked.status == RESULT_SKIPPED else '检查失败'}: "
                        f"{reason}")
                report.record_prepare(ST_SKIPPED)
                report.end_entry()
                self._log(f"[批量] {label} 没有可执行任务，忽略该用户")
                continue

            prepared = BatchStageResult(state=batch_state)
            if use_lifecycle:
                prepared = self._run_stage(
                    "prepare_item", self._spec.workflows.prepare_item,
                    run_idx, username, batch_state,
                    round_number=round_number,
                )
                batch_state = (prepared.state if prepared.state is not None
                               else batch_state)
                prepare_ui_status = self._result_to_ui_status(prepared.status)
                entry_result["prepare"] = prepare_ui_status
                report.record_prepare(prepare_ui_status)
                if prepared.status != RESULT_SUCCESS:
                    report.end_entry()
                    for s in self._scripts:
                        entry_result["scripts"][s.id] = ST_SKIPPED
                        self.progress.emit(
                            round_number, run_idx, label, s.id, ST_SKIPPED)
                    self._log(self._stage_message(label, prepared))
                    if prepared.status == RESULT_STOPPED:
                        self._stopped = True
                        break
                    # 条目准备失败就整条跳过，这个用户不会再跑任务，
                    # 所以只需要回到登录主页等下一个用户。
                    self._recover_unattended(
                        run_idx, username, batch_state, pending=False)
                    continue

            # 2. 批量层显式传递用户：按行加载该用户 session，
            #    不触碰全局 active user，与 UI 下拉框彻底无关
            if username:
                session = self._session_manager.load(username)
            else:
                session = {}

            # 3. 顺序执行脚本
            for script_idx, script in enumerate(self._scripts):
                if self._stop_check():
                    self._stopped = True
                    break

                self.progress.emit(
                    round_number, run_idx, label, script.id, ST_RUNNING)
                self._log(f"[批量] {label} → {script.name} ...")
                report.start_script(script.id, script.name)
                planned = self._task_plan[(run_idx, script.id)]
                params = copy.deepcopy(planned.params)
                from ..history.daily import try_create_task_run
                task_started_at = datetime.now().astimezone()
                task_run = try_create_task_run(
                    username=username or "unknown", task_id=script.id,
                    task_name=script.name, task_scope=script.scope,
                    params=params, source=self._ctx.history_source, batch_run_id=batch_run_id,
                    started_at=task_started_at,
                    repository=(batch_run.repository
                                if batch_run is not None else None),
                    **self._task_history_target_kwargs(),
                )
                try:
                    capture = (task_run.capture_logs()
                               if task_run is not None else nullcontext())
                    with capture:
                        if task_run is not None:
                            logger.info(
                                f"任务开始: task_run_id={task_run.task_run_id}, "
                                f"batch_run_id={batch_run_id}, task_id={script.id}")
                        try:
                            result = self._run_script(
                                script, session, username, params=params)
                            if username:
                                self._session_manager.save(username, session)
                        except Exception as error:
                            log_failure(
                                f"批量执行 {label}/{script.name} 异常", error)
                            raise
                        if task_run is not None:
                            logger.info(
                                f"任务线程结束: task_run_id={task_run.task_run_id}")
                    task_stopped = self._task_was_stopped()
                    script_ui_status = ST_STOPPED if task_stopped else ST_SUCCESS
                    entry_result["scripts"][script.id] = script_ui_status
                    self.progress.emit(
                        round_number, run_idx, label, script.id, script_ui_status)
                    self._log(
                        f"[批量] {label} → {script.name} "
                        + ("已停止，继续下一项" if task_stopped else "完成"))
                    report.end_script(script_ui_status, result)
                    try:
                        result_path = self._save_result(
                            username or "unknown", script, result, task_started_at)
                    except Exception as output_exc:  # noqa: BLE001
                        result_path = None
                        logger.warning(f"批量结果保存失败，继续任务收尾: {output_exc}")
                        self._log(
                            f"[批量] {label} → {script.name} 结果 JSON 保存失败: "
                            f"{output_exc}")
                    if task_run is not None:
                        try:
                            task_run.finish(
                                status=("stopped" if task_stopped
                                        else "interrupted" if self._stop_check()
                                        else "completed"),
                                result_path=result_path,
                            )
                        except Exception as history_exc:  # noqa: BLE001
                            logger.warning(
                                f"任务历史收尾失败，继续批量任务: {history_exc}")
                except Exception as e:
                    result_path = None
                    try:
                        result_path = self._save_result(
                            username or "unknown", script, {
                                "error": str(e),
                                "exception_type": type(e).__name__,
                            }, task_started_at)
                    except Exception as output_exc:  # noqa: BLE001
                        logger.warning(f"批量失败结果保存失败: {output_exc}")
                    if task_run is not None:
                        try:
                            task_run.finish(
                                status="failed", result_path=result_path,
                                error_message=str(e))
                        except Exception as history_exc:  # noqa: BLE001
                            logger.warning(
                                f"任务历史收尾失败，继续批量任务: {history_exc}")
                    entry_result["scripts"][script.id] = ST_FAILED
                    self.progress.emit(
                        round_number, run_idx, label, script.id, ST_FAILED)
                    self._log(f"[批量] {label} → {script.name} 失败: {e}", "error")
                    report.end_script(ST_FAILED)
                    self._recover_unattended(
                        run_idx, username, batch_state,
                        pending=script_idx + 1 < len(self._scripts))
                except ChannelFaultError as fault:
                    # 通道级故障：后续每一项都只会拿旧帧空转，必须中止整批。
                    # 它跟「任务内异常」和「无人值守中断」都不是一类事，不能
                    # 记一条失败继续下一项，也不该跑无人值守恢复 wf（那时设备
                    # 本身不可用，恢复流程只会连环失败）。
                    self._abort_channel_fault(
                        fault, report=report, label=label, script=script,
                        run_idx=run_idx, round_number=round_number,
                        attempt=script_idx + 1, task_run=task_run,
                        username=username, started_at=task_started_at)

            # 用户中断或通道故障时，关闭尚未结束的脚本记录
            if self._stopped or self._aborted:
                report.finish_pending()

            if use_lifecycle:
                # 条目收尾收到通用执行摘要，wf 可据此维护私有状态。
                item_summary = {
                    "prepare": prepared.status,
                    "scripts": {
                        script_id: self._ui_status_to_result(status)
                        for script_id, status in entry_result["scripts"].items()
                    },
                }
                finished = self._run_stage(
                    "finish_item", self._spec.workflows.finish_item,
                    run_idx, username, batch_state, item_summary,
                    round_number=round_number,
                )
                batch_state = (finished.state if finished.state is not None
                               else batch_state)
                finish_ui_status = self._result_to_ui_status(finished.status)
                entry_result["finish"] = finish_ui_status
                report.record_finish(finish_ui_status)
                if finished.status != RESULT_SUCCESS:
                    self._log(self._stage_message(
                        f"{label} 条目收尾", finished))
                if finished.status == RESULT_STOPPED:
                    self._stopped = True

            report.end_entry()

            if self._stopped or self._aborted:
                break

        summary["stopped"] = self._stopped
        summary["aborted"] = self._aborted
        summary["channel_fault"] = self._channel_fault
        tag = (tr("（执行通道中断，整批中止）") if self._aborted
               else tr("（用户中断）") if self._stopped else "")
        self._log(f"[批量] 全部结束{tag}", "error" if self._aborted else "info")

        # setup 成功才说明批次现场已经建立，此时才允许执行 teardown。
        # setup 非 success 时直接终止，不启动任何后续生命周期阶段。
        if use_lifecycle and can_run:
            teardown = self._run_stage(
                "batch_teardown", self._spec.workflows.batch_teardown,
                -1, "", batch_state,
                {"stopped": self._stopped, "entries": summary["entries"]},
                round_number=rounds,
            )
            summary["lifecycle"]["batch_teardown"] = teardown.status
            if teardown.status != RESULT_SUCCESS:
                self._log(self._stage_message(tr("批次收尾"), teardown))

        # 6. 生成报告
        report.end_batch(stopped=self._stopped)
        report_path = None
        try:
            report_path = report.write()
            if report_path:
                self._log(f"[批量] 报告已保存: {report_path}")
        except Exception as e:
            logger.error(f"批量报告写入失败: {e}")
            self._log(f"[批量] 报告写入失败（不影响执行结果）: {e}", "warning")

        if batch_run is not None:
            task_failed = any(
                ST_FAILED in entry.get("scripts", {}).values()
                or entry.get("prepare") == ST_FAILED
                or entry.get("finish") == ST_FAILED
                for entry in summary["entries"].values()
            )
            lifecycle_failed = any(
                value == RESULT_FAILED
                for value in summary["lifecycle"].values()
            )
            try:
                batch_run.finish(
                    status=("channel_fault" if self._aborted
                            else "interrupted" if self._stopped
                            else "failed" if task_failed or lifecycle_failed
                            else "completed"),
                    report_path=report_path,
                    error_message=self._channel_fault if self._aborted else "",
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"批量历史收尾失败，继续退出批量任务: {exc}")

        self.finished_all.emit(summary)


    def _run_locked_by_attr(self) -> None:
        """属性单元的运行态调度；旧用户执行路径不受影响。"""
        from ..access import AccessDeniedError, acquire_user
        from ..history.daily import try_create_batch_run, try_create_task_run

        if not self._spec.workflows.prepare_item:
            raise ValueError("属性执行单元必须配置条目准备工作流")
        unit_key = self._spec.execution_unit_key
        batch_run = try_create_batch_run(
            config_name=self._spec.name,
            **self._history_target_kwargs(),
            input_snapshot={
                "execution_unit_key": unit_key,
                "units": copy.deepcopy(self._unit_members),
                "usernames": list(dict.fromkeys(
                    name for members in self._unit_members.values()
                    for name in members)),
                "scripts": [{"task_id": s.id, "task_name": s.name,
                             "scope": s.scope} for s in self._scripts],
                "rounds": self._spec.rounds,
                "workflows": self._spec.workflows.to_dict(),
                "workflow_params": copy.deepcopy(self._lifecycle_params),
            },
        )
        self._batch_run = batch_run
        batch_run_id = batch_run.batch_run_id if batch_run is not None else ""
        summary: dict = {"batch_run_id": batch_run_id, "entries": {},
                         "stopped": False, "lifecycle": {}}
        report = BatchReport(
            config_name=self._spec.name,
            scripts=[(s.id, s.name) for s in self._scripts],
            workflows=self._spec.workflows.to_dict(),
            total_rows=len(self._usernames) * self._spec.rounds,
            batch_run_id=batch_run_id,
        )
        report.start_batch()
        batch_state: dict = {}
        if self._needs_portrait_recovery():
            batch_state = self._recover_to_login_at_start(batch_state)
        setup = self._run_stage(
            "batch_setup", self._spec.workflows.batch_setup,
            -1, "", batch_state)
        batch_state = setup.state if setup.state is not None else batch_state
        summary["lifecycle"]["batch_setup"] = setup.status
        counts = {value: 0 for value in self._usernames}
        next_ready = {value: 0.0 for value in self._usernames}
        deferrals = {value: 0 for value in self._usernames}
        done: set[str] = set()
        attempt = 0
        cursor = 0
        if setup.status != RESULT_SUCCESS:
            self._stopped = setup.status == RESULT_STOPPED
            done.update(self._usernames)
            self._log(self._stage_message(tr("批次准备"), setup))
            for round_number in range(1, self._spec.rounds + 1):
                for run_idx, unit in enumerate(self._usernames):
                    label = f"{unit} · 第 {round_number} 次"
                    skipped_entry = {
                        "prepare": ST_SKIPPED, "finish": ST_SKIPPED,
                        "username": "",
                        "scripts": {script.id: ST_SKIPPED for script in self._scripts},
                    }
                    summary["entries"][label] = skipped_entry
                    report.start_entry(label, "")
                    report.record_prepare(ST_SKIPPED)
                    for script in self._scripts:
                        self.progress.emit(
                            round_number, run_idx, label, script.id, ST_SKIPPED)
                        report.start_script(script.id, script.name)
                        report.end_script(ST_SKIPPED)
                    report.end_entry()

        while (len(done) < len(self._usernames)
               and not self._stopped and not self._aborted
               and not self._stop_check()):
            ready = [value for value in self._usernames
                     if value not in done and next_ready[value] <= time.monotonic()]
            if not ready:
                time.sleep(min(1.0, max(0.0, min(
                    next_ready[value] for value in self._usernames if value not in done
                ) - time.monotonic())))
                continue
            unit = next((value for value in self._usernames[cursor:]
                         if value in ready), ready[0])
            run_idx = self._usernames.index(unit)
            cursor = (run_idx + 1) % len(self._usernames)
            members = self._unit_members.get(unit, [])
            if not members:
                self._log(f"[批量] 单元 {unit} 无用户资料，跳过")
                done.add(unit)
                continue
            attempt += 1
            round_number = counts[unit] + 1
            label = f"{unit} · 第 {round_number} 次 · 尝试 {attempt}"
            self._log(f"[批量] {unit_key}={unit}，第 {round_number}/{self._spec.rounds} 次")
            # 属性单元各自推进轮次，没有全局轮屏障：这里只复位本单元的行，
            # 其它单元仍保留它们自己那一轮的结果。
            for pending_script in self._scripts:
                self.progress.emit(
                    round_number, run_idx, unit, pending_script.id, ST_PENDING)
            report.start_entry(label, "")
            entry: dict = {"prepare": ST_SKIPPED, "finish": ST_SKIPPED,
                           "username": "", "scripts": {}}
            summary["entries"][label] = entry
            try:
                # 准备工作流可能选择任意成员；在它操作客户端前锁定整组。
                with ExitStack() as locks:
                    for name in sorted(members):
                        lease = acquire_user(name, self._session_manager._users_dir)
                        locks.callback(lease.release)
                        locks.enter_context(lease.authorized())

                    eligible: list[str] = []
                    checks_by_member: dict[str, dict[str, BatchCheckResult]] = {}
                    for name in members:
                        checks = {
                            script.id: self._check_script(
                                script, name,
                                params=self._member_task_plan[(name, script.id)].params,
                            ) for script in self._scripts
                        }
                        checks_by_member[name] = checks
                        if any(check.status == RESULT_SUCCESS
                               for check in checks.values()):
                            eligible.append(name)
                    if not eligible:
                        self._log(f"[批量] {unit} 内所有角色均无可执行任务")
                        done.add(unit)
                        for script in self._scripts:
                            statuses = [checks[script.id].status
                                        for checks in checks_by_member.values()]
                            status = (ST_FAILED if RESULT_FAILED in statuses
                                      else ST_SKIPPED)
                            entry["scripts"][script.id] = status
                            self.progress.emit(
                                round_number, run_idx, label, script.id, status)
                            report.start_script(script.id, script.name)
                            report.end_script(status)
                        report.record_prepare(ST_SKIPPED)
                        continue

                    prepared = self._run_stage(
                        "prepare_item", self._spec.workflows.prepare_item,
                        run_idx, "", batch_state, round_number=round_number,
                        unit_members=eligible,
                    )
                    batch_state = prepared.state if prepared.state is not None else batch_state
                    entry["prepare"] = self._result_to_ui_status(prepared.status)
                    report.record_prepare(entry["prepare"])
                    if prepared.status != RESULT_SUCCESS:
                        self._log(self._stage_message(label, prepared))
                        for script in self._scripts:
                            entry["scripts"][script.id] = ST_SKIPPED
                            self.progress.emit(
                            round_number, run_idx, label, script.id, ST_SKIPPED)
                        if prepared.status == RESULT_STOPPED:
                            self._stopped = True
                            done.add(unit)
                        elif prepared.status == RESULT_SKIPPED:
                            counts[unit] += 1
                            if counts[unit] >= self._spec.rounds:
                                done.add(unit)
                        else:
                            done.add(unit)
                        self._recover_unattended(
                            run_idx, "", batch_state, pending=False)
                        continue

                    username = prepared.username
                    if username not in eligible:
                        raise ValueError(self._unit_prepare_protocol_error(
                            prepared, eligible))
                    deferrals[unit] = 0
                    entry["username"] = username
                    report.set_entry_username(username)
                    self.selected_task_plan.emit(run_idx, {
                        (run_idx, script.id): self._member_task_plan[(username, script.id)]
                        for script in self._scripts
                    })
                    session = self._session_manager.load(username)
                    for script_idx, script in enumerate(self._scripts):
                        if self._stop_check():
                            self._stopped = True
                            break
                        planned = self._member_task_plan[(username, script.id)]
                        # 资格筛选只用于选角色；前一个脚本可能已经修改 Profile，
                        # 因此每个脚本执行前都要重新检查当前角色。
                        checked = self._check_script(
                            script, username, params=planned.params)
                        if checked.status != RESULT_SUCCESS:
                            status = self._result_to_ui_status(checked.status)
                            entry["scripts"][script.id] = status
                            self.progress.emit(
                                round_number, run_idx, label, script.id, status)
                            report.start_script(script.id, script.name)
                            report.end_script(status)
                            reason = checked.message or checked.status
                            self._log(
                                f"[批量] {label} → {script.name} "
                                f"{'跳过' if status == ST_SKIPPED else '检查失败'}: "
                                f"{reason}")
                            check_run = try_create_task_run(
                                username=username, task_id=script.id,
                                task_name=script.name, task_scope=script.scope,
                                params=copy.deepcopy(planned.params), source=self._ctx.history_source,
                                batch_run_id=batch_run_id,
                                repository=(batch_run.repository
                                            if batch_run is not None else None),
                                **self._task_history_target_kwargs(),
                            )
                            if check_run is not None:
                                try:
                                    check_run.finish(
                                        status=checked.status,
                                        error_message=reason,
                                    )
                                except Exception as history_exc:  # noqa: BLE001
                                    logger.warning(
                                        f"任务历史收尾失败，继续批量任务: {history_exc}")
                            continue
                        self.progress.emit(
                    round_number, run_idx, label, script.id, ST_RUNNING)
                        report.start_script(script.id, script.name)
                        task_started_at = datetime.now().astimezone()
                        task_run = try_create_task_run(
                            username=username, task_id=script.id,
                            task_name=script.name, task_scope=script.scope,
                            params=copy.deepcopy(planned.params), source=self._ctx.history_source,
                            batch_run_id=batch_run_id, started_at=task_started_at,
                            repository=(batch_run.repository
                                        if batch_run is not None else None),
                            **self._task_history_target_kwargs(),
                        )
                        try:
                            capture = (task_run.capture_logs()
                                       if task_run is not None else nullcontext())
                            with capture:
                                result = self._run_script(
                                    script, session, username,
                                    params=copy.deepcopy(planned.params))
                                self._session_manager.save(username, session)
                            task_stopped = self._task_was_stopped()
                            script_ui_status = ST_STOPPED if task_stopped else ST_SUCCESS
                            entry["scripts"][script.id] = script_ui_status
                            report.end_script(script_ui_status, result)
                            self.progress.emit(
                        round_number, run_idx, label, script.id, script_ui_status)
                            self._log(
                                f"[批量] {label} → {script.name} "
                                + ("已停止，继续下一项" if task_stopped else "完成"))
                            try:
                                result_path = self._save_result(
                                    username, script, result, task_started_at)
                            except Exception as output_exc:  # noqa: BLE001
                                result_path = None
                                logger.warning(
                                    f"批量结果保存失败，继续任务收尾: {output_exc}")
                                self._log(
                                    f"[批量] {label} → {script.name} "
                                    f"结果 JSON 保存失败: {output_exc}")
                            if task_run is not None:
                                try:
                                    task_run.finish(
                                        status=("stopped" if task_stopped
                                                else "interrupted" if self._stop_check()
                                                else "completed"),
                                        result_path=result_path)
                                except Exception as history_exc:  # noqa: BLE001
                                    logger.warning(
                                        f"任务历史收尾失败，继续批量任务: {history_exc}")
                        except ChannelFaultError as fault:
                            self._abort_channel_fault(
                                fault, report=report, label=label, script=script,
                                run_idx=run_idx, round_number=round_number,
                                attempt=script_idx + 1, task_run=task_run,
                                username=username, started_at=task_started_at)
                        except Exception as exc:  # noqa: BLE001
                            if isinstance(exc, WorkflowAbort):
                                logger.info(
                                    f"属性单元任务被无人值守中止: "
                                    f"{unit}/{script.id}: {exc}")
                            else:
                                logger.exception(
                                    f"属性单元任务失败: {unit}/{script.id}")
                            result_path = None
                            try:
                                result_path = self._save_result(
                                    username, script, {
                                        "error": str(exc),
                                        "exception_type": type(exc).__name__,
                                    }, task_started_at)
                            except Exception as output_exc:  # noqa: BLE001
                                logger.warning(f"批量失败结果保存失败: {output_exc}")
                            entry["scripts"][script.id] = ST_FAILED
                            report.end_script(ST_FAILED)
                            self.progress.emit(
                                round_number, run_idx, label, script.id, ST_FAILED)
                            self._log(f"[批量] {label} → {script.name} 失败: {exc}", "error")
                            if task_run is not None:
                                try:
                                    task_run.finish(
                                        status="failed", result_path=result_path,
                                        error_message=str(exc))
                                except Exception as history_exc:  # noqa: BLE001
                                    logger.warning(
                                        f"任务历史收尾失败，继续批量任务: {history_exc}")
                            self._recover_unattended(
                                run_idx, username, batch_state,
                                pending=script_idx + 1 < len(self._scripts))
                    if self._stopped or self._aborted:
                        report.finish_pending()
                    finished = self._run_stage(
                        "finish_item", self._spec.workflows.finish_item,
                        run_idx, username, batch_state,
                        {"prepare": prepared.status,
                         "scripts": {key: self._ui_status_to_result(value)
                                     for key, value in entry["scripts"].items()}},
                        round_number=round_number,
                    )
                    batch_state = finished.state if finished.state is not None else batch_state
                    entry["finish"] = self._result_to_ui_status(finished.status)
                    report.record_finish(entry["finish"])
                    if finished.status == RESULT_STOPPED:
                        self._stopped = True
                    elif finished.status != RESULT_SUCCESS:
                        self._log(self._stage_message(f"{label} 条目收尾", finished))
                    counts[unit] += 1
                    if counts[unit] >= self._spec.rounds:
                        done.add(unit)
            except AccessDeniedError as exc:
                reason = self._describe_user_conflict(entry.get("username", ""), exc)
                self._log(f"[批量] {unit} 暂不可执行: {reason}")
                entry["prepare"] = ST_SKIPPED
                entry["skip_reason"] = reason
                report.record_prepare(ST_SKIPPED)
                report.record_skip_reason(reason)
                deferrals[unit] += 1
                next_ready[unit] = time.monotonic() + 60
                if deferrals[unit] >= 30:
                    self._log(f"[批量] {unit} 连续 30 次无法取得用户锁，本次停止调度该单元", "warning")
                    for script in self._scripts:
                        entry["scripts"][script.id] = ST_SKIPPED
                        self.progress.emit(
                            round_number, run_idx, label, script.id, ST_SKIPPED)
                        report.start_script(script.id, script.name)
                        report.end_script(ST_SKIPPED)
                    done.add(unit)
            except Exception as exc:  # noqa: BLE001
                logger.exception(f"属性单元执行失败: {unit}")
                self._log(f"[批量] {unit} 执行失败: {exc}")
                entry["error"] = str(exc)
                report.record_error(str(exc))
                done.add(unit)
            finally:
                report.end_entry()

        self._stopped = self._stopped or self._stop_check()
        summary["stopped"] = self._stopped
        if setup.status == RESULT_SUCCESS:
            teardown = self._run_stage(
                "batch_teardown", self._spec.workflows.batch_teardown,
                -1, "", batch_state,
                {"stopped": self._stopped, "entries": summary["entries"]},
                round_number=self._spec.rounds)
            summary["lifecycle"]["batch_teardown"] = teardown.status
        report.end_batch(stopped=self._stopped)
        report_path = None
        try:
            report_path = report.write()
        except Exception as exc:  # noqa: BLE001
            logger.error(f"批量报告写入失败: {exc}")
            self._log(f"[批量] 报告写入失败（不影响执行结果）: {exc}", "warning")
        if batch_run is not None:
            failed = (
                any(value == RESULT_FAILED
                    for value in summary["lifecycle"].values())
                or any(entry["prepare"] == ST_FAILED
                       or entry["finish"] == ST_FAILED
                       or ST_FAILED in entry["scripts"].values()
                       or entry.get("error")
                       for entry in summary["entries"].values())
            )
            try:
                batch_run.finish(
                    status=("channel_fault" if self._aborted
                            else "interrupted" if self._stopped
                            else "failed" if failed
                            else "completed"),
                    report_path=report_path,
                    error_message=self._channel_fault if self._aborted else "")
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"批量历史收尾失败，继续退出批量任务: {exc}")
        self.finished_all.emit(summary)


    def _log(self, message: str, level: str = "info") -> None:
        """批量逐项消息：进批量页滚动区，同时写主日志。

        这些消息过去只 emit 到界面控件，任务结束后主日志里只剩「自动化结束」
        一行，事后无法追溯是哪个条目、第几次尝试、为什么被跳过或失败。
        级别由调用方显式给出，不让日志层按关键字猜。
        """
        try:
            getattr(logger, level, logger.info)(str(message))
        except Exception:  # noqa: BLE001 - 记日志失败不能影响批量执行
            pass
        self.log.emit(message)


    def _create_engine(self) -> WorkflowEngine:
        """创建新引擎（共享后端引用，每个脚本独立实例）"""
        ctx = self._ctx
        engine = DeviceWorkflowEngineBuilder(
            capture=ctx.capture,  # type: ignore[arg-type]
            ocr=ctx.ocr,  # type: ignore[arg-type]
            input_ctrl=ctx.input_ctrl,  # type: ignore[arg-type]
            layout=ctx.layout,  # type: ignore[arg-type]
            run_env=ctx.run_env,
            input_sim=ctx.input_sim,  # type: ignore[arg-type]
            delay_params=ctx.delay_params,
            android_apps=ctx.android_apps,
            android_device=ctx.android_device,
            window_left=ctx.window_left,
            window_top=ctx.window_top,
            stop_check=self._task_stop_check,
            pause_event=ctx.pause_event,
            unattended=self._unattended_active(),
        ).build()
        # 生命周期与条目脚本必须复用宿主的主线程 UI broker；否则
        # pause/confirm 会退化为无法被 F10 关闭的系统原生阻塞框。
        engine._ui_callback = (
            self._unattended_ui_callback if self._unattended_active()
            else ctx.ui_callback)
        from types import SimpleNamespace
        engine.execution_target_snapshot = SimpleNamespace(
            id=ctx.target_id, kind=ctx.target_kind, display_name=ctx.target_label)
        engine.window_rebind_hook = ctx.window_rebind_hook
        return engine


    @staticmethod
    def _save_result(
        role: str, script: BatchScript, result: dict,
        started_at: datetime | None = None,
    ):
        """结果落盘 output/{role}/YYYY-MM/DD/，日期取任务启动时间。"""
        if not isinstance(result, (dict, list)):
            return None

        def _ser(obj):
            if isinstance(obj, list):
                return [_ser(i) for i in obj]
            if isinstance(obj, dict):
                return {k: _ser(v) for k, v in obj.items()}
            if hasattr(obj, "to_dict"):
                return obj.to_dict()
            return obj

        started_at = started_at or datetime.now()
        path = task_result_path(role, script.id, started_at)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(_ser(result), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.info(f"批量结果已保存: {path}")
        return path
