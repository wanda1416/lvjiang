"""批量执行编排器

通用批处理执行器：按批次/条目生命周期调用 wf。
控制层只解释标准结果 status/message/state，不理解 state 内的业务字段。

线程模型：单个 BatchWorker(QThread) 串行执行，与主窗口
“同一时刻仅一个自动化”约束一致。

批量配置和 BatchScript 不携带用户参数值。BatchWorker 构造时根据
“任务 ID + 用户”生成全部执行参数快照，运行过程中不再回读配置。
"""

from __future__ import annotations

import copy
import json
import traceback
from contextlib import ExitStack, nullcontext
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from loguru import logger
from PyQt6.QtCore import QThread, pyqtSignal

from ...core.batch_config import BatchConfigItem, lifecycle_parameter_definitions
from ...core.config.resolver import get_resolver
from ...core.config.users import SessionManager
from ...i18n import tr
from ...workflows.engine import WorkflowEngine
from .batch_report import BatchReport

# 进度状态常量
ST_PENDING = tr("待执行")
ST_RUNNING = tr("运行中")
ST_SUCCESS = tr("成功")
ST_FAILED = tr("失败")
ST_SKIPPED = tr("跳过")

RESULT_SUCCESS = "success"
RESULT_SKIPPED = "skipped"
RESULT_FAILED = "failed"
RESULT_STOPPED = "stopped"
_RESULT_STATUSES = {
    RESULT_SUCCESS, RESULT_SKIPPED, RESULT_FAILED, RESULT_STOPPED,
}


@dataclass
class BatchScript:
    """批量执行中的脚本描述

    不含任何参数值；parameters 只是脚本元数据中的参数定义。
    """
    id: str
    name: str
    wf_file: str = ""       # DSL 工作流文件（相对 workflows/ 或绝对路径）
    class_name: str = ""    # Python 类实现（与 wf_file 二选一）
    scope: str = "daily"    # 任务定义性质；daily / dedicated 都进入历史
    parameters: list[dict] | None = None  # 仅参数定义，不携带任何参数值


@dataclass(frozen=True)
class PlannedTask:
    """批量启动时冻结的一个“用户 × 脚本”执行项。"""
    run_idx: int
    username: str
    script: BatchScript
    params: dict
    parameter_source: str


@dataclass
class BatchContext:
    """引擎创建所需的后端上下文（由 host 在启动时注入）"""
    capture: object
    ocr: object
    input_ctrl: object
    layout: object
    run_env: str = ""
    input_sim: object | None = None
    delay_params: dict | None = None
    android_apps: dict | None = None
    android_device: object | None = None
    window_left: int = 0
    window_top: int = 0
    pause_event: object = None  # threading.Event | None
    ui_callback: Callable[..., object] | None = None


@dataclass(frozen=True)
class BatchStageResult:
    """生命周期 wf 的通用返回结果。state 内容对控制层完全透明。"""
    status: str = RESULT_SUCCESS
    message: str = ""
    state: dict | None = None


