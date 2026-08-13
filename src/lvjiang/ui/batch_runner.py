"""批量执行编排器

多用户 × 多脚本顺序执行：每个用户轮次先执行游戏内账号切换
（_switch_account.wf，按 game_account/game_character OCR 定位），
再切换工具侧 session 并逐个执行所选脚本。

线程模型：单个 BatchWorker(QThread) 串行执行，与主窗口
"同一时刻仅一个自动化" 约束一致（由 _begin_automation 保证）。
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

from ..core.config_resolver import get_resolver
from ..core.session_manager import SessionManager
from ..core.user_config import UserConfigManager
from ..workflows.engine import WorkflowEngine

# 切换账号工作流文件名（下划线前缀 → 日常下拉不展示）
SWITCH_WF_NAME = "_switch_account.wf"

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
class BatchUser:
    """批量执行中的用户描述"""
    name: str
    game_account: str = ""
    game_character: str = ""


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
        progress(username, script_id, status): 进度更新
        log(str): 日志行（由 host 转发到日志面板）
        finished_all(dict): 全部结束，携带汇总
    """
    progress = pyqtSignal(str, str, str)
    log = pyqtSignal(str)
    finished_all = pyqtSignal(dict)

    def __init__(
        self,
        users: list[BatchUser],
        scripts: list[BatchScript],
        ctx: BatchContext,
        user_manager: UserConfigManager,
        session_manager: SessionManager,
        stop_check: Callable[[], bool],
        skip_first_switch: bool = True,
        parent=None,
    ):
        super().__init__(parent)
        self._users = users
        self._scripts = scripts
        self._ctx = ctx
        self._user_manager = user_manager
        self._session_manager = session_manager
        self._stop_check = stop_check
        self._skip_first_switch = skip_first_switch
        self._stopped = False

    # ─── 主循环 ─────────────────────────────────────────

    def run(self):
        summary: dict = {"users": {}, "stopped": False}
        self.log.emit(f"[批量] 开始：{len(self._users)} 个用户 × "
                      f"{len(self._scripts)} 个脚本")

        for i, user in enumerate(self._users):
            if self._stop_check():
                self._stopped = True
                break

            self.log.emit(f"[批量] ── 用户 {i + 1}/{len(self._users)}: "
                          f"{user.name} ──")
            user_result: dict = {"switch": ST_SKIPPED, "scripts": {}}
            summary["users"][user.name] = user_result

            # 1. 游戏内账号切换（首个用户可跳过）
            need_switch = not (self._skip_first_switch and i == 0)
            if need_switch:
                user_result["switch"] = ST_RUNNING
                ok = self._run_switch(user)
                user_result["switch"] = ST_SUCCESS if ok else ST_FAILED
                if not ok:
                    # 切换失败 → 该用户全部脚本标记跳过，继续下一用户
                    for s in self._scripts:
                        user_result["scripts"][s.id] = ST_SKIPPED
                        self.progress.emit(user.name, s.id, ST_SKIPPED)
                    self.log.emit(f"[批量] {user.name} 账号切换失败，跳过该用户")
                    continue

            # 2. 工具侧用户切换 + session 加载
            self._user_manager.set_active_user(user.name)
            session = self._session_manager.load(user.name)

            # 3. 顺序执行脚本
            any_success = False
            for script in self._scripts:
                if self._stop_check():
                    self._stopped = True
                    break

                self.progress.emit(user.name, script.id, ST_RUNNING)
                self.log.emit(f"[批量] {user.name} → {script.name} ...")
                try:
                    result = self._run_script(script, session)
                    user_result["scripts"][script.id] = ST_SUCCESS
                    self.progress.emit(user.name, script.id, ST_SUCCESS)
                    self.log.emit(f"[批量] {user.name} → {script.name} 完成")
                    any_success = True
                    self._save_result(user.name, script, result)
                except Exception as e:
                    tb = traceback.format_exc()
                    logger.error(f"批量执行 {user.name}/{script.name} 异常:\n{tb}")
                    user_result["scripts"][script.id] = ST_FAILED
                    self.progress.emit(user.name, script.id, ST_FAILED)
                    self.log.emit(f"[批量] {user.name} → {script.name} 失败: {e}")
                    # 默认继续下一脚本

            # 4. session 落盘（有脚本正常执行过才保存）
            if any_success:
                self._session_manager.save(user.name, session)

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

    def _run_switch(self, user: BatchUser) -> bool:
        """执行账号切换工作流，返回是否成功"""
        wf_path = get_resolver().resolve_read(f"workflows/{SWITCH_WF_NAME}")
        if wf_path is None:
            self.log.emit(f"[批量] 切换工作流不存在: {SWITCH_WF_NAME}，"
                          "请在场景编辑器校准后使用")
            return False

        if not user.game_account and not user.game_character:
            self.log.emit(f"[批量] {user.name} 未配置游戏账号/角色名，无法切换")
            return False

        engine = self._create_engine()
        engine.session = {}
        try:
            engine.execute(wf_path, initial_variables={
                "target_account": user.game_account,
                "target_character": user.game_character,
            })
            return True
        except Exception as e:
            logger.error(f"账号切换失败 ({user.name}): {e}")
            self.log.emit(f"[批量] 账号切换异常: {e}")
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
    def _save_result(username: str, script: BatchScript, result: dict):
        """结果落盘 output/{username}/{script_id}_{timestamp}.json"""
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

        user_dir = OUTPUT_DIR / username
        user_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = user_dir / f"{script.id}_{ts}.json"
        path.write_text(
            json.dumps(_ser(result), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.info(f"批量结果已保存: {path}")
