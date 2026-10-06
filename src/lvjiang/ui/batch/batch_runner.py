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
import time
import traceback
from contextlib import ExitStack, nullcontext
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Callable

from loguru import logger
from PyQt6.QtCore import QThread, pyqtSignal

from ...core.batch_config import (
    BatchConfigItem,
    BatchWorkflows,
    lifecycle_parameter_definitions,
)
from ...core.batch_run import BatchRunDraft
from ...core.config.resolver import get_resolver
from ...core.config.users import SessionManager
from ...core.fs_util import dated_output_dir
from ...i18n import tr
from ...workflows.engine import DeviceWorkflowEngineBuilder, WorkflowEngine
from ...workflows.errors import WorkflowAbort
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
    env: list[str] | None = None  # 脚本级可运行环境，启动前二次校验
    batch_check: str = ""  # 批量执行前调用的 wf 内部子过程；空表示直接执行


@dataclass(frozen=True)
class PlannedTask:
    """批量启动时冻结的一个“用户 × 脚本”执行项。"""
    run_idx: int
    username: str
    script: BatchScript
    params: dict
    parameter_source: str


@dataclass(frozen=True)
class BatchRunSpec:
    """点下「开始」那一刻冻结的一次批量计划。

    配置组定义 + 运行草稿 → 这份快照。调度器只认它：运行期不再读 `batch.json`、
    不再读 session、也不再看主页面控件。所以启动之后去改配置组、改用户资料或
    在主页面重新勾选，都不会影响正在跑的这一批。

    `unattended` 存的是**有效值**：草稿勾了无人值守、且配置组确实配了异常恢复
    wf，才会是真。这个与运算只在构造快照时做一次，调度器不必再判断前提。
    """

    group_id: str = ""
    name: str = ""
    #: 本次执行顺序：用户模式是用户名，属性单元模式是属性值
    entries: tuple[str, ...] = ()
    #: 属性单元模式下参与分组的全部可见用户；用户模式与 entries 相同
    candidate_usernames: tuple[str, ...] = ()
    scripts: tuple[BatchScript, ...] = ()
    execution_unit_key: str = "user"
    rounds: int = 1
    workflows: BatchWorkflows = field(default_factory=BatchWorkflows)
    workflow_params: dict[str, dict] = field(default_factory=dict)
    skip_lifecycle_for_single_item: bool = True
    unattended: bool = False

    @staticmethod
    def build(
        item: BatchConfigItem,
        draft: BatchRunDraft,
        *,
        entries: list[str],
        scripts: list[BatchScript],
        candidate_usernames: list[str] | None = None,
    ) -> "BatchRunSpec":
        return BatchRunSpec(
            group_id=item.id,
            name=item.name,
            entries=tuple(entries),
            candidate_usernames=tuple(
                entries if candidate_usernames is None else candidate_usernames),
            scripts=tuple(copy.deepcopy(scripts)),
            execution_unit_key=item.execution_unit_key,
            rounds=draft.rounds,
            workflows=copy.deepcopy(item.workflows),
            workflow_params=copy.deepcopy(item.workflow_params),
            skip_lifecycle_for_single_item=item.skip_lifecycle_for_single_item,
            # 无人值守必须配套恢复 wf，否则改道弹窗只会让整批在错误页面上连环失败
            unattended=bool(
                draft.unattended and item.workflows.recover_unattended),
        )


@dataclass
class BatchContext:
    """引擎创建所需的后端上下文（由 host 在启动时注入）"""
    capture: object
    ocr: object
    input_ctrl: object
    layout: object
    target_id: str = ""
    target_kind: str = ""
    target_label: str = ""
    task_run_id: str = ""
    input_kind: str = ""
    layout_name: str = ""
    run_env: str = ""
    input_sim: object | None = None
    delay_params: dict | None = None
    android_apps: dict | None = None
    android_device: object | None = None
    window_left: int = 0
    window_top: int = 0
    pause_event: object = None  # threading.Event | None
    ui_callback: Callable[..., object] | None = None
    # 条目准备重启客户端后，宿主据此把定位状态跟到新窗口。
    window_rebind_hook: Callable[[dict], None] | None = None
    # 用户锁冲突时，宿主据此说明是哪个目标的哪个运行实例占着这个用户。
    # 用户锁本身是进程级的，不知道目标，所以必须由持有 RunManager 的宿主回答。
    user_conflict_describer: Callable[[str], str] | None = None