class BatchWorker(QThread):
    """批量执行工作线程

    Signals:
        progress(entry_label, script_id, status): 进度更新
        log(str): 日志行（由 host 转发到日志面板）
        finished_all(dict): 全部结束，携带汇总
    """
    # (run_idx, 条目标签, script_id, 状态)：run_idx 是用户名序列中的
    # 位置，进度表按它直接定位，不靠标签文本匹配（标签会重名）。
    progress = pyqtSignal(int, str, str, str)
    log = pyqtSignal(str)
    finished_all = pyqtSignal(dict)

    def __init__(
        self,
        usernames: list[str],
        scripts: list[BatchScript],
        config: BatchConfigItem,
        ctx: BatchContext,
        session_manager: SessionManager,
        stop_check: Callable[[], bool],
        parent=None,
    ):
        super().__init__(parent)
        self._usernames = list(usernames)
        self._scripts = copy.deepcopy(scripts)
        self._config = copy.deepcopy(config)
        self._ctx = ctx
        self._session_manager = session_manager
        self._stop_check = stop_check
        self._stopped = False
        self._task_plan: dict[tuple[int, str], PlannedTask] = {}
        self._user_attributes: dict[str, dict[str, str]] = {}
        self._workflow_configs: dict[str, dict] = {}
        self._lifecycle_params: dict[str, dict] = {}
        self._build_execution_plan()

    def _build_execution_plan(self) -> None:
        """在工作线程启动前冻结全部任务参数和用户配置。"""
        from ...core.config.wf_configs import get_wf_config
        from ...core.task_params import merge_task_params
        from ...core.user_config import load_user_metadata

        shared = {
            script.id: get_wf_config(script.id)
            for script in self._scripts
        }
        self._workflow_configs = copy.deepcopy(shared)
        users = {}
        for username in self._usernames:
            user = load_user_metadata(username, self._session_manager._users_dir)
            users[username] = user
            self._user_attributes[username] = (
                dict(user.attributes) if user is not None else {})
        for run_idx, username in enumerate(self._usernames):
            user = users.get(username)
            for script in self._scripts:
                override = (
                    user.workflow_params.get(script.id)
                    if user is not None and script.id in user.workflow_params
                    else None)
                params, source = merge_task_params(
                    script.parameters or [], shared[script.id], override)
                self._task_plan[(run_idx, script.id)] = PlannedTask(
                    run_idx, username, script, params, source)
        for phase, definitions in lifecycle_parameter_definitions(
            self._config.workflows,
        ).items():
            params, _source = merge_task_params(
                definitions, self._config.workflow_params.get(phase, {}), None)
            self._lifecycle_params[phase] = params

    def task_plan_snapshot(self) -> dict[tuple[int, str], PlannedTask]:
        """供进度页展示与本轮执行完全一致的参数快照。"""
        return copy.deepcopy(self._task_plan)

    # ─── 主循环 ─────────────────────────────────────────

    def run(self):
        self._execution_lease = None
        self._user_scope = ExitStack()
        self._batch_run = None
        try:
            self._run_locked()
        except Exception as exc:
            logger.exception("批量执行失败")
            self.log.emit(f"[批量] 执行或保存失败: {exc}")
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
        from ...core.daily_history import try_create_batch_run
        batch_run = try_create_batch_run(
            config_name=self._config.name,
            input_snapshot={
                "usernames": list(self._usernames),
                "scripts": [
                    {"task_id": item.id, "task_name": item.name,
                     "scope": item.scope}
                    for item in self._scripts
                ],
                "rounds": self._config.rounds,
                "workflows": self._config.workflows.to_dict(),
                "workflow_params": copy.deepcopy(self._lifecycle_params),
            },
        )
        batch_run_id = batch_run.batch_run_id if batch_run is not None else ""
        self._batch_run = batch_run
        summary: dict = {
            "batch_run_id": batch_run_id,
            "entries": {}, "stopped": False, "lifecycle": {},
        }
        rounds = self._config.rounds
        visits = [
            (round_number, run_idx, username)
            for round_number in range(1, rounds + 1)
            for run_idx, username in enumerate(self._usernames)
        ]
        total = len(visits)
        use_lifecycle = True
        self.log.emit(f"[批量] 开始：{len(self._usernames)} 用户 × "
                      f"{len(self._scripts)} 脚本 × {rounds} 轮")
        self.log.emit("[批量] 执行用户：" + "、".join(self._usernames))
        self.log.emit("[批量] 执行任务：" + "、".join(
            script.name for script in self._scripts))
        for run_idx, username in enumerate(self._usernames):
            for script in self._scripts:
                planned = self._task_plan[(run_idx, script.id)]
                params = json.dumps(planned.params, ensure_ascii=False, sort_keys=True)
                source = "用户独立" if planned.parameter_source == "user" else "全局任务"
                self.log.emit(
                    f"[批量计划] 用户={username} 任务={script.name} "
                    f"参数来源={source} 输入参数={params}"
                )

        # 初始化报告
        report = BatchReport(
            config_name=self._config.name,
            scripts=[(s.id, s.name) for s in self._scripts],
            workflows=(self._config.workflows.to_dict()
                       if use_lifecycle else {}),
            total_rows=total,
        )
        report.start_batch()

        batch_state: dict = {}
        can_run = True
        if use_lifecycle:
            setup = self._run_stage(
                "batch_setup", self._config.workflows.batch_setup,
                -1, "", batch_state, round_number=0,
            )
            batch_state = setup.state if setup.state is not None else batch_state
            summary["lifecycle"]["batch_setup"] = setup.status
            can_run = setup.status == RESULT_SUCCESS
            if not can_run:
                self._stopped = setup.status == RESULT_STOPPED
                self.log.emit(self._stage_message(tr("批次准备"), setup))
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
                        self.progress.emit(run_idx, label, script.id, ST_SKIPPED)

        for visit_index, (round_number, run_idx, username) in enumerate(visits):
            if not can_run:
                break
            if self._stop_check():
                self._stopped = True
                break

            if run_idx == 0:
                self.log.emit(f"[批量] 开始第 {round_number}/{rounds} 轮")
                if round_number > 1:
                    for pending_idx, pending_user in enumerate(self._usernames):
                        for pending_script in self._scripts:
                            self.progress.emit(
                                pending_idx, pending_user,
                                pending_script.id, ST_PENDING,
                            )

            label = (username if rounds == 1 else
                     f"第 {round_number} 轮 · {username}")
            if self._execution_lease is not None:
                self._user_scope.close()
                self._execution_lease.release()
                self._execution_lease = None
            if username:
                from ...core.access import AccessDeniedError, acquire_user
                try:
                    self._execution_lease = acquire_user(
                        username, self._session_manager._users_dir)
                except AccessDeniedError as exc:
                    entry_result = {
                        "prepare": ST_SKIPPED,
                        "finish": ST_SKIPPED,
                        "scripts": {
                            script.id: ST_SKIPPED for script in self._scripts
                        },
                    }
                    summary["entries"][label] = entry_result
                    report.start_entry(label, username)
                    report.record_prepare(ST_SKIPPED)
                    report.end_entry()
                    for script in self._scripts:
                        self.progress.emit(
                            run_idx, label, script.id, ST_SKIPPED)
                    self.log.emit(f"[批量] {label} 跳过: {exc}")
                    continue
                self._user_scope.enter_context(self._execution_lease.authorized())
            self.log.emit(f"[批量] ── [{visit_index + 1}/{total}] {label} ──")
            entry_result: dict = {
                "prepare": ST_SKIPPED,
                "finish": ST_SKIPPED,
                "scripts": {},
            }
            summary["entries"][label] = entry_result

            report.start_entry(label, username)

            prepared = BatchStageResult(state=batch_state)
            if use_lifecycle:
                prepared = self._run_stage(
                    "prepare_item", self._config.workflows.prepare_item,
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
                        self.progress.emit(run_idx, label, s.id, ST_SKIPPED)
                    self.log.emit(self._stage_message(label, prepared))
                    if prepared.status == RESULT_STOPPED:
                        self._stopped = True
                        break
                    continue

            # 2. 批量层显式传递用户：按行加载该用户 session，
            #    不触碰全局 active user，与 UI 下拉框彻底无关
            if username:
                session = self._session_manager.load(username)
            else:
                session = {}

            # 3. 顺序执行脚本
            for script in self._scripts:
                if self._stop_check():
                    self._stopped = True
                    break

                self.progress.emit(run_idx, label, script.id, ST_RUNNING)
                self.log.emit(f"[批量] {label} → {script.name} ...")
                report.start_script(script.id, script.name)
                planned = self._task_plan[(run_idx, script.id)]
                params = copy.deepcopy(planned.params)
                from ...core.daily_history import try_create_task_run
                task_run = try_create_task_run(
                    username=username or "unknown", task_id=script.id,
                    task_name=script.name, task_scope=script.scope,
                    params=params, source="batch", batch_run_id=batch_run_id,
                    repository=(batch_run.repository
                                if batch_run is not None else None),
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
                        except Exception:
                            tb = traceback.format_exc()
                            logger.error(
                                f"批量执行 {label}/{script.name} 异常:\n{tb}")
                            raise
                        if task_run is not None:
                            logger.info(
                                f"任务线程结束: task_run_id={task_run.task_run_id}")
                    entry_result["scripts"][script.id] = ST_SUCCESS
                    self.progress.emit(run_idx, label, script.id, ST_SUCCESS)
                    self.log.emit(f"[批量] {label} → {script.name} 完成")
                    report.end_script(ST_SUCCESS, result)
                    try:
                        result_path = self._save_result(
                            username or "unknown", script, result)
                    except Exception as output_exc:  # noqa: BLE001
                        result_path = None
                        logger.warning(f"批量结果保存失败，继续任务收尾: {output_exc}")
                        self.log.emit(
                            f"[批量] {label} → {script.name} 结果 JSON 保存失败: "
                            f"{output_exc}")
                    if task_run is not None:
                        try:
                            task_run.finish(
                                status=("interrupted" if self._stop_check()
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
                            })
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
                    self.progress.emit(run_idx, label, script.id, ST_FAILED)
                    self.log.emit(f"[批量] {label} → {script.name} 失败: {e}")
                    report.end_script(ST_FAILED)

            # 用户中断时，关闭尚未结束的脚本记录
            if self._stopped:
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
                    "finish_item", self._config.workflows.finish_item,
                    run_idx, username, batch_state, item_summary,
                    round_number=round_number,
                )
                batch_state = (finished.state if finished.state is not None
                               else batch_state)
                finish_ui_status = self._result_to_ui_status(finished.status)
                entry_result["finish"] = finish_ui_status
                report.record_finish(finish_ui_status)
                if finished.status != RESULT_SUCCESS:
                    self.log.emit(self._stage_message(
                        f"{label} 条目收尾", finished))
                if finished.status == RESULT_STOPPED:
                    self._stopped = True

            report.end_entry()

            if self._stopped:
                break

        summary["stopped"] = self._stopped
        tag = tr("（用户中断）") if self._stopped else ""
        self.log.emit(f"[批量] 全部结束{tag}")

        # setup 成功才说明批次现场已经建立，此时才允许执行 teardown。
        # setup 非 success 时直接终止，不启动任何后续生命周期阶段。
        if use_lifecycle and can_run:
            teardown = self._run_stage(
                "batch_teardown", self._config.workflows.batch_teardown,
                -1, "", batch_state,
                {"stopped": self._stopped, "entries": summary["entries"]},
                round_number=rounds,
            )
            summary["lifecycle"]["batch_teardown"] = teardown.status
            if teardown.status != RESULT_SUCCESS:
                self.log.emit(self._stage_message(tr("批次收尾"), teardown))

        # 6. 生成报告
        report.end_batch(stopped=self._stopped)
        report_path = None
        try:
            report_path = report.write()
            if report_path:
                self.log.emit(f"[批量] 报告已保存: {report_path}")
        except Exception as e:
            logger.error(f"批量报告写入失败: {e}")
            self.log.emit(f"[批量] 报告写入失败（不影响执行结果）: {e}")

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
                    status=("interrupted" if self._stopped else
                            "failed" if task_failed or lifecycle_failed else
                            "completed"),
                    report_path=report_path,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"批量历史收尾失败，继续退出批量任务: {exc}")

        self.finished_all.emit(summary)

    # ─── 生命周期协议 ────────────────────────────────────

    @staticmethod
    def _normalize_stage_result(value, current_state: dict) -> BatchStageResult:
        """校验 wf 返回协议；不解析 state 内任何业务字段。"""
        if value is None:
            return BatchStageResult(state=current_state)
        if not isinstance(value, dict):
            return BatchStageResult(
                status=RESULT_FAILED,
                message=tr("生命周期 wf 必须返回 dict 或 null"),
                state=current_state,
            )
        status = value.get("status", RESULT_SUCCESS)
        message = value.get("message", "")
        state = value.get("state", current_state)
        if status not in _RESULT_STATUSES:
            return BatchStageResult(
                status=RESULT_FAILED,
                message=f"生命周期 wf 返回了未知 status: {status!r}",
                state=current_state,
            )
        if not isinstance(message, str):
            message = str(message)
        if not isinstance(state, dict):
            return BatchStageResult(
                status=RESULT_FAILED,
                message=tr("生命周期 wf 的 state 必须是 dict"),
                state=current_state,
            )
        return BatchStageResult(status=status, message=message, state=state)

    @staticmethod
    def _result_to_ui_status(status: str) -> str:
        return {
            RESULT_SUCCESS: ST_SUCCESS,
            RESULT_SKIPPED: ST_SKIPPED,
            RESULT_FAILED: ST_FAILED,
            RESULT_STOPPED: ST_SKIPPED,
        }.get(status, ST_FAILED)

    @staticmethod
    def _ui_status_to_result(status: str) -> str:
        return {
            ST_SUCCESS: RESULT_SUCCESS,
            ST_FAILED: RESULT_FAILED,
            ST_SKIPPED: RESULT_SKIPPED,
            ST_PENDING: "pending",
            ST_RUNNING: "running",
        }.get(status, RESULT_FAILED)

    @staticmethod
    def _stage_message(label: str, result: BatchStageResult) -> str:
        text = result.message or result.status
        return f"[批量] {label}: {text}"

    # ─── 内部方法 ───────────────────────────────────────

    def _create_engine(self) -> WorkflowEngine:
        """创建新引擎（共享后端引用，每个脚本独立实例）"""
        ctx = self._ctx
        engine = WorkflowEngine(
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
            stop_check=self._stop_check,
            pause_event=ctx.pause_event,
        )
        # 生命周期与条目脚本必须复用宿主的主线程 UI broker；否则
        # pause/confirm 会退化为无法被 F10 关闭的系统原生阻塞框。
        engine._ui_callback = ctx.ui_callback
        return engine

    def _run_stage(
        self,
        phase: str,
        wf_name: str,
        run_idx: int,
        username: str,
        batch_state: dict,
        item_result: dict | None = None,
        round_number: int = 0,
    ) -> BatchStageResult:
        """执行一个生命周期 wf，并统一校验其返回协议。"""
        if not wf_name:
            return BatchStageResult(state=batch_state)
        wf_path = get_resolver().resolve_read(f"workflows/{wf_name}")
        if wf_path is None:
            return BatchStageResult(
                status=RESULT_FAILED,
                message=f"工作流不存在: {wf_name}",
                state=batch_state,
            )

        engine = self._create_engine()
        engine.session = {}
        engine.run_username = username
        engine.users_dir = self._session_manager._users_dir
        engine.user_attributes_snapshot = copy.deepcopy(self._user_attributes)

        # 每阶段获得独立工作副本；只有返回协议中的 state 会被调用方提交。
        # 控制层不读取 state 内部的任何业务字段。
        working_state = copy.deepcopy(batch_state)
        variables: dict = copy.deepcopy(self._lifecycle_params.get(phase, {}))
        variables.update({
            "batch_phase": phase,
            "batch_users": list(self._usernames),
            "batch_index": run_idx,
            "batch_round": round_number,
            "batch_rounds": self._config.rounds,
            "batch_state": working_state,
            "batch_item_result": item_result or {},
        })

        try:
            engine.execute(wf_path, initial_variables=variables)
            return self._normalize_stage_result(engine.return_value, batch_state)
        except Exception as e:
            logger.error(
                f"批量阶段失败 ({phase}, "
                f"{username or 'batch'}): {e}")
            return BatchStageResult(
                status=RESULT_FAILED,
                message=f"工作流异常: {e}",
                state=batch_state,
            )

    def _run_script(self, script: BatchScript, session: dict,
                    username: str | None, *, params: dict | None = None) -> dict:
        """执行单个脚本，返回 collect 结果

        参数由批量启动时生成的执行计划传入，运行过程中不回读配置。
        用户信息由批量层显式传递（username）：引擎绑定 run_username 与
        save_fn，全程不读全局 active user。
        """
        engine = self._create_engine()
        engine.session = session
        engine.run_username = username or ""
        engine.users_dir = self._session_manager._users_dir
        engine.user_attributes_snapshot = copy.deepcopy(self._user_attributes)
        engine.workflow_config_snapshot = copy.deepcopy(
            self._workflow_configs.get(script.id, {}))
        if username:
            engine._save_callback = self._session_manager.save_fn(username, session)

        # 正常批量执行总会传入启动时冻结的参数；直接调用时按空参数执行。
        if params is None:
            params = {}

        if script.class_name:
            from ...workflows.implementations import get_workflow_class
            ctx = self._ctx
            wf_class = get_workflow_class(script.class_name)
            wf_instance = wf_class(
                capture=ctx.capture,
                ocr=ctx.ocr,
                input_ctrl=ctx.input_ctrl,
                layout=ctx.layout,
                input_sim=ctx.input_sim,
                delay_params=ctx.delay_params,
                window_left=ctx.window_left,
                window_top=ctx.window_top,
                stop_check=self._stop_check,
                pause_event=ctx.pause_event,
            )
            return engine.execute(wf_instance, initial_variables=params)

        # DSL 工作流。批量页的候选快照可能滞后于脚本发现（升级迁移、
        # 「脚本配置」改动），所以和日常页一样按 id 自愈一次再判缺失。
        from ...workflows.discovery import resolve_workflow_path
        wf_path, _resolved = resolve_workflow_path(script.wf_file, script.id)
        if wf_path is None:
            raise FileNotFoundError(f"工作流文件不存在: {script.wf_file}")
        return engine.execute(wf_path, initial_variables=params)

    @staticmethod
    def _save_result(role: str, script: BatchScript, result: dict):
        """结果落盘 output/{role}/{script_id}_{timestamp}.json"""
        if not isinstance(result, (dict, list)):
            return None
        from ...constants import OUTPUT_DIR

        def _ser(obj):
            if isinstance(obj, list):
                return [_ser(i) for i in obj]
            if isinstance(obj, dict):
                return {k: _ser(v) for k, v in obj.items()}
            if hasattr(obj, "to_dict"):
                return obj.to_dict()
            return obj

        user_dir = OUTPUT_DIR / role
        user_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        path = user_dir / f"{script.id}_{ts}.json"
        path.write_text(
            json.dumps(_ser(result), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.info(f"批量结果已保存: {path}")
        return path
