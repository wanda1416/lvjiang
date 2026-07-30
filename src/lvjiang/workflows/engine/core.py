"""WorkflowEngine 主类：生命周期、执行入口与语句分发"""

import traceback
from typing import Callable

from loguru import logger
from pathlib import Path

from ...config import DelayConfig
from ...core.capture_base import CaptureBackend
from ...core.ocr import OCREngine
from ...core.input_base import InputBackend
from ...core.scene_registry import Layout, get_scene_name

from ..grammar import (
    parse_file,
    Click, Drag, Wait, Scan, Recognize, Collect, Log,
    Import, ProcDef, CallProc,
    If, For, ForRange, Loop, WhileLoop, UntilLoop, Break, Continue, Return, Label, Goto, Eval, EvalFieldChainAssign, FuncCall,
    Literal,
)
from ..grammar.ast_nodes import Align, Try
from ..scene_scan import collect_scene_keys
from ..base import BaseWorkflow
from ..align import GridAlignment

from .signals import WorkflowUserError, _BreakSignal, _ReturnSignal, _GotoSignal, _ContinueSignal
from .actions import _ActionsMixin
from .panel import _PanelMixin
from .data_ops import _DataOpsMixin
from .control_flow import _ControlFlowMixin
from .evaluation import _EvalMixin


# ─── 引擎 ─────────────────────────────────────────────────

