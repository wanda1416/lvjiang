"""批量执行编排器

通用批处理执行器：遍历启用的行，根据配置调用预处理/切换/后处理 wf。
每个 wf 接收 (batch_table, batch_index, batch_row) 变量。

线程模型：单个 BatchWorker(QThread) 串行执行，与主窗口
"同一时刻仅一个自动化" 约束一致。
"""

from __future__ import annotations

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
from ...core.user_config import UserConfigManager
from ...workflows.engine import WorkflowEngine

# 进度状态常量
ST_PENDING = "待执行"
ST_RUNNING = "运行中"
ST_SUCCESS = "成功"
ST_FAILED = "失败"
ST_SKIPPED = "跳过"


@dataclass
class BatchScript:
    """批量执行中的脚本描述"""
    id: str
    name: str
    wf_file: str = ""       # DSL 工作流文件（相对 workflows/ 或绝对路径）
    class_name: str = ""    # Python 类实现（与 wf_file 二选一）
    params: dict | None = None


@dataclass
class BatchContext:
    """引擎创建所需的后端上下文（由 host 在启动时注入）"""
    capture: object
    ocr: object
    input_ctrl: object
    layout: object
    input_sim: object | None = None
    delay_params: dict | None = None
    window_left: int = 0
    window_top: int = 0