@dataclass(frozen=True)
class BatchStageResult:
    """生命周期 wf 的通用返回结果。state 内容对控制层完全透明。"""
    status: str = RESULT_SUCCESS
    message: str = ""
    state: dict | None = None
    username: str = ""
    #: wf 是否执行过顶层 return。跑到末尾一次 return 都没走时为 False——
    #: 对「只做事不返回」的阶段无所谓，但属性单元的条目准备必须回传 username，
    #: 这时要能把「没返回」和「返回了但内容不对」分开报。
    returned: bool = True
    #: 实际加载的 wf 路径。config/local 与 config/remote 会顶替 system，
    #: 报错时带上它，本地覆盖这类问题一眼可见，不用去翻编辑器。
    source: str = ""


@dataclass(frozen=True)
class BatchCheckResult:
    """业务 wf 的批量可执行性判定结果。"""
    status: str = RESULT_SUCCESS
    message: str = ""


class UnattendedInterrupt(WorkflowAbort):
    """无人值守模式下工作流通过 pause 声明异常阻断。

    pause 经由 ``engine._ui_callback`` 走到宿主，所以
    无人值守只需要在这一处改道，不必去改 wf 里几十个调用点。抛出后当前任务
    按异常记失败、不重试，调度器随即调用配置的恢复 wf 把游戏收回公共初始页，
    再继续下一个任务。
    """


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
    selected_task_plan = pyqtSignal(int, object)
    log = pyqtSignal(str)
    finished_all = pyqtSignal(dict)

    def __init__(
        self,
        spec: BatchRunSpec,
        ctx: BatchContext,
        session_manager: SessionManager,
        stop_check: Callable[[], bool],
        parent=None,
    ):
        super().__init__(parent)
        self._spec = spec
        self._usernames = list(spec.entries)
        self._candidate_usernames = list(spec.candidate_usernames)
        self._scripts = copy.deepcopy(list(spec.scripts))
        self._ctx = ctx
        self._session_manager = session_manager
        self._stop_check = stop_check
        self._stopped = False
        self._task_plan: dict[tuple[int, str], PlannedTask] = {}
        self._member_task_plan: dict[tuple[str, str], PlannedTask] = {}
        self._unit_members: dict[str, list[str]] = {}
        self._user_attributes: dict[str, dict[str, str]] = {}
        self._workflow_configs: dict[str, dict] = {}
        self._lifecycle_params: dict[str, dict] = {}
        #: 最近一次无人值守中止的弹窗文本；恢复后清空（见 _recover_unattended）。
        self._unattended_hit = ""
        self._build_execution_plan()

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
        from ...core.config.wf_configs import get_wf_config
        from ...core.task_params import merge_task_params
        from ...core.user_config import load_user_metadata

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

    # ─── 主循环 ─────────────────────────────────────────

    def run(self):
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
        if self._spec.execution_unit_key != "user":
            self._run_locked_by_attr()
            return
        from ...core.daily_history import try_create_batch_run
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
        use_lifecycle = not (
            len(self._usernames) == 1
            and self._spec.skip_lifecycle_for_single_item
        )
        self.log.emit(f"[批量] 开始：{len(self._usernames)} 用户 × "
                      f"{len(self._scripts)} 脚本 × {rounds} 轮")
        self.log.emit("[批量] 执行用户：" + "、".join(self._usernames))
        self.log.emit("[批量] 执行任务：" + "、".join(
            script.name for script in self._scripts))
        if not use_lifecycle:
            self.log.emit("[批量] 单用户直通：跳过批量生命周期工作流")
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
            config_name=self._spec.name,
            scripts=[(s.id, s.name) for s in self._scripts],
            workflows=(self._spec.workflows.to_dict()
                       if use_lifecycle else {}),
            total_rows=total,
            batch_run_id=batch_run_id,
        )
        report.start_batch()

        batch_state: dict = {}
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
                            run_idx, label, script.id, ST_SKIPPED)
                    self.log.emit(f"[批量] {label} 跳过: {reason}")
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
                    self.progress.emit(run_idx, label, script.id, ui_status)
                    report.start_script(script.id, script.name)
                    report.end_script(ui_status)
                    reason = checked.message or checked.status
                    self.log.emit(
                        f"[批量] {label} → {script.name} "
                        f"{'跳过' if checked.status == RESULT_SKIPPED else '检查失败'}: "
                        f"{reason}")
                report.record_prepare(ST_SKIPPED)
                report.end_entry()
                self.log.emit(f"[批量] {label} 没有可执行任务，忽略该用户")
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
                        self.progress.emit(run_idx, label, s.id, ST_SKIPPED)
                    self.log.emit(self._stage_message(label, prepared))
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

                self.progress.emit(run_idx, label, script.id, ST_RUNNING)
                self.log.emit(f"[批量] {label} → {script.name} ...")
                report.start_script(script.id, script.name)
                planned = self._task_plan[(run_idx, script.id)]
                params = copy.deepcopy(planned.params)
                from ...core.daily_history import try_create_task_run
                task_started_at = datetime.now().astimezone()
                task_run = try_create_task_run(
                    username=username or "unknown", task_id=script.id,
                    task_name=script.name, task_scope=script.scope,
                    params=params, source="batch", batch_run_id=batch_run_id,
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
                            username or "unknown", script, result, task_started_at)
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
                    self.progress.emit(run_idx, label, script.id, ST_FAILED)
                    self.log.emit(f"[批量] {label} → {script.name} 失败: {e}")
                    report.end_script(ST_FAILED)
                    self._recover_unattended(
                        run_idx, username, batch_state,
                        pending=script_idx + 1 < len(self._scripts))

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
                "batch_teardown", self._spec.workflows.batch_teardown,
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

    def _run_locked_by_attr(self) -> None:
        """属性单元的运行态调度；旧用户执行路径不受影响。"""
        from ...core.access import AccessDeniedError, acquire_user
        from ...core.daily_history import try_create_batch_run, try_create_task_run

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
            self.log.emit(self._stage_message(tr("批次准备"), setup))
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
                        self.progress.emit(run_idx, label, script.id, ST_SKIPPED)
                        report.start_script(script.id, script.name)
                        report.end_script(ST_SKIPPED)
                    report.end_entry()

        while (len(done) < len(self._usernames)
               and not self._stopped and not self._stop_check()):
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
                self.log.emit(f"[批量] 单元 {unit} 无用户资料，跳过")
                done.add(unit)
                continue
            attempt += 1
            round_number = counts[unit] + 1
            label = f"{unit} · 第 {round_number} 次 · 尝试 {attempt}"
            self.log.emit(f"[批量] {unit_key}={unit}，第 {round_number}/{self._spec.rounds} 次")
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
                        self.log.emit(f"[批量] {unit} 内所有角色均无可执行任务")
                        done.add(unit)
                        for script in self._scripts:
                            statuses = [checks[script.id].status
                                        for checks in checks_by_member.values()]
                            status = (ST_FAILED if RESULT_FAILED in statuses
                                      else ST_SKIPPED)
                            entry["scripts"][script.id] = status
                            self.progress.emit(run_idx, label, script.id, status)
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
                        self.log.emit(self._stage_message(label, prepared))
                        for script in self._scripts:
                            entry["scripts"][script.id] = ST_SKIPPED
                            self.progress.emit(run_idx, label, script.id, ST_SKIPPED)
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
                            self.progress.emit(run_idx, label, script.id, status)
                            report.start_script(script.id, script.name)
                            report.end_script(status)
                            reason = checked.message or checked.status
                            self.log.emit(
                                f"[批量] {label} → {script.name} "
                                f"{'跳过' if status == ST_SKIPPED else '检查失败'}: "
                                f"{reason}")
                            check_run = try_create_task_run(
                                username=username, task_id=script.id,
                                task_name=script.name, task_scope=script.scope,
                                params=copy.deepcopy(planned.params), source="batch",
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
                        self.progress.emit(run_idx, label, script.id, ST_RUNNING)
                        report.start_script(script.id, script.name)
                        task_started_at = datetime.now().astimezone()
                        task_run = try_create_task_run(
                            username=username, task_id=script.id,
                            task_name=script.name, task_scope=script.scope,
                            params=copy.deepcopy(planned.params), source="batch",
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
                            entry["scripts"][script.id] = ST_SUCCESS
                            report.end_script(ST_SUCCESS, result)
                            self.progress.emit(run_idx, label, script.id, ST_SUCCESS)
                            self.log.emit(f"[批量] {label} → {script.name} 完成")
                            try:
                                result_path = self._save_result(
                                    username, script, result, task_started_at)
                            except Exception as output_exc:  # noqa: BLE001
                                result_path = None
                                logger.warning(
                                    f"批量结果保存失败，继续任务收尾: {output_exc}")
                                self.log.emit(
                                    f"[批量] {label} → {script.name} "
                                    f"结果 JSON 保存失败: {output_exc}")
                            if task_run is not None:
                                try:
                                    task_run.finish(
                                        status=("interrupted" if self._stop_check()
                                                else "completed"),
                                        result_path=result_path)
                                except Exception as history_exc:  # noqa: BLE001
                                    logger.warning(
                                        f"任务历史收尾失败，继续批量任务: {history_exc}")
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
                            self.progress.emit(run_idx, label, script.id, ST_FAILED)
                            self.log.emit(f"[批量] {label} → {script.name} 失败: {exc}")
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
                    if self._stopped:
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
                        self.log.emit(self._stage_message(f"{label} 条目收尾", finished))
                    counts[unit] += 1
                    if counts[unit] >= self._spec.rounds:
                        done.add(unit)
            except AccessDeniedError as exc:
                reason = self._describe_user_conflict(entry.get("username", ""), exc)
                self.log.emit(f"[批量] {unit} 暂不可执行: {reason}")
                entry["prepare"] = ST_SKIPPED
                entry["skip_reason"] = reason
                report.record_prepare(ST_SKIPPED)
                report.record_skip_reason(reason)
                deferrals[unit] += 1
                next_ready[unit] = time.monotonic() + 60
                if deferrals[unit] >= 30:
                    self.log.emit(f"[批量] {unit} 连续 30 次无法取得用户锁，本次停止调度该单元")
                    for script in self._scripts:
                        entry["scripts"][script.id] = ST_SKIPPED
                        self.progress.emit(run_idx, label, script.id, ST_SKIPPED)
                        report.start_script(script.id, script.name)
                        report.end_script(ST_SKIPPED)
                    done.add(unit)
            except Exception as exc:  # noqa: BLE001
                logger.exception(f"属性单元执行失败: {unit}")
                self.log.emit(f"[批量] {unit} 执行失败: {exc}")
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
            self.log.emit(f"[批量] 报告写入失败（不影响执行结果）: {exc}")
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
                    status="interrupted" if self._stopped else "failed" if failed
                    else "completed", report_path=report_path)
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"批量历史收尾失败，继续退出批量任务: {exc}")
        self.finished_all.emit(summary)

    def _unit_prepare_protocol_error(
        self, prepared: BatchStageResult, eligible: list[str],
    ) -> str:
        """属性单元条目准备违反协议时的报文。

        「wf 一次 return 都没走」和「返回了但 username 不对」是两种完全不同的
        排查方向，混成一句话会把人带到错误的地方；实际加载路径一并带上，因为
        config/local 与 config/remote 会顶替 system，用户自己改过的那份会永久生效。
        """
        wf_name = self._spec.workflows.prepare_item
        where = f"（实际加载 {prepared.source}）" if prepared.source else ""
        if not prepared.returned:
            return (
                f"条目准备工作流 {wf_name!r}{where} 没有返回任何值：属性单元要求它"
                f"返回 {{status, state, username}}。请检查该 wf 是否有顶层 return，"
                f"以及是否被 config/local 或 config/remote 顶替成了旧版本")
        return (
            f"条目准备工作流 {wf_name!r}{where} 未返回本单元可执行的用户名"
            f"（得到 {prepared.username!r}）：属性单元必须由它选定成员并回传"
            f" username，可执行成员为 {eligible}")

    # ─── 生命周期协议 ────────────────────────────────────

    @staticmethod
    def _normalize_stage_result(value, current_state: dict) -> BatchStageResult:
        """校验 wf 返回协议；不解析 state 内任何业务字段。"""
        if value is None:
            return BatchStageResult(state=current_state, returned=False)
        if not isinstance(value, dict):
            return BatchStageResult(
                status=RESULT_FAILED,
                message=tr("生命周期 wf 必须返回 dict 或 null"),
                state=current_state,
            )
        status = value.get("status", RESULT_SUCCESS)
        message = value.get("message", "")
        state = value.get("state", current_state)
        username = value.get("username", "")
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
        if not isinstance(username, str):
            return BatchStageResult(
                status=RESULT_FAILED, message=tr("生命周期 wf 返回了无效角色"),
                state=current_state)
        return BatchStageResult(
            status=status, message=message, state=state,
            username=username)

    @staticmethod
    def _normalize_check_result(value) -> BatchCheckResult:
        """校验业务 wf 的批量可执行性返回协议。"""
        if not isinstance(value, dict):
            return BatchCheckResult(
                status=RESULT_FAILED,
                message=tr("批量可执行性子过程必须返回 dict"),
            )
        status = value.get("status", RESULT_SUCCESS)
        message = value.get("message", "")
        if status not in {RESULT_SUCCESS, RESULT_SKIPPED}:
            return BatchCheckResult(
                status=RESULT_FAILED,
                message=f"批量可执行性子过程返回了未知 status: {status!r}",
            )
        if not isinstance(message, str):
            message = str(message)
        return BatchCheckResult(status=status, message=message)

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

    def _describe_user_conflict(self, username: str, exc: Exception) -> str:
        """把用户锁冲突补成「哪个用户 · 哪个目标 · 哪个运行实例」。

        用户锁是进程级的，本身不知道目标，所以具体占用情况必须问宿主的
        RunManager。拿不到时退回异常原文，绝不编一个目标名出来。
        """
        detail = str(exc)
        describer = self._ctx.user_conflict_describer
        if describer is None or not username:
            return detail
        try:
            extra = describer(username)
        except Exception as describe_exc:  # noqa: BLE001
            logger.debug(f"查询用户锁占用情况失败: {describe_exc}")
            return detail
        return f"{detail}（{extra}）" if extra else detail

    @staticmethod
    def _stage_message(label: str, result: BatchStageResult) -> str:
        text = result.message or result.status
        return f"[批量] {label}: {text}"

    # ─── 内部方法 ───────────────────────────────────────

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
            stop_check=self._stop_check,
            pause_event=ctx.pause_event,
        ).build()
        # 生命周期与条目脚本必须复用宿主的主线程 UI broker；否则
        # pause/confirm 会退化为无法被 F10 关闭的系统原生阻塞框。
        engine._ui_callback = (
            self._unattended_ui_callback if self._unattended_active()
            else ctx.ui_callback)
        engine.window_rebind_hook = ctx.window_rebind_hook
        return engine

    def _unattended_active(self) -> bool:
        """无人值守是否真的生效：必须同时配好恢复 wf。

        少了恢复 wf 就改道 pause 只会把整批推到错误页面上连环失败，
        不如照旧弹窗等人。配置层已经拦了一道，这里不依赖它。
        """
        return bool(self._spec.unattended
                    and self._spec.workflows.recover_unattended)

    def _unattended_ui_callback(self, kind: str, **kwargs) -> object:
        """Only pause declares an abnormal interruption; forward other UI requests."""
        if kind != "pause":
            callback = self._ctx.ui_callback
            return callback(kind, **kwargs) if callback is not None else None
        text = str(kwargs.get("message") or kind)
        self._unattended_hit = text
        raise UnattendedInterrupt(text)

    def _recover_unattended(self, run_idx: int, username: str,
                            batch_state: dict, *, pending: bool) -> None:
        """无人值守中止后把游戏收回公共初始页（登录页 - 登录主页视图）。

        所有批量任务都从这个视图起步，所以恢复目标只有这一个。具体怎么收——
        先看是不是已经在登录主页、启动页点返回、还是强制重启客户端——交给
        配置的恢复 wf：重启是框架能力，不该写进某个业务任务里。

        只在确实撞了弹窗时动作：任务因别的原因失败时画面通常还在可用状态，
        没有理由顺手重启一次客户端。

        ``pending`` 为真表示本条目还有未执行的任务，恢复 wf 据此决定要不要
        继续登录回游戏主页。
        """
        if not self._unattended_hit:
            return
        text, self._unattended_hit = self._unattended_hit, ""
        wf_name = self._spec.workflows.recover_unattended
        if not wf_name:
            self.log.emit(
                f"[批量] 无人值守中止（{text}）；未配置恢复 wf，直接继续下一个任务")
            return
        self.log.emit(f"[批量] 无人值守中止（{text}）；执行恢复 wf 收回登录主页")
        result = self._run_stage(
            "recover_unattended", wf_name, run_idx, username, batch_state,
            extra_variables={
                # 本条目还有没跑的任务时，恢复流程不止回登录主页，还要用当前
                # 角色重新登录到游戏主页，调度器才能接着跑下一项；全跑完了就
                # 停在登录主页等下一个用户。
                "batch_recover_pending": bool(pending),
                "batch_recover_username": username,
            })
        self._unattended_hit = ""
        if result.status != RESULT_SUCCESS:
            self.log.emit(f"[批量] 无人值守恢复未成功：{result.message}")

    def _run_stage(
        self,
        phase: str,
        wf_name: str,
        run_idx: int,
        username: str,
        batch_state: dict,
        item_result: dict | None = None,
        round_number: int = 0,
        unit_members: list[str] | None = None,
        extra_variables: dict | None = None,
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
        engine.run_username = "" if unit_members is not None else username
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
            "batch_rounds": self._spec.rounds,
            "batch_state": working_state,
            "batch_item_result": item_result or {},
        })
        if extra_variables:
            variables.update(copy.deepcopy(extra_variables))
        if unit_members is not None:
            variables.update({
                "batch_unit_key": self._spec.execution_unit_key,
                "batch_unit_value": self._usernames[run_idx],
                "batch_unit_members": [
                    {"username": name, "attributes": copy.deepcopy(
                        self._user_attributes.get(name, {}))}
                    for name in unit_members
                ],
            })

        try:
            engine.execute(wf_path, initial_variables=variables)
            result = self._normalize_stage_result(engine.return_value, batch_state)
            if not result.returned and self._stop_check():
                # 停止请求让 _exec_body 提前 return，wf 没机会走到它的 return；
                # 这是用户按了停止，不是 wf 违反返回协议，不能报成协议错误。
                result = replace(
                    result, status=RESULT_STOPPED,
                    message=tr("执行被停止"))
            return replace(result, source=str(wf_path))
        except WorkflowAbort as e:
            # 无人值守改道，不是阶段 wf 出错。按失败记账（这条目不重试，直接
            # 跳到下一个），但日志级别和文案都不该把预期分支说成异常。
            logger.info(
                f"批量阶段被无人值守中止 ({phase}, "
                f"{username or 'batch'}): {e}")
            return BatchStageResult(
                status=RESULT_FAILED,
                message=f"无人值守中止: {e}",
                state=batch_state,
            )
        except Exception as e:
            logger.error(
                f"批量阶段失败 ({phase}, "
                f"{username or 'batch'}): {e}")
            return BatchStageResult(
                status=RESULT_FAILED,
                message=f"工作流异常: {e}",
                state=batch_state,
            )

    def _check_script(
        self,
        script: BatchScript,
        username: str,
        *,
        params: dict,
    ) -> BatchCheckResult:
        """调用业务 wf 元数据声明的批量可执行性子过程。

        只加载 def/import，不执行 wf 顶层正文。未声明 ``batch_check`` 的任务
        保持原行为；声明错误或检查异常按该任务检查失败处理，避免带病执行。
        """
        if not script.batch_check:
            return BatchCheckResult()
        if not script.wf_file or script.class_name:
            return BatchCheckResult(
                status=RESULT_FAILED,
                message=tr("批量可执行性检查只支持 DSL 工作流"),
            )

        from ...workflows.discovery import resolve_workflow_path
        wf_path, _resolved = resolve_workflow_path(script.wf_file, script.id)
        if wf_path is None:
            return BatchCheckResult(
                status=RESULT_FAILED,
                message=f"工作流文件不存在: {script.wf_file}",
            )

        engine = self._create_engine()
        engine.session = {}
        engine.run_username = username
        engine.users_dir = self._session_manager._users_dir
        engine.user_attributes_snapshot = copy.deepcopy(self._user_attributes)
        engine.workflow_config_snapshot = copy.deepcopy(
            self._workflow_configs.get(script.id, {}))
        try:
            engine.load_subcalls(wf_path)
            value = engine.call_subcall(
                script.batch_check, [copy.deepcopy(params)])
            return self._normalize_check_result(value)
        except Exception as exc:  # noqa: BLE001 — 单个任务的检查不得拖垮整批
            logger.error(
                f"批量可执行性检查失败 ({username}/{script.id}): {exc}")
            return BatchCheckResult(
                status=RESULT_FAILED,
                message=f"可执行性检查异常: {exc}",
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
    def _save_result(
        role: str, script: BatchScript, result: dict,
        started_at: datetime | None = None,
    ):
        """结果落盘 output/{role}/YYYY-MM/DD/，日期取任务启动时间。"""
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

        started_at = started_at or datetime.now()
        user_dir = dated_output_dir(OUTPUT_DIR / role, started_at)
        user_dir.mkdir(parents=True, exist_ok=True)
        ts = started_at.strftime("%Y%m%d_%H%M%S_%f")
        path = user_dir / f"{script.id}_{ts}.json"
        path.write_text(
            json.dumps(_ser(result), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.info(f"批量结果已保存: {path}")
        return path
