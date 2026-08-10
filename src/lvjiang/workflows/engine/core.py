"""WorkflowEngine 主类：生命周期、执行入口与语句分发"""

import posixpath
import traceback
from datetime import datetime
from pathlib import Path
from typing import Callable

import cv2
from loguru import logger

from ...constants import PROJECT_ROOT
from ...core.capture_base import CaptureBackend
from ...core.config import DelayParam, InputSimConfig
from ...core.config.resolver import get_resolver
from ...core.input_base import InputBackend
from ...core.layout_models import Layout
from ...core.ocr import OCREngine
from ..align import GridAlignment
from ..base import BaseWorkflow
from ..grammar import (
    Break,
    CallProc,
    Click,
    Collect,
    Continue,
    Drag,
    Eval,
    EvalFieldChainAssign,
    Find,
    For,
    ForRange,
    FuncCall,
    Goto,
    If,
    Import,
    Label,
    Literal,
    Log,
    Loop,
    ProcDef,
    Recognize,
    Return,
    Scan,
    Screenshot,
    UntilLoop,
    Wait,
    WaitStable,
    WhileLoop,
    parse_file,
)
from ..grammar.ast_nodes import Align, Try
from ..static_check import check_refs, format_problems
from ..workflow_references import collect_refs
from .actions import _ActionsMixin
from .control_flow import _ControlFlowMixin
from .data_ops import _DataOpsMixin
from .evaluation import _EvalMixin
from .panel import _PanelMixin
from .signals import (
    WorkflowUserError,
    _BreakSignal,
    _ContinueSignal,
    _GotoSignal,
    _ReturnSignal,
)

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
        input_sim: InputSimConfig | None = None,
        delay_params: dict[str, DelayParam] | None = None,
        window_left: int = 0,
        window_top: int = 0,
        stop_check: Callable[[], bool] | None = None,
    ):
        # 硬件后端（直接持有）
        self._capture = capture
        self._ocr = ocr
        self._input = input_ctrl
        self._layout = layout
        self._input_sim = input_sim or InputSimConfig()
        self._delay_params = delay_params or {}
        self._window_left = window_left
        self._window_top = window_top
        self._stop_check = stop_check or (lambda: False)
        # 执行状态
        self.variables: dict = {}
        self.output: dict = {}
        self.return_value = None  # 顶层 return 值（供场景编辑器显示）
        self._coord_meta: dict[str, dict] = {}
        self._base_dir: Path | None = None
        # 当前 wf 相对 workflows 根的目录（跨层 import 解析用），None=根外文件
        self._wf_rel_dir: str | None = None
        # panel 对齐缓存：(scene_key, panel_key) → GridAlignment
        self._panel_alignments: dict[tuple[str, str], GridAlignment] = {}
        # 过程定义索引：{name: ProcDef}，由 _execute_dsl / load_subcalls 填充
        self._procs: dict[str, ProcDef] = {}
        # 过程来源索引：{name: 所在 .wf 文件}，静态检查报错定位用
        self._proc_sources: dict[str, str] = {}
        # session / context（公开属性，UI 层注入）
        self.session: dict = {}          # 持久状态（UI 层从 SessionManager 加载）
        self.context: dict = {}          # 运行时上下文（每次执行自动初始化空 dict）
        # 本次运行归属的用户名（启动时由 UI 层/批量层快照注入）。
        # 整个运行生命周期只依赖此绑定值，绝不再重读全局 active user，
        # 保证运行期间 UI 切换用户不影响数据落盘归属。
        self.run_username: str = ""
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

    def _ensure_workflow(self) -> BaseWorkflow:
        """懒创建 BaseWorkflow 作为游戏操作委托

        注意：Python 工作流执行期间（_execute_python_workflow），self._workflow
        会被临时设置为注入的工作流实例（如 AutoTuningWorkflow），使 DSL 子过程
        的游戏操作原语委派到该工作流。执行结束后在 finally 中重置为 None。
        """
        if self._workflow is None:
            self._workflow = BaseWorkflow(
                capture=self._capture,
                ocr=self._ocr,
                input_ctrl=self._input,
                layout=self._layout,
                input_sim=self._input_sim,
                delay_params=self._delay_params,
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

    def validate_only(self, wf_path: Path | str) -> None:
        """只解析与静态校验 .wf，不执行任何动作

        供上机前预检与 CI 门禁使用：布局漏绑区域、key 拼错、命名等待参数未
        定义这类错误，过去只有真执行到那一行才炸 —— 而实机失败时游戏已经被
        点到别处去了。校验通过返回 None，不通过抛 WorkflowUserError（消息含
        全部问题清单）。

        判据与 _execute_dsl 共用 _load_and_validate，不会出现「预检放过、
        上机仍炸」的偏差。
        """
        self._load_and_validate(Path(wf_path).resolve())

    def _load_and_validate(self, resolved: Path):
        """解析 .wf（含 import 链与 def 注册）并跑两道静态校验，返回 program

        _execute_dsl 与 validate_only 共用此方法：两者对「什么算合法脚本」的
        判据必须是同一份。
        """
        self._base_dir = resolved.parent
        self._wf_rel_dir = self._workflows_rel_dir(resolved)
        program = parse_file(resolved)

        # 解析 import 链（含循环检测），收集所有 def 到 self._procs
        self._procs = {}
        self._proc_sources = {}
        import_stack = {str(resolved)}
        self._resolve_imports(program, import_stack)
        # 注册本地 def
        for name, proc_def in program.procs.items():
            self._procs[name] = proc_def
            self._proc_sources[name] = program.source

        # 静态校验：wait 引用的命名等待参数必须已定义，未定义直接报错不执行
        self._validate_named_waits(program)
        # 静态校验：脚本引用的场景 / 区域 / 方向 / 面板必须已在当前布局绑定坐标
        self._validate_refs_bound(program)
        return program

    # ─── Python 桥：subcall 加载与调用 ────────────────────────

    def load_subcalls(self, wf_path: str | Path) -> None:
        """加载 .wf 文件的 def 定义为可调用过程（不执行顶层语句）

        供 Python 工作流经 call_subcall 复用 DSL 子过程，避免同一导航/
        操作序列在 Python 与 DSL 两处重复维护。

        每次调用都重新解析文件，确保修改立即生效。后加载的同名过程覆盖
        先加载的。相对路径以 workflows 根为基准经 resolver 解析（local
        影子层优先）；import 链递归加载；与 _execute_dsl 同样跑两道静态
        校验（命名等待 / 布局引用）。

        注意：后续 execute(.wf) 会重置 _procs，已加载的 subcall 过程将失效。
        若需 DSL 执行期间保留 subcall，请在 execute 之后重新 load_subcalls。
        """
        path = Path(wf_path)
        if not path.is_absolute():
            found = get_resolver().resolve_read(f"workflows/{path.as_posix()}")
            if found is None:
                raise WorkflowUserError(
                    f"load_subcalls: 找不到子过程文件 workflows/{path.as_posix()}")
            path = Path(found)
        resolved = path.resolve()
        key = str(resolved)

        program = parse_file(resolved)
        # 递归解析 import 链（临时切换 base_dir，与 _load_and_validate 同语义）
        old_base, old_rel = self._base_dir, self._wf_rel_dir
        self._base_dir = resolved.parent
        self._wf_rel_dir = self._workflows_rel_dir(resolved)
        try:
            self._resolve_imports(program, {key})
        finally:
            self._base_dir, self._wf_rel_dir = old_base, old_rel
        for name, proc_def in program.procs.items():
            self._procs[name] = proc_def
            self._proc_sources[name] = program.source

        # 校验本次文件定义的所有过程（含覆盖的），确保修改后的静态校验仍然生效
        loaded_proc_names = set(program.procs.keys())
        loaded_bodies = [program.procs[n].body for n in loaded_proc_names]
        self._validate_named_waits_scoped(program.body, loaded_bodies)
        self._validate_refs_bound_scoped(program.body, loaded_proc_names, program.source)
        logger.debug(f"load_subcalls: {key} → 注册 {len(program.procs)} 个过程")

    def call_subcall(self, name: str, args: list | None = None):
        """调用已加载的 DSL 子过程，返回其 return 值（变量/output 隔离）

        与 DSL 内 call 语句同语义：子过程从干净变量表开始，结束恢复
        调用方快照；return 值直接返回给 Python 调用方（约定 return < 0
        表示错误）。未加载的过程直接报错，不走静默降级。
        """
        proc_def = self._procs.get(name)
        if proc_def is None:
            raise ValueError(
                f"call_subcall: 过程 {name} 未加载，请先 load_subcalls 对应 .wf 文件")
        logger.debug(f"--- call_subcall {name}({len(args or [])} args) ---")
        return_value, _callee_output = self._run_proc(proc_def, list(args or []))
        return return_value

    def _execute_dsl(self, wf_path: Path) -> dict:
        """加载并执行 .wf 文件"""
        self._ensure_workflow()
        resolved = Path(wf_path).resolve()
        program = self._load_and_validate(resolved)

        logger.info(f"=== DSL 工作流开始: {resolved.stem} ({len(program.body)} 条顶层指令, {len(self._procs)} 个过程) ===")

        try:
            self._exec_body(program.body)
        except _GotoSignal as sig:
            logger.error(f"goto 目标标签不存在: {sig.target}")
            return self.output
        except _ReturnSignal as sig:
            self.return_value = sig.value  # 捕获顶层 return 值
            logger.info(f"=== DSL 工作流正常返回，收集到 {len(self.output)} 项数据 ===")
            return self.output
        except _BreakSignal:
            # 停止请求在顶层语句边界触发时 _BreakSignal 会穿透到顶层，
            # 同样视为正常停止，返回已收集的部分结果
            logger.info(f"=== DSL 工作流被停止，收集到 {len(self.output)} 项数据 ===")
            return self.output

        logger.info(f"=== DSL 工作流完成，收集到 {len(self.output)} 项数据 ===")
        return self.output

    @staticmethod
    def _workflows_rel_dir(path: Path) -> str | None:
        """计算 wf 文件所在目录相对 workflows 根的 posix 路径

        命中 system/local 任一层的 workflows 目录时返回相对目录
        （顶层为 ""），否则 None（编辑器临时文件、外部绝对路径执行）。
        """
        resolver = get_resolver()
        for root in (resolver.system_dir, resolver.local_dir):
            try:
                rel = path.resolve().relative_to((root / "workflows").resolve())
            except (ValueError, OSError):
                continue
            return rel.parent.as_posix() if rel.parent != Path(".") else ""
        return None

    def _resolve_imports(self, program, import_stack: set):
        """递归解析 import 链，收集所有 def 到 self._procs

        相对路径先按「当前 wf 相对 workflows 根的目录 + import 路径」
        经 resolver 跨层解析（local 影子优先），未命中回退 _base_dir 拼接。
        import_stack: 当前 import 链中的文件路径集合，用于循环检测。
        """
        for imp in program.imports:
            imp_path = Path(imp.path)
            if not imp_path.is_absolute():
                resolved_cross = None
                if self._wf_rel_dir is not None:
                    rel = posixpath.normpath(posixpath.join(
                        self._wf_rel_dir, Path(imp.path).as_posix()))
                    if not rel.startswith(".."):
                        resolved_cross = get_resolver().resolve_read(
                            f"workflows/{rel}")
                if resolved_cross is not None:
                    imp_path = resolved_cross
                elif self._base_dir:
                    imp_path = self._base_dir / imp_path
            imp_resolved = str(imp_path.resolve())

            # 循环检测
            if imp_resolved in import_stack:
                chain = " -> ".join(sorted(import_stack)) + f" -> {imp_resolved}"
                raise WorkflowUserError(f"循环 import 检测: {chain}")

            # 解析导入文件
            imp_program = parse_file(imp_path)
            new_stack = import_stack | {imp_resolved}

            # 递归解析子文件的 import（临时切换 base_dir 与相对目录）
            old_base = self._base_dir
            old_rel = self._wf_rel_dir
            self._base_dir = imp_path.parent
            self._wf_rel_dir = self._workflows_rel_dir(imp_path)
            self._resolve_imports(imp_program, new_stack)
            self._base_dir = old_base
            self._wf_rel_dir = old_rel

            # 收集子文件的 def（平铺到当前命名空间）
            for name, proc_def in imp_program.procs.items():
                self._procs[name] = proc_def
                self._proc_sources[name] = imp_program.source
            logger.debug(f"import: {imp.path} → 注册 {len(imp_program.procs)} 个过程")

    def _validate_refs_bound(self, program):
        """解析后静态校验：脚本引用的坐标必须已在当前布局绑定

        遍历顶层语句与所有过程体（含 import 引入的），搜集全部静态引用，
        逐条比对布局中的区域 / 坐标点 / 方向 / 面板。key 拼错、把中文名当
        key 写、布局漏绑都在这里一次性列出，不进入执行阶段 —— 否则要等
        执行到那一行才炸，前面的步骤已经把游戏点到别处去了。
        """
        refs = collect_refs(program.body, self._procs,
                           proc_sources=self._proc_sources, source=program.source)
        problems = check_refs(refs, self._layout)
        if problems:
            raise WorkflowUserError(format_problems(problems))

    def _validate_named_waits(self, program):
        """解析后静态校验：wait 引用的命名等待参数必须已定义

        命名等待全部来自 delay_params（配置管理「等待参数」页，app.yaml delay_params 节），
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
                    if name not in self._delay_params:
                        missing.setdefault(name, node.line_no)
                case If():
                    self._collect_missing_waits(node.then_body, missing)
                    self._collect_missing_waits(node.else_body, missing)
                case For() | ForRange() | Loop() | WhileLoop() | UntilLoop():
                    self._collect_missing_waits(node.body, missing)
                case Try():
                    self._collect_missing_waits(node.body, missing)
                    self._collect_missing_waits(node.catch_body, missing)

    def _validate_named_waits_scoped(self, top_body: list, proc_bodies: list):
        """限定范围的命名等待校验：仅检查指定的语句体列表

        供 load_subcalls 使用，避免重复校验已加载过程。
        """
        missing: dict[str, int] = {}
        for body in [top_body, *proc_bodies]:
            self._collect_missing_waits(body, missing)
        if missing:
            detail = "、".join(f"{name}(行 {line})" for name, line in missing.items())
            raise WorkflowUserError(
                f"wait 引用了未定义的等待参数: {detail}，请先在配置管理→等待参数中定义")

    def _validate_refs_bound_scoped(self, top_body: list, proc_names: set, source: str):
        """限定范围的布局引用校验：仅检查顶层语句与指定过程

        供 load_subcalls 使用，避免重复校验已加载过程。
        """
        # 构造仅包含指定过程的子集用于 collect_refs
        scoped_procs = {n: self._procs[n] for n in proc_names if n in self._procs}
        scoped_sources = {n: self._proc_sources.get(n, source) for n in proc_names}
        refs = collect_refs(top_body, scoped_procs,
                           proc_sources=scoped_sources, source=source)
        problems = check_refs(refs, self._layout)
        if problems:
            raise WorkflowUserError(format_problems(problems))

    def _execute_python_workflow(self, workflow: BaseWorkflow) -> dict:
        """执行 Python 工作流实例"""
        workflow.reset_state()
        workflow.variables.update(self.variables)
        # 注入引擎引用，使工作流内调用的 UI 交互内置函数
        # （confirm/pause/input）能经 _ui_callback 走 Qt 主线程桥
        workflow._engine = self
        # 工作流实例直接作为游戏操作委托：期间经 call_subcall 执行的
        # DSL 子过程与工作流本体共用同一套原语（延迟参数/测试替身一致）
        self._workflow = workflow
        logger.info(f"=== Python 工作流开始: {workflow.__class__.__name__} ===")
        try:
            result = workflow.run()
        except Exception as e:
            logger.error(f"Python 工作流异常: {e}\n{traceback.format_exc()}")
            result = workflow.output
        finally:
            # 清理：避免引擎复用时泄漏过期工作流引用
            self._workflow = None
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
            case WaitStable():
                self._exec_wait_stable(node)
            case Align():
                self._exec_align(node)
            case Scan():
                self._exec_scan(node)
            case Recognize():
                self._exec_recognize(node)
            case Find():
                self._exec_find(node)
            case Collect():
                self._exec_collect(node)
            case Log():
                if isinstance(node.message, FuncCall):
                    msg = str(self._call_func(node.message))
                else:
                    val = self._resolve(node.message)
                    msg = "null" if val is None else val
                _log_func = {
                    "debug": logger.debug,
                    "info": logger.info,
                    "warn": logger.warning,
                    "error": logger.error,
                }.get(node.level, logger.info)
                _log_func(msg)
            case Screenshot():
                self._exec_screenshot()
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
                value = self._resolve(node.value) if node.value is not None else None
                raise _ReturnSignal(value)
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

    def _exec_screenshot(self):
        """截取当前画面并保存到 logs/image/"""
        img = self._capture.capture()
        if img is None:
            logger.warning("screenshot: 截图失败")
            return

        # 生成文件名：image + 日期时间精确到毫秒
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]  # 毫秒
        filename = f"image_{timestamp}.png"

        # 保存目录
        out_dir = PROJECT_ROOT / "logs" / "image"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / filename

        cv2.imwrite(str(out_path), img)
        logger.info(f"screenshot: 已保存 {filename}")
