"""批量执行编排器

多条目顺序执行：每条 (账号, 角色) 轮次先通过 DSL 工作流完成
游戏内登录（选账号 → 选角色 → 进入游戏），再顺序执行所选脚本，
最后退出回登录页。

同账号多角色优化：连续条目属同一账号时跳过选账号步骤，
直接选角色进入游戏。

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

from ..core.batch_config import BatchEntry
from ..core.config_resolver import get_resolver
from ..core.session_manager import SessionManager
from ..core.user_config import UserConfigManager
from ..workflows.engine import WorkflowEngine

# DSL 工作流文件名（batch 子目录）
SWITCH_ACCOUNT_WF = "batch/_switch_account.wf"
SELECT_ROLE_WF = "batch/_select_role.wf"
EXIT_TO_LOGIN_WF = "batch/_exit_to_login.wf"

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
        finished_all(dict): 全部结束，携带汇总
    """
    progress = pyqtSignal(str, str, str)
    log = pyqtSignal(str)
    finished_all = pyqtSignal(dict)

    def __init__(
        self,
        entries: list[BatchEntry],
        scripts: list[BatchScript],
        ctx: BatchContext,
        user_manager: UserConfigManager,
        session_manager: SessionManager,
        stop_check: Callable[[], bool],
        parent=None,
    ):
        super().__init__(parent)
        self._entries = entries
        self._scripts = scripts
        self._ctx = ctx
        self._user_manager = user_manager
        self._session_manager = session_manager
        self._stop_check = stop_check
        self._stopped = False

    # ─── 主循环 ─────────────────────────────────────────

    def run(self):
        summary: dict = {"entries": {}, "stopped": False}
        self.log.emit(f"[批量] 开始：{len(self._entries)} 条目 × "
                      f"{len(self._scripts)} 脚本")

        current_account = ""

        for i, entry in enumerate(self._entries):
            if self._stop_check():
                self._stopped = True
                break

            label = f"{entry.account}/{entry.role}"
            self.log.emit(f"[批量] ── [{i + 1}/{len(self._entries)}] {label} ──")
            entry_result: dict = {"switch": ST_SKIPPED, "scripts": {}}
            summary["entries"][label] = entry_result

            # 1. 游戏内登录（同账号跳过选账号）
            need_full_switch = (entry.account != current_account)
            ok = self._run_login(entry, need_full_switch)
            if not ok:
                entry_result["switch"] = ST_FAILED
                for s in self._scripts:
                    entry_result["scripts"][s.id] = ST_SKIPPED
                    self.progress.emit(label, s.id, ST_SKIPPED)
                self.log.emit(f"[批量] {label} 登录失败，跳过该条目")
                continue

            entry_result["switch"] = ST_SUCCESS
            current_account = entry.account

            # 2. 工具侧 session 切换（user 是角色名，不是账号）
            self._user_manager.set_active_user(entry.role)
            session = self._session_manager.load(entry.role)

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
                    self._save_result(entry.account, entry.role, script, result)
                except Exception as e:
                    tb = traceback.format_exc()
                    logger.error(f"批量执行 {label}/{script.name} 异常:\n{tb}")
                    entry_result["scripts"][script.id] = ST_FAILED
                    self.progress.emit(label, script.id, ST_FAILED)
                    self.log.emit(f"[批量] {label} → {script.name} 失败: {e}")

            # 4. session 落盘
            if any_success:
                self._session_manager.save(entry.role, session)

            # 5. 退出回登录页（最后一个条目也退出，方便下次从头开始）
            if not self._stop_check():
                exit_ok = self._run_exit_to_login()
                if not exit_ok:
                    self.log.emit(f"[批量] {label} 退出回登录页失败，请手动处理")

            if self._stopped:
                break

        summary["stopped"] = self._stopped
        tag = "（用户中断）" if self._stopped else ""
        self.log.emit(f"[批量] 全部结束{tag}")
        self.finished_all.emit(summary)

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

    def _run_login(self, entry: BatchEntry, full_switch: bool) -> bool:
        """执行登录工作流

        full_switch=True  → _switch_account.wf（选账号 + 登录 + 选角色 + 进入游戏）
        full_switch=False → _select_role.wf（仅选角色 + 进入游戏，同账号优化）
        """
        wf_name = SWITCH_ACCOUNT_WF if full_switch else SELECT_ROLE_WF
        wf_path = get_resolver().resolve_read(f"workflows/{wf_name}")
        if wf_path is None:
            self.log.emit(f"[批量] 工作流不存在: {wf_name}")
            return False

        engine = self._create_engine()
        engine.session = {}
        variables: dict = {"role_idx": entry.role_index}
        if full_switch:
            variables["target_phone_tail"] = entry.phone_tail

        try:
            engine.execute(wf_path, initial_variables=variables)
            return True
        except Exception as e:
            logger.error(f"登录失败 ({entry.account}/{entry.role}): {e}")
            self.log.emit(f"[批量] 登录异常: {e}")
            return False

    def _run_exit_to_login(self) -> bool:
        """执行退出工作流（主页 → 菜单 → 退出 → 确认 → 登录页）"""
        wf_path = get_resolver().resolve_read(f"workflows/{EXIT_TO_LOGIN_WF}")
        if wf_path is None:
            self.log.emit(f"[批量] 退出工作流不存在: {EXIT_TO_LOGIN_WF}")
            return False

        engine = self._create_engine()
        engine.session = {}
        try:
            engine.execute(wf_path)
            return True
        except Exception as e:
            logger.error(f"退出回登录页失败: {e}")
            self.log.emit(f"[批量] 退出异常: {e}")
            return False

    def _run_script(self, script: BatchScript, session: dict) -> dict:
        """执行单个脚本，返回 collect 结果"""
        engine = self._create_engine()
        engine.session = session
        params = script.params or {}

        if script.class_name:
            from ..workflows.implementations import get_workflow_class
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
    def _save_result(account: str, role: str, script: BatchScript, result: dict):
        """结果落盘 output/{role}/{script_id}_{timestamp}.json"""
        if not isinstance(result, (dict, list)) or not result:
            return
        from ..constants import OUTPUT_DIR

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