class BatchWorker(QThread):
    """批量执行工作线程

    Signals:
        progress(entry_label, script_id, status): 进度更新
        log(str): 日志行（由 host 转发到日志面板）
        user_changed(str): 用户切换通知（由 host 转发到主页面）
        finished_all(dict): 全部结束，携带汇总
    """
    progress = pyqtSignal(str, str, str)
    log = pyqtSignal(str)
    user_changed = pyqtSignal(str)
    finished_all = pyqtSignal(dict)

    def __init__(
        self,
        enabled_rows: list[tuple[int, dict]],
        scripts: list[BatchScript],
        config: BatchConfigItem,
        ctx: BatchContext,
        user_manager: UserConfigManager,
        session_manager: SessionManager,
        stop_check: Callable[[], bool],
        parent=None,
    ):
        super().__init__(parent)
        self._enabled_rows = enabled_rows  # [(index, row_data), ...]
        self._scripts = scripts
        self._config = config
        self._ctx = ctx
        self._user_manager = user_manager
        self._session_manager = session_manager
        self._stop_check = stop_check
        self._stopped = False

    # ─── 主循环 ─────────────────────────────────────────

    def run(self):
        summary: dict = {"entries": {}, "stopped": False}
        total = len(self._enabled_rows)
        self.log.emit(f"[批量] 开始：{total} 行 × "
                      f"{len(self._scripts)} 脚本")

        prev_row: dict | None = None

        for run_idx, (row_idx, row_data) in enumerate(self._enabled_rows):
            if self._stop_check():
                self._stopped = True
                break

            label = self._format_label(row_data, run_idx)
            self.log.emit(f"[批量] ── [{run_idx + 1}/{total}] {label} ──")
            entry_result: dict = {"switch": ST_SKIPPED, "scripts": {}}
            summary["entries"][label] = entry_result

            # 1. 调用预处理/切换 wf
            wf_path = self._choose_switch_wf(row_idx, row_data, prev_row)
            if wf_path:
                ok = self._run_switch_wf(wf_path, run_idx, row_data)
                if not ok:
                    entry_result["switch"] = ST_FAILED
                    for s in self._scripts:
                        entry_result["scripts"][s.id] = ST_SKIPPED
                        self.progress.emit(label, s.id, ST_SKIPPED)
                    self.log.emit(f"[批量] {label} 切换失败，跳过该行")
                    continue
                entry_result["switch"] = ST_SUCCESS
            else:
                entry_result["switch"] = ST_SKIPPED

            # 2. 工具侧 session 切换（user 是角色名，从 row_data 获取）
            role = self._get_role_from_row(row_data)
            if role:
                self._user_manager.set_active_user(role)
                self.user_changed.emit(role)  # 通知主页面更新当前用户
                session = self._session_manager.load(role)
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
                try:
                    result = self._run_script(script, session)
                    entry_result["scripts"][script.id] = ST_SUCCESS
                    self.progress.emit(label, script.id, ST_SUCCESS)
                    self.log.emit(f"[批量] {label} → {script.name} 完成")
                    any_success = True
                    self._save_result(role or "unknown", script, result)
                except Exception as e:
                    tb = traceback.format_exc()
                    logger.error(f"批量执行 {label}/{script.name} 异常:\n{tb}")
                    entry_result["scripts"][script.id] = ST_FAILED
                    self.progress.emit(label, script.id, ST_FAILED)
                    self.log.emit(f"[批量] {label} → {script.name} 失败: {e}")

            # 4. session 落盘
            if any_success and role:
                self._session_manager.save(role, session)

            prev_row = row_data

            if self._stopped:
                break

        summary["stopped"] = self._stopped
        tag = "（用户中断）" if self._stopped else ""
        self.log.emit(f"[批量] 全部结束{tag}")

        # 5. 调用后处理 wf（如果定义）
        postprocess_wf = self._config.workflows.postprocess
        if postprocess_wf:
            self.log.emit(f"[批量] 执行后处理: {postprocess_wf}")
            self._run_switch_wf(postprocess_wf, -1, {})

        self.finished_all.emit(summary)

    # ─── 切换逻辑 ───────────────────────────────────────

    def _choose_switch_wf(self, row_idx: int, row_data: dict,
                          prev_row: dict | None) -> str | None:
        """选择调用哪个 wf

        - 首行 → preprocess wf
        - 后续行 → switch wf
        """
        if prev_row is None:
            return self._config.workflows.preprocess or None
        return self._config.workflows.switch or None

    def _format_label(self, row_data: dict, row_idx: int = -1) -> str:
        """格式化行显示标签"""
        parts = []
        for col in self._config.columns:
            val = row_data.get(col, "")
            if val:
                parts.append(str(val))
        return " / ".join(parts) if parts else f"(行 {row_idx})"

    def _get_role_from_row(self, row_data: dict) -> str | None:
        """根据配置的 user_column 获取用户名（用于 session 切换）

        如果未配置 user_column，返回 None（不切换用户）
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
        return WorkflowEngine(
            capture=ctx.capture,
            ocr=ctx.ocr,
            input_ctrl=ctx.input_ctrl,
            layout=ctx.layout,
            input_sim=ctx.input_sim,
            delay_params=ctx.delay_params,
            window_left=ctx.window_left,
            window_top=ctx.window_top,
            stop_check=self._stop_check,
        )

    def _run_switch_wf(self, wf_name: str, run_idx: int, row_data: dict) -> bool:
        """执行切换工作流（预处理/切换/后处理）

        传递变量：batch_table (启用行列表), batch_index (启用行索引), batch_row, 以及行数据的各列
        """
        wf_path = get_resolver().resolve_read(f"workflows/{wf_name}")
        if wf_path is None:
            self.log.emit(f"[批量] 工作流不存在: {wf_name}")
            return False

        engine = self._create_engine()
        engine.session = {}

        # 构建变量：batch_table 为启用行列表（不含禁用行）
        enabled_rows_list = [row for _, row in self._enabled_rows]
        variables: dict = {
            "batch_table": enabled_rows_list,
            "batch_index": run_idx,
            "batch_row": row_data,
        }
        # 展开行数据各列
        for k, v in row_data.items():
            variables[k] = v

        try:
            engine.execute(wf_path, initial_variables=variables)
            return True
        except Exception as e:
            logger.error(f"切换失败 ({self._format_label(row_data, run_idx)}): {e}")
            self.log.emit(f"[批量] 切换异常: {e}")
            return False

    def _run_script(self, script: BatchScript, session: dict) -> dict:
        """执行单个脚本，返回 collect 结果"""
        engine = self._create_engine()
        engine.session = session
        params = script.params or {}

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
