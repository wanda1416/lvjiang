"""批量执行编排器

通用批处理执行器：按批次/条目生命周期调用 wf。
控制层只解释标准结果 status/message/state，不理解 state 内的业务字段。

线程模型：单个 BatchWorker(QThread) 串行执行，与主窗口
“同一时刻仅一个自动化”约束一致。

⚠️ 警告：批量引擎不负责传递脚本参数
批量引擎只负责遍历批量上下文 + 传递（任务 ID + 用户）。
脚本参数由单次任务执行时从 wf_configs 按 script.id 加载。
禁止在 BatchScript 中携带参数，禁止批量层读取 wf_configs 并传递。
"""

from __future__ import annotations

import copy
import json
import traceback
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

from loguru import logger
from PyQt6.QtCore import QThread, pyqtSignal

from ...core.batch_config import BatchConfigItem
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

    ⚠️ 不含 params 字段：脚本参数由 _run_script 执行时从 wf_configs 加载，
    批量层不负责传递参数。
    """
    id: str
    name: str
    wf_file: str = ""       # DSL 工作流文件（相对 workflows/ 或绝对路径）
    class_name: str = ""    # Python 类实现（与 wf_file 二选一）


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
    progress = pyqtSignal(str, str, str)
    log = pyqtSignal(str)
    finished_all = pyqtSignal(dict)

    def __init__(
        self,
        enabled_rows: list[tuple[int, dict]],
        scripts: list[BatchScript],
        config: BatchConfigItem,
        ctx: BatchContext,
        session_manager: SessionManager,
        stop_check: Callable[[], bool],
        parent=None,
    ):
        super().__init__(parent)
        self._enabled_rows = enabled_rows  # [(index, row_data), ...]
        self._scripts = scripts
        self._config = config
        self._ctx = ctx
        self._session_manager = session_manager
        self._stop_check = stop_check
        self._stopped = False

    # ─── 主循环 ─────────────────────────────────────────

    def run(self):
        summary: dict = {
            "entries": {}, "stopped": False, "lifecycle": {},
        }
        total = len(self._enabled_rows)
        use_lifecycle = not (
            total == 1 and self._config.skip_lifecycle_for_single_item
        )
        self.log.emit(f"[批量] 开始：{total} 行 × "
                      f"{len(self._scripts)} 脚本")
        if not use_lifecycle:
            self.log.emit("[批量] 单条目直通：跳过批量生命周期工作流")

        # 初始化报告
        report = BatchReport(
            config_name=self._config.name,
            scripts=[(s.id, s.name) for s in self._scripts],
            workflows=(self._config.workflows.to_dict()
                       if use_lifecycle else {}),
            user_column=self._config.user_column,
            total_rows=total,
        )
        report.start_batch()

        batch_state: dict = {}
        can_run = True
        if use_lifecycle:
            setup = self._run_stage(
                "batch_setup", self._config.workflows.batch_setup,
                -1, -1, {}, batch_state,
            )
            batch_state = setup.state if setup.state is not None else batch_state
            summary["lifecycle"]["batch_setup"] = setup.status
            can_run = setup.status == RESULT_SUCCESS
            if not can_run:
                self._stopped = setup.status == RESULT_STOPPED
                self.log.emit(self._stage_message(tr("批次初始化"), setup))
                for run_idx, (_row_idx, row_data) in enumerate(self._enabled_rows):
                    label = self._format_label(row_data, run_idx)
                    username = self._get_username_from_row(row_data) or ""
                    summary["entries"][label] = {
                        "prepare": ST_SKIPPED,
                        "finish": ST_SKIPPED,
                        "scripts": {s.id: ST_SKIPPED for s in self._scripts},
                    }
                    report.start_entry(label, username)
                    report.record_prepare(ST_SKIPPED)
                    report.end_entry()
                    for script in self._scripts:
                        self.progress.emit(label, script.id, ST_SKIPPED)

        for run_idx, (row_idx, row_data) in enumerate(self._enabled_rows):
            if not can_run:
                break
            if self._stop_check():
                self._stopped = True
                break

            label = self._format_label(row_data, run_idx)
            username = self._get_username_from_row(row_data) or ""
            self.log.emit(f"[批量] ── [{run_idx + 1}/{total}] {label} ──")
            entry_result: dict = {
                "prepare": ST_SKIPPED,
                "finish": ST_SKIPPED,
                "scripts": {},
            }
            summary["entries"][label] = entry_result

            report.start_entry(label, username)

            prepared = BatchStageResult(state=batch_state)
            if use_lifecycle:
                # 多条目才需要切换现场；单条目直接使用当前现场执行。
                prepared = self._run_stage(
                    "prepare_item", self._config.workflows.prepare_item,
                    run_idx, row_idx, row_data, batch_state,
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
                        self.progress.emit(label, s.id, ST_SKIPPED)
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
            any_success = False
            for script in self._scripts:
                if self._stop_check():
                    self._stopped = True
                    break

                self.progress.emit(label, script.id, ST_RUNNING)
                self.log.emit(f"[批量] {label} → {script.name} ...")
                report.start_script(script.id, script.name)
                try:
                    result = self._run_script(script, session, username)
                    entry_result["scripts"][script.id] = ST_SUCCESS
                    self.progress.emit(label, script.id, ST_SUCCESS)
                    self.log.emit(f"[批量] {label} → {script.name} 完成")
                    report.end_script(ST_SUCCESS, result)
                    any_success = True
                    self._save_result(username or "unknown", script, result)
                except Exception as e:
                    tb = traceback.format_exc()
                    logger.error(f"批量执行 {label}/{script.name} 异常:\n{tb}")
                    entry_result["scripts"][script.id] = ST_FAILED
                    self.progress.emit(label, script.id, ST_FAILED)
                    self.log.emit(f"[批量] {label} → {script.name} 失败: {e}")
                    report.end_script(ST_FAILED)

            # 4. session 落盘
            if any_success and username:
                self._session_manager.save(username, session)

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
                    run_idx, row_idx, row_data, batch_state, item_summary,
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
                -1, -1, {}, batch_state,
                {"stopped": self._stopped, "entries": summary["entries"]},
            )
            summary["lifecycle"]["batch_teardown"] = teardown.status
            if teardown.status != RESULT_SUCCESS:
                self.log.emit(self._stage_message(tr("批次收尾"), teardown))

        # 6. 生成报告
        report.end_batch(stopped=self._stopped)
        try:
            report_path = report.write()
            if report_path:
                self.log.emit(f"[批量] 报告已保存: {report_path}")
        except Exception as e:
            logger.error(f"批量报告写入失败: {e}")
            self.log.emit(f"[批量] 报告写入失败（不影响执行结果）: {e}")

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

    def _format_label(self, row_data: dict, row_idx: int = -1) -> str:
        """格式化行显示标签（优先使用 user_column）"""
        if self._config.user_column:
            val = row_data.get(self._config.user_column, "")
            if val:
                return str(val)
        # fallback: 拼接所有列
        parts = []
        for col in self._config.columns:
            val = row_data.get(col, "")
            if val:
                parts.append(str(val))
        return " / ".join(parts) if parts else f"(行 {row_idx})"

    def _get_username_from_row(self, row_data: dict) -> str | None:
        """获取该行的用户名（yysls 中为游戏角色名 role）

        根据配置的 user_column 获取用户名（用于 session 绑定）
        如果未配置 user_column，返回 None（不绑定用户）
        """
        if not self._config.user_column:
            return None
        if self._config.user_column not in row_data:
            logger.warning(f"用户名列 {self._config.user_column!r} 不存在于行数据中")
            return None
        return row_data[self._config.user_column]

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
        source_idx: int,
        row_data: dict,
        batch_state: dict,
        item_result: dict | None = None,
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

        # 每阶段获得独立工作副本；只有返回协议中的 state 会被调用方提交。
        # 控制层不读取 state 内部的任何业务字段。
        working_state = copy.deepcopy(batch_state)
        enabled_rows_list = [row for _, row in self._enabled_rows]
        variables: dict = {
            "batch_phase": phase,
            "batch_table": enabled_rows_list,
            "batch_index": run_idx,
            "batch_source_index": source_idx,
            "batch_row": row_data,
            "batch_state": working_state,
            "batch_item_result": item_result or {},
        }
        # 展开行数据各列
        for k, v in row_data.items():
            variables[k] = v

        try:
            engine.execute(wf_path, initial_variables=variables)
            return self._normalize_stage_result(engine.return_value, batch_state)
        except Exception as e:
            logger.error(
                f"批量阶段失败 ({phase}, "
                f"{self._format_label(row_data, run_idx)}): {e}")
            return BatchStageResult(
                status=RESULT_FAILED,
                message=f"工作流异常: {e}",
                state=batch_state,
            )

    def _run_script(self, script: BatchScript, session: dict, username: str | None) -> dict:
        """执行单个脚本，返回 collect 结果

        参数从 wf_configs 按 script.id 加载，批量层不传递参数。
        用户信息由批量层显式传递（username）：引擎绑定 run_username 与
        save_fn，全程不读全局 active user。
        """
        engine = self._create_engine()
        engine.session = session
        engine.run_username = username or ""
        if username:
            engine._save_callback = self._session_manager.save_fn(username, session)

        # 参数由执行时从 wf_configs 加载，而非批量层传递
        from ...core.config.wf_configs import get_wf_config
        params = get_wf_config(script.id) or {}

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

        # DSL 工作流
        wf_path = Path(script.wf_file)
        if not wf_path.is_absolute():
            resolved = get_resolver().resolve_read(f"workflows/{script.wf_file}")
            if resolved is None:
                raise FileNotFoundError(f"工作流文件不存在: {script.wf_file}")
            wf_path = resolved
        return engine.execute(wf_path, initial_variables=params)

    @staticmethod
    def _save_result(role: str, script: BatchScript, result: dict):
        """结果落盘 output/{role}/{script_id}_{timestamp}.json"""
        if not isinstance(result, (dict, list)) or not result:
            return
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
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = user_dir / f"{script.id}_{ts}.json"
        path.write_text(
            json.dumps(_ser(result), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.info(f"批量结果已保存: {path}")
