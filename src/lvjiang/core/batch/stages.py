"""批量执行的生命周期与脚本阶段。

控制层只认标准结果协议：一个生命周期 wf / 业务 wf 的返回值要么符合
``status/message/state`` 形状，要么按协议错误中止整批——这里不解析 state 内的
任何业务字段，业务含义留给脚本自己。

本 mixin 的方法通过 ``self`` 使用编排器提供的状态与回调（引擎创建、消息推送、
结果落盘），不持有自己的连接或缓存。
"""

from __future__ import annotations

import copy
from dataclasses import replace

from loguru import logger

from ...i18n import tr
from ...workflows.errors import WorkflowAbort
from ...workflows.result_protocol import (
    RESULT_FAILED,
    RESULT_SKIPPED,
    RESULT_STOPPED,
    RESULT_SUCCESS,
)
from ..android.recovery import ChannelFaultError
from ..config.resolver import get_root_resolver
from .models import (
    _RESULT_STATUSES,
    ST_FAILED,
    ST_PENDING,
    ST_RUNNING,
    ST_SKIPPED,
    ST_STOPPED,
    ST_SUCCESS,
    BatchCheckResult,
    BatchScript,
    BatchStageResult,
)


class StageExecutionMixin:
    """生命周期阶段与业务脚本的执行、协议校验与状态映射。"""


    def _unit_prepare_protocol_error(
        self, prepared: BatchStageResult, eligible: list[str],
    ) -> str:
        """属性单元条目准备违反协议时的报文。

        「wf 一次 return 都没走」和「返回了但 username 不对」是两种完全不同的
        排查方向，混成一句话会把人带到错误的地方；实际加载路径一并带上，因为
        config/local 会顶替 system，用户自己改过的那份会永久生效。
        """
        wf_name = self._spec.workflows.prepare_item
        where = f"（实际加载 {prepared.source}）" if prepared.source else ""
        if not prepared.returned:
            return (
                f"条目准备工作流 {wf_name!r}{where} 没有返回任何值：属性单元要求它"
                f"返回 {{status, state, username}}。请检查该 wf 是否有顶层 return，"
                f"以及是否被 config/local 顶替成了旧版本")
        return (
            f"条目准备工作流 {wf_name!r}{where} 未返回本单元可执行的用户名"
            f"（得到 {prepared.username!r}）：属性单元必须由它选定成员并回传"
            f" username，可执行成员为 {eligible}")


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
            RESULT_STOPPED: ST_STOPPED,
        }.get(status, ST_FAILED)


    @staticmethod
    def _ui_status_to_result(status: str) -> str:
        return {
            ST_SUCCESS: RESULT_SUCCESS,
            ST_FAILED: RESULT_FAILED,
            ST_SKIPPED: RESULT_SKIPPED,
            ST_STOPPED: RESULT_STOPPED,
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
        wf_path = get_root_resolver().resolve_read(f"workflows/{wf_name}")
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
            self._task_stop_clear()
            engine.execute(wf_path, initial_variables=variables)
            result = self._normalize_stage_result(engine.return_value, batch_state)
            if not result.returned:
                if self._stop_check():
                    # 停止请求让 _exec_body 提前 return，wf 没机会走到它的 return；
                    # 这是用户按了停止，不是 wf 违反返回协议，不能报成协议错误。
                    result = replace(
                        result, status=RESULT_STOPPED,
                        message=tr("执行被停止"))
                elif self._task_stop_check():
                    # 弹窗「停止任务」只结束当前阶段，批次继续下一项。
                    result = replace(
                        result, status=RESULT_SKIPPED,
                        message=tr("用户结束当前任务，继续下一项"))
            return replace(result, source=str(wf_path))
        except ChannelFaultError:
            raise
        except WorkflowAbort as e:
            # 无人值守改道，不是阶段 wf 出错。按失败记账（这条目不重试，直接
            # 跳到下一个），但日志级别和文案都不该把预期分支说成异常。
            logger.info(
                f"批量阶段被无人值守中止 ({phase}, "
                f"{username or 'batch'}): {e}")
            self._unattended_hit = ""
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
            self._dedicated_configs.get((username or "", script.id), self._workflow_configs.get(script.id, {})))
        try:
            self._task_stop_clear()
            engine.load_subcalls(wf_path)
            value = engine.call_subcall(
                script.batch_check, [copy.deepcopy(params)])
            return self._normalize_check_result(value)
        except ChannelFaultError:
            raise
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
        try:
            engine = self._create_engine()
        finally:
            self._diagnostic_task_id = ""
        self._task_stop_clear()
        engine.session = session
        engine.run_username = username or ""
        engine.users_dir = self._session_manager._users_dir
        engine.user_attributes_snapshot = copy.deepcopy(self._user_attributes)
        engine.workflow_config_snapshot = copy.deepcopy(
            self._dedicated_configs.get((username or "", script.id), self._workflow_configs.get(script.id, {})))
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
                stop_check=self._task_stop_check,
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