class WorkflowEngine(_ActionsMixin, _PanelMixin, _DataOpsMixin,
                     _ControlFlowMixin, _EvalMixin):
    """DSL v2 工作流运行时

    直接持有硬件后端，管理 session/context 生命周期。
    通过 _ensure_workflow() 懒创建 BaseWorkflow 作为游戏操作委托。
    通过 import/def/call proc 实现模块化过程复用。
    """

    def __init__(
        self,
        *,
        capture: CaptureBackend,
        ocr: OCREngine,
        input_ctrl: InputBackend,
        layout: Layout,
        delay_config: DelayConfig | None = None,
        window_left: int = 0,
        window_top: int = 0,
        stop_check: Callable[[], bool] | None = None,
    ):
        # 硬件后端（直接持有）
        self._capture = capture
        self._ocr = ocr
        self._input = input_ctrl
        self._layout = layout
        self._delay = delay_config or DelayConfig()
        self._window_left = window_left
        self._window_top = window_top
        self._stop_check = stop_check or (lambda: False)
        # 执行状态
        self.variables: dict = {}
        self.output: dict = {}
        self._coord_meta: dict[str, dict] = {}
        self._base_dir: Path | None = None
        # panel 对齐缓存：(scene_key, panel_key) → GridAlignment
        self._panel_alignments: dict[tuple[str, str], GridAlignment] = {}
        # 过程定义索引：{name: ProcDef}，由 _execute_dsl 填充
        self._procs: dict[str, ProcDef] = {}
        # session / context（公开属性，UI 层注入）
        self.session: dict = {}          # 持久状态（UI 层从 SessionManager 加载）
        self.context: dict = {}          # 运行时上下文（每次执行自动初始化空 dict）
        self._save_callback: Callable | None = None
        # UI 交互回调（UI 层注入，解决工作流线程不能直接弹对话框的问题）
        # 签名: (action, **kwargs) → result
        #   action="confirm": message → bool
        #   action="pause":   message → None
        #   action="notify":  message → None
        #   action="input":   prompt  → str | None
        self._ui_callback: Callable | None = None
        # 游戏操作委托（execute 时懒创建）
        self._workflow: BaseWorkflow | None = None

    @property
    def _session(self) -> dict:
        """兼容旧访问方式，DSL 内部使用"""
        return self.session

    @_session.setter
    def _session(self, value: dict | None):
        self.session = value if value is not None else {}

    @property
    def _context(self) -> dict:
        """兼容旧访问方式，DSL 内部使用"""
        return self.context

    @_context.setter
    def _context(self, value: dict | None):
        self.context = value if value is not None else {}

    def _ensure_workflow(self) -> BaseWorkflow:
        """懒创建 BaseWorkflow 作为游戏操作委托"""
        if self._workflow is None:
            self._workflow = BaseWorkflow(
                capture=self._capture,
                ocr=self._ocr,
                input_ctrl=self._input,
                layout=self._layout,
                delay_config=self._delay,
                window_left=self._window_left,
                window_top=self._window_top,
                stop_check=self._stop_check,
            )
        return self._workflow

    def execute(self, source, *, initial_variables: dict | None = None,
                _reset_context: bool = True) -> dict:
        """统一执行入口

        Args:
            source: .wf 文件路径 或 BaseWorkflow 实例
            initial_variables: 外部注入的初始变量
            _reset_context: 是否重置 context（仅顶层调用为 True）

        Returns:
            dict: collect 累积结果
        """
        if initial_variables:
            self.variables.update(initial_variables)

        # context 是临时状态，仅顶层执行时重置
        # 过程中通过 context 共享引用传递数据
        if _reset_context:
            self.context = {}

        # Python 工作流实例
        if isinstance(source, BaseWorkflow):
            return self._execute_python_workflow(source)

        # DSL .wf 文件
        source_path = Path(source)
        if source_path.suffix == ".wf":
            return self._execute_dsl(source_path)
        raise ValueError(f"不支持的执行源: {source}")

    def _execute_dsl(self, wf_path: Path) -> dict:
        """加载并执行 .wf 文件"""
        self._ensure_workflow()
        resolved = Path(wf_path).resolve()
        self._base_dir = resolved.parent
        program = parse_file(resolved)

        # 解析 import 链（含循环检测），收集所有 def 到 self._procs
        self._procs = {}
        import_stack = {str(resolved)}
        self._resolve_imports(program, import_stack)
        # 注册本地 def
        for name, proc_def in program.procs.items():
            self._procs[name] = proc_def

        # 静态校验：wait 引用的命名等待参数必须已定义，未定义直接报错不执行
        self._validate_named_waits(program)
        # 静态校验：脚本引用的场景必须已在当前布局绑定坐标（取代手写 required_scenes）
        self._validate_scenes_bound(program)

        logger.info(f"=== DSL 工作流开始: {resolved.stem} ({len(program.body)} 条顶层指令, {len(self._procs)} 个过程) ===")

        try:
            self._exec_body(program.body)
        except _GotoSignal as sig:
            logger.error(f"goto 目标标签不存在: {sig.target}")
            return self.output
        except _ReturnSignal:
            logger.info(f"=== DSL 工作流正常返回，收集到 {len(self.output)} 项数据 ===")
            return self.output
        except _BreakSignal:
            # 停止请求在顶层语句边界触发时 _BreakSignal 会穿透到顶层，
            # 同样视为正常停止，返回已收集的部分结果
            logger.info(f"=== DSL 工作流被停止，收集到 {len(self.output)} 项数据 ===")
            return self.output

        logger.info(f"=== DSL 工作流完成，收集到 {len(self.output)} 项数据 ===")
        return self.output

    def _resolve_imports(self, program, import_stack: set):
        """递归解析 import 链，收集所有 def 到 self._procs

        import_stack: 当前 import 链中的文件路径集合，用于循环检测。
        """
        for imp in program.imports:
            imp_path = Path(imp.path)
            if not imp_path.is_absolute() and self._base_dir:
                imp_path = self._base_dir / imp_path
            imp_resolved = str(imp_path.resolve())

            # 循环检测
            if imp_resolved in import_stack:
                chain = " -> ".join(sorted(import_stack)) + f" -> {imp_resolved}"
                raise WorkflowUserError(f"循环 import 检测: {chain}")

            # 解析导入文件
            imp_program = parse_file(imp_path)
            new_stack = import_stack | {imp_resolved}

            # 递归解析子文件的 import（临时切换 base_dir）
            old_base = self._base_dir
            self._base_dir = imp_path.parent
            self._resolve_imports(imp_program, new_stack)
            self._base_dir = old_base

            # 收集子文件的 def（平铺到当前命名空间）
            for name, proc_def in imp_program.procs.items():
                self._procs[name] = proc_def
            logger.debug(f"import: {imp.path} → 注册 {len(imp_program.procs)} 个过程")

    def _validate_scenes_bound(self, program):
        """解析后静态校验：脚本引用的场景必须已在当前布局绑定坐标

        遍历顶层语句与所有过程体（含 import 引入的），搜集全部静态场景引用；
        区域/坐标点/方向/面板任一非空即视为已绑定。缺失直接报错不执行。
        """
        scenes = collect_scene_keys(program.body, self._procs)
        missing = [
            k for k in sorted(scenes)
            if not (self._layout.get_scene_regions(k) or self._layout.get_scene_points(k)
                    or self._layout.get_scene_arrows(k) or self._layout.get_scene_panels(k))
        ]
        if missing:
            names = "、".join(get_scene_name(k) for k in missing)
            raise WorkflowUserError(f"以下场景未绑定坐标: {names}")

    def _validate_named_waits(self, program):
        """解析后静态校验：wait 引用的命名等待参数必须已定义

        命名等待全部来自 DelayConfig.custom（配置管理「等待参数」页），
        遍历顶层语句与所有过程体（含 import 引入的），引用未定义的 key
        直接报错返回，不进入执行阶段。
        """
        missing: dict[str, int] = {}  # key → 首次出现行号
        for body in [program.body, *(p.body for p in self._procs.values())]:
            self._collect_missing_waits(body, missing)
        if missing:
            detail = "、".join(f"{name}(行 {line})" for name, line in missing.items())
            raise WorkflowUserError(
                f"wait 引用了未定义的等待参数: {detail}，请先在配置管理→等待参数中定义")

    def _collect_missing_waits(self, stmts: list, missing: dict[str, int]):
        """递归收集语句体中引用未定义命名等待的 Wait 节点"""
        for node in stmts:
            match node:
                case Wait(delay=Literal(value=str() as name)):
                    if name not in self._delay.custom:
                        missing.setdefault(name, node.line_no)
                case If():
                    self._collect_missing_waits(node.then_body, missing)
                    self._collect_missing_waits(node.else_body, missing)
                case For() | ForRange() | Loop() | WhileLoop() | UntilLoop():
                    self._collect_missing_waits(node.body, missing)
                case Try():
                    self._collect_missing_waits(node.body, missing)
                    self._collect_missing_waits(node.catch_body, missing)

    def _execute_python_workflow(self, workflow: BaseWorkflow) -> dict:
        """执行 Python 工作流实例"""
        workflow.reset_state()
        workflow.variables.update(self.variables)
        logger.info(f"=== Python 工作流开始: {workflow.__class__.__name__} ===")
        try:
            result = workflow.run()
        except Exception as e:
            logger.error(f"Python 工作流异常: {e}\n{traceback.format_exc()}")
            result = workflow.output
        logger.info(f"=== Python 工作流完成，收集到 {len(result)} 项数据 ===")
        return result

    # ─── 语句执行 ──────────────────────────────────────────

    def _exec_body(self, stmts: list) -> None:
        """执行一组语句（含 goto 跳转支持）

        collect 的输出记录到 self._collect_output，不中断执行流。
        仅 break/goto/停止请求 才会提前终止。
        goto 信号会向上传播，直到找到包含目标标签的层级。
        """
        pc = 0
        while pc < len(stmts):
            if self._stop_check():
                logger.info("工作流被用户停止")
                return

            node = stmts[pc]

            try:
                self._exec_stmt(node)
            except _GotoSignal as sig:
                target = sig.target
                # 检查当前层级的标签索引
                local_index = self._build_label_index(stmts)
                if target in local_index:
                    pc = local_index[target]
                    continue  # 从标签位置继续，不 pc+1
                else:
                    # 当前层级没有该标签，向上传播
                    raise
            except _ReturnSignal:
                raise  # return 直接穿透，不记错误日志
            except _BreakSignal:
                raise  # break 直接穿透，由 loop/for 处理
            except _ContinueSignal:
                raise  # continue 直接穿透，由循环处理
            except BaseException as e:
                line_info = f"(行 {node.line_no})" if hasattr(node, 'line_no') and node.line_no else ""
                logger.error(f"DSL 执行异常 {line_info}: {e}")
                logger.error(f"异常详情:\n{traceback.format_exc()}")
                raise  # 异常即终止，不跳过继续

            pc += 1

    def _exec_stmt(self, node):
        """执行单条语句"""
        # 语句边界也检查停止标志，让 F10 在两条语句之间立即生效
        if self._stop_check():
            raise _BreakSignal()
        match node:
            case Click():
                self._exec_click(node)
            case Drag():
                self._exec_drag(node)
            case Wait():
                self._exec_wait(node)
            case Align():
                self._exec_align(node)
            case Scan():
                self._exec_scan(node)
            case Recognize():
                self._exec_recognize(node)
            case Collect():
                self._exec_collect(node)
            case Log():
                if isinstance(node.message, FuncCall):
                    logger.info(str(self._call_func(node.message)))
                else:
                    val = self._resolve(node.message)
                    logger.info("null" if val is None else val)
            case If():
                self._exec_if(node)
            case For():
                self._exec_for(node)
            case ForRange():
                self._exec_for_range(node)
            case Loop():
                self._exec_loop(node)
            case WhileLoop():
                self._exec_while_loop(node)
            case UntilLoop():
                self._exec_until_loop(node)
            case Break():
                raise _BreakSignal()
            case Continue():
                raise _ContinueSignal()
            case Return():
                raise _ReturnSignal()
            case Label():
                pass  # 标签无操作
            case Goto():
                raise _GotoSignal(node.target)
            case Eval():
                self._exec_eval(node)
            case EvalFieldChainAssign():
                self._exec_eval_field_assign(node)
            case Import():
                pass  # import 在 _execute_dsl 中已处理，运行时跳过
            case CallProc():
                self._exec_call_proc(node)
            case Try():
                self._exec_try(node)
            case _:
                logger.error(f"未知节点类型: {type(node).__name__}")
