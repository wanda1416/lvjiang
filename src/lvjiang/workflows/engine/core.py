"""WorkflowEngine 主类：生命周期、执行入口与语句分发"""

import posixpath
import threading
import traceback
from dataclasses import fields, is_dataclass
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
from ...core.input_trace import InputTrace, InputTraceError, load_input_trace
from ...core.key_validation import validate_key_name
from ...core.layout_models import Layout
from ...core.ocr import OCREngine
from ...core.recognizers import ReferenceRecognizer
from ..align import GridAlignment
from ..base import BaseWorkflow
from ..errors import WorkflowExecutionError
from ..grammar.ast_nodes import (
    Align,
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
    MouseButton,
    Move,
    Place,
    Press,
    ProcDef,
    Recognize,
    ReplayInputTrace,
    Return,
    Scan,
    Screenshot,
    Scroll,
    Try,
    UntilLoop,
    Wait,
    WaitStable,
    WhileLoop,
)
from ..grammar.parser import parse_file
from ..static_check import check_refs, format_problems
from ..workflow_references import collect_refs
from .actions import _ActionsMixin
from .control_flow import _ControlFlowMixin
from .data_ops import _DataOpsMixin
from .evaluation import _EvalMixin
from .key_state import KeyStateRegistry
from .panel import _PanelMixin
from .signals import (
    WorkflowUserError,
    _BreakSignal,
    _ContinueSignal,
    _GotoSignal,
    _ReturnSignal,
)

# ─── 引擎 ─────────────────────────────────────────────────


#: import 路径的非法开头。逐条都是能逃出 workflows 沙盒的写法。
_IMPORT_BAD_PREFIX = ("/", "\\", "~", ".")


def _normalize_import_path(raw: str) -> str:
    """校验并规范化 import 路径，返回相对 workflows 根的 posix 路径。

    只接受「以文件名开头的相对路径」，例如 ``subcall/navigation.wf``。

    不能用 ``Path.is_absolute()`` 判绝对路径——它的结果随运行平台变：
    ``C:/evil.wf`` 与 ``\\\\server\\share\\x.wf`` 在 Linux 上判定为「非绝对」，
    而开发与 CI 都在 Linux、用户却在 Windows，这类写法会静默漏过。
    所以这里用与平台无关的字符串规则。

    两道检查缺一不可：开头检查挡住 ``/`` ``~`` ``..`` 与盘符，
    normpath 后再查一次 ``..`` 挡住 ``subcall/../../etc/x.wf`` 这种
    「以文件名开头、却在中段逃逸」的写法。
    """
    raw = (raw or "").strip()
    if not raw:
        raise WorkflowUserError("import 路径为空")
    if raw.startswith(_IMPORT_BAD_PREFIX) or ":" in raw or "\\" in raw:
        raise WorkflowUserError(
            f"import 路径非法: {raw!r}。"
            f"必须是相对 workflows 根的路径、以文件名开头，"
            f"不能以 / \\ ~ . 开头，也不能含盘符或反斜杠。"
            f"例如 subcall/navigation.wf")
    rel = posixpath.normpath(raw)
    if rel.startswith("..") or rel.startswith("/"):
        raise WorkflowUserError(
            f"import 路径越界: {raw!r} → {rel}，不能离开 workflows 目录")
    return rel


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
        run_env: str = "",
        window_left: int = 0,
        window_top: int = 0,
        stop_check: Callable[[], bool] | None = None,
        pause_event: threading.Event | None = None,
    ):
        # 硬件后端（直接持有）
        self._capture = capture
        self._ocr = ocr
        self._input = input_ctrl
        self._layout = layout
        self._input_sim = input_sim or InputSimConfig()
        self._delay_params = delay_params or {}
        # 本次运行使用的环境快照。工作流执行期间只读此内存值，绝不回读
        # session.json，避免 UI 切换或其他进程写配置污染已启动实例。
        self.run_env = str(run_env)
        self._window_left = window_left
        self._window_top = window_top
        self._stop_check = stop_check or (lambda: False)
        # 暂停事件（由 UI 层注入）：set=运行，clear=暂停阻塞
        self._pause_event = pause_event
        # 引擎生命周期服务：DSL 委托与 Python 类工作流共享图库匹配缓存。
        self._reference_recognizer = ReferenceRecognizer(self._ocr)
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
        # 高精度轨迹解码缓存：{resolved path: InputTrace}，校验期填充，
        # 执行期 _exec_replay_input_trace 直接复用，避免同一份 .lvtrace
        # 在一次运行内解码两次；每次 _load_and_validate 重新执行前清空。
        self._input_trace_cache: dict[Path, InputTrace] = {}
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
        # 按键状态注册表（press 指令用，懒初始化绑定当前 backend）
        self._key_registry: KeyStateRegistry | None = None
        # 原始 mouse down/up 的状态；工作流任何方式退出都必须释放，避免把
        # 物理鼠标键留在按下状态。
        self._pressed_mouse_buttons: set[str] = set()
        # 调试钩子（脚本工作台注入）：每条语句执行前回调 (line_no, variables 快照)。
        # step_mode=True 时每条语句前把 pause_event 清掉再等——即"单步"：
        # UI 每 set 一次事件，引擎只往前走一条。两者都在工作流线程里触发。
        self.statement_hook: Callable[[int, dict], None] | None = None
        self.step_mode: bool = False

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
                pause_event=self._pause_event,
                reference_recognizer=self._reference_recognizer,
            )
        return self._workflow

    def _debug_before_stmt(self, node) -> None:
        """语句级调试钩子：上报当前行 + 变量快照；单步模式下在此停住等 UI 放行

        快照是浅拷贝：UI 线程只读展示，引擎继续改自己的 dict 互不干扰。
        """
        if self.statement_hook is None and not self.step_mode:
            return
        line_no = getattr(node, "line_no", 0) or 0
        # 必须在通知 UI 前清除事件。若先 emit，UI 的“单步”可能立刻 set，
        # 随后又被工作线程 clear，导致这次放行永久丢失。
        pause_event = self._pause_event if self.step_mode else None
        if pause_event is not None:
            pause_event.clear()
        if self.statement_hook is not None:
            try:
                self.statement_hook(line_no, dict(self.variables))
            except Exception as e:  # noqa: BLE001 — 调试面板出错不能把脚本带崩
                logger.warning(f"statement_hook 异常: {e}")
        if pause_event is not None:
            self._wait_if_paused()
            # 「停止」是靠 set 事件把阻塞中的引擎唤醒的——醒来后必须再看一眼停止标志，
            # 否则会把当前这条语句执行掉才在下一条边界退出
            if self._stop_check():
                raise _BreakSignal()

    def _wait_if_paused(self):
        """暂停检查：若 pause_event 未 set 则阻塞等待，期间响应 stop_check

        与 _stop_check 的区别：
        - _stop_check 返回 True → raise _BreakSignal 终止
        - _wait_if_paused 阻塞 → 直到 resume（event set）或 stop → 若 stop 则 raise
        """
        if self._pause_event is None:
            return
        while not self._pause_event.is_set():
            if self._stop_check():
                raise _BreakSignal()
            self._pause_event.wait(timeout=1.0)

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

        try:
            # Python 工作流实例
            if isinstance(source, BaseWorkflow):
                return self._execute_python_workflow(source)

            # DSL .wf 文件
            source_path = Path(source)
            if source_path.suffix == ".wf":
                return self._execute_dsl(source_path)
            raise ValueError(f"不支持的执行源: {source}")
        finally:
            # 所有退出路径统一释放按键（正常/异常/取消/超时）
            if self._key_registry:
                self._key_registry.release_all()
            for button in tuple(self._pressed_mouse_buttons):
                try:
                    self._input.mouse_button(button, False)
                except Exception:  # noqa: BLE001 - 清理一个键失败不能阻塞其他键
                    logger.exception(f"释放鼠标键 {button} 失败")
            self._pressed_mouse_buttons.clear()

    def validate_only(self, wf_path: Path | str) -> None:
        """只解析与静态校验 .wf，不执行任何动作

        供上机前预检与 CI 门禁使用：布局漏绑区域、key 拼错、命名等待参数未
        定义这类错误，过去只有真执行到那一行才炸 —— 而实机失败时游戏已经被
        点到别处去了。校验通过返回 None，不通过抛 WorkflowUserError（消息含
        全部问题清单）。

        判据与 _execute_dsl 共用 _load_and_validate，不会出现「预检放过、
        上机仍炸」的偏差。

        唯一的差别是**预检更严**：这里连没被调用的过程也一起校验
        （``reachable_only=False``），执行时只查真正可达的。方向是安全的
        ——预检更严只会「报了但其实不会炸」，反过来才是事故。这样库函数
        （如 page_detection.wf 里那些 is_in_*_page）的 key 拼错、布局漏绑
        仍能被 CI 门禁发现，而用户执行某个脚本时不会被无关页面挡住。
        """
        self._load_and_validate(Path(wf_path).resolve(), reachable_only=False)

    def _load_and_validate(self, resolved: Path, *, reachable_only: bool = True):
        """解析 .wf（含 import 链与 def 注册）并跑两道静态校验，返回 program

        _execute_dsl 与 validate_only 共用此方法：两者对「什么算合法脚本」的
        判据必须是同一份。
        """
        self._base_dir = resolved.parent
        self._wf_rel_dir = self._workflows_rel_dir(resolved)
        program = parse_file(resolved)

        # 解析 import 图：同一物理文件在本次加载中只处理一次，
        # 同名过程若来自不同文件则报错，不让 import 顺序暗中决定行为。
        loaded_procs: dict[str, ProcDef] = {}
        loaded_sources: dict[str, str] = {}
        root_key = str(resolved)
        self._resolve_imports(
            program,
            import_stack=[root_key],
            imported_files={root_key},
            loaded_procs=loaded_procs,
            loaded_sources=loaded_sources,
        )
        # 注册本地 def；主工作流与导入过程同名也属于冲突。
        for name, proc_def in program.procs.items():
            self._register_loaded_proc(
                name, proc_def, program.source, loaded_procs, loaded_sources)

        self._procs = loaded_procs
        self._proc_sources = loaded_sources
        # 本次重新加载：上一次运行缓存的轨迹解码结果作废，校验期重新填充
        self._input_trace_cache = {}

        # 静态校验：wait 引用的命名等待参数必须已定义，未定义直接报错不执行
        self._validate_named_waits(program)
        # 静态校验：press 的字面量按键必须存在于公共合法键名库。
        # 变量按键无法预知，仍由执行时 normalize_key 校验。
        self._validate_literal_press_keys(program)
        # 静态校验：脚本引用的场景 / 区域 / 方向 / 面板必须已在当前布局绑定坐标
        self._validate_refs_bound(program, reachable_only=reachable_only)
        # 高精度轨迹在执行任何动作前完成路径与内容校验（含 import 引入的过程）。
        self._validate_input_traces(program)
        return program

    def _resolve_input_trace_path(
        self, reference: str, base_dir: Path | None = None,
    ) -> Path:
        """相对指定目录解析轨迹，禁止绝对路径、非 lvtrace 文件，
        以及借 ``..`` 穿出工作流目录树引用任意"lvtrace"同名目录。

        base_dir 省略时取当前 self._base_dir（顶层语句执行期即为此值，
        _run_proc 执行过程体期间会临时切到该过程定义文件所在目录）；
        校验阶段则显式传入每个过程各自的定义文件目录，见
        _validate_input_traces_scoped。
        """
        if base_dir is None:
            base_dir = self._base_dir
        path = Path(reference)
        if path.is_absolute() or path.suffix.lower() != ".lvtrace":
            raise WorkflowUserError(
                f"input_trace 必须是相对 .lvtrace 路径: {reference}")
        if base_dir is None:
            raise WorkflowUserError("input_trace 缺少工作流基准目录")
        resolved = (base_dir / path).resolve()
        if resolved.parent.name != "lvtrace":
            raise WorkflowUserError(
                f"input_trace 必须位于 lvtrace 文件夹: {reference}")
        # 已知 workflows 根（system/local）时，轨迹必须落在该根的 lvtrace/
        # 目录下——与 save_input_trace_bundle 的落盘约定一致，不允许借多层
        # ".." 指向根目录树之外某个恰好也叫 lvtrace 的目录。
        # 根外文件（测试用任意目录、编辑器临时文件）退化为不得逃出 base_dir。
        root = self._workflows_root_for(base_dir)
        if root is not None:
            if resolved.parent != root / "lvtrace":
                raise WorkflowUserError(
                    f"input_trace 越权引用工作流目录之外的文件: {reference}")
        else:
            base = base_dir.resolve()
            try:
                resolved.relative_to(base)
            except ValueError:
                raise WorkflowUserError(
                    f"input_trace 越权引用工作流目录之外的文件: {reference}"
                ) from None
        return resolved

    @staticmethod
    def _workflows_root_for(base_dir: Path) -> Path | None:
        """base_dir 所属的 workflows 根目录（system/local 二选一）

        未命中已知根（测试用任意目录、编辑器临时文件）时返回 None。
        """
        resolver = get_resolver()
        resolved_base = base_dir.resolve()
        for root in (resolver.system_dir, resolver.local_dir):
            candidate = (root / "workflows").resolve()
            try:
                resolved_base.relative_to(candidate)
            except (ValueError, OSError):
                continue
            return candidate
        return None

    def _proc_bodies_with_base_dir(
        self, procs: dict[str, ProcDef],
    ) -> list[tuple[list, Path | None]]:
        """把 {name: ProcDef} 展开成 (过程体, 定义文件所在目录) 列表

        定义文件目录来自 _proc_sources[name]（parse_file 记录的绝对
        路径）——过程可能来自 import 引入的另一个 .wf，relative 路径
        必须相对它自己的文件解析，不能沿用调用方/根文件的目录。
        未命中 _proc_sources（测试直接构造 ProcDef 不经注册）时退回
        self._base_dir。
        """
        result = []
        for name, proc in procs.items():
            source = self._proc_sources.get(name)
            base = Path(source).parent if source is not None else self._base_dir
            result.append((proc.body, base))
        return result

    def _validate_input_traces(self, program) -> None:
        """静态校验：input_trace 路径与内容必须在执行任何动作前全部合法

        遍历顶层语句与所有已加载过程体（含 import 引入的），与
        _validate_named_waits / _validate_refs_bound 同样的覆盖范围——
        否则导入文件里的 replay 只能等真正执行到那一行才暴露问题。
        """
        bodies = [(program.body, self._base_dir)]
        bodies += self._proc_bodies_with_base_dir(self._procs)
        self._validate_input_traces_scoped(bodies)

    def _validate_input_traces_scoped(
        self, bodies: list[tuple[list, Path | None]],
    ) -> None:
        """限定范围的 input_trace 校验：每个语句体各自带上其定义文件目录

        供 load_subcalls 使用，避免重复校验已加载过程；也是
        _validate_input_traces 的公共实现。校验期顺带把解码结果缓存进
        _input_trace_cache，执行期 _exec_replay_input_trace 直接复用，
        避免同一份 .lvtrace 在一次运行内解码两次。
        """
        def walk(value):
            if isinstance(value, ReplayInputTrace):
                yield value
                return
            if isinstance(value, dict):
                for item in value.values():
                    yield from walk(item)
                return
            if isinstance(value, (list, tuple)):
                for item in value:
                    yield from walk(item)
                return
            if is_dataclass(value):
                for field in fields(value):
                    yield from walk(getattr(value, field.name))

        for body, base_dir in bodies:
            for node in walk(body):
                path = self._resolve_input_trace_path(node.path, base_dir=base_dir)
                try:
                    trace = load_input_trace(path)
                except InputTraceError as exc:
                    raise WorkflowUserError(str(exc)) from exc
                self._input_trace_cache[path] = trace
                self._input_trace_cache[path] = trace

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
        # 每次显式调用都创建新的加载集合：本次 import 图内去重，
        # 但下一次 load_subcalls 仍会重新解析，保留热更新语义。
        loaded_procs: dict[str, ProcDef] = {}
        loaded_sources: dict[str, str] = {}
        old_base, old_rel = self._base_dir, self._wf_rel_dir
        self._base_dir = resolved.parent
        self._wf_rel_dir = self._workflows_rel_dir(resolved)
        try:
            self._resolve_imports(
                program,
                import_stack=[key],
                imported_files={key},
                loaded_procs=loaded_procs,
                loaded_sources=loaded_sources,
            )
        finally:
            self._base_dir, self._wf_rel_dir = old_base, old_rel
        for name, proc_def in program.procs.items():
            self._register_loaded_proc(
                name, proc_def, program.source, loaded_procs, loaded_sources)

        # 临时合并后校验整个加载单元；若校验失败则恢复原过程表，
        # 避免显式热加载失败时留下半套新定义。
        previous_procs = self._procs.copy()
        previous_sources = self._proc_sources.copy()
        self._procs.update(loaded_procs)
        self._proc_sources.update(loaded_sources)

        loaded_proc_names = set(loaded_procs)
        loaded_bodies = [proc.body for proc in loaded_procs.values()]
        try:
            self._validate_named_waits_scoped(program.body, loaded_bodies)
            self._validate_refs_bound_scoped(
                program.body, loaded_proc_names, program.source)
            self._validate_input_traces_scoped(
                [(program.body, resolved.parent)]
                + self._proc_bodies_with_base_dir(loaded_procs))
        except Exception:
            self._procs = previous_procs
            self._proc_sources = previous_sources
            raise
        logger.debug(f"load_subcalls: {key} → 注册 {len(loaded_procs)} 个过程")

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

    @staticmethod
    def _register_loaded_proc(
        name: str,
        proc_def: ProcDef,
        source: str,
        loaded_procs: dict[str, ProcDef],
        loaded_sources: dict[str, str],
    ) -> None:
        """将过程注册到本次加载单元，拒绝跨文件的隐式覆盖。"""
        previous_source = loaded_sources.get(name)
        if previous_source is not None:
            raise WorkflowUserError(
                f"过程 {name} 定义冲突:\n"
                f"- {previous_source}\n"
                f"- {source}"
            )
        loaded_procs[name] = proc_def
        loaded_sources[name] = source

    def _resolve_imports(
        self,
        program,
        import_stack: list[str],
        imported_files: set[str],
        loaded_procs: dict[str, ProcDef],
        loaded_sources: dict[str, str],
    ) -> None:
        """递归解析 import 图，收集本次加载单元的所有 def。

        import 路径一律**相对 workflows 根**（不是相对当前文件所在目录），
        经 resolver 跨层解析（local 影子优先 → system）。所以 subcall 内部
        互相 import 也要写全 ``subcall/xxx.wf``。

        这样每条 import 都独立地从根算起，不累积路径，越界检查退化成对
        单条路径的判断，也不需要在递归时维护「当前目录」这类状态。

        import_stack 保留当前递归链的真实顺序，用于循环检测；
        imported_files 记录本次加载已解析的规范绝对路径，用于菱形依赖去重。
        """
        for imp in program.imports:
            rel = _normalize_import_path(imp.path)
            resolved = get_resolver().resolve_read(f"workflows/{rel}")
            if resolved is None:
                raise WorkflowUserError(
                    f"import 找不到文件: {rel}"
                    f"（在 config/local 与 config/system 的 workflows/ 下均未找到）")
            imp_path = Path(resolved)
            imp_resolved = str(imp_path.resolve())

            # 循环检测
            if imp_resolved in import_stack:
                cycle_start = import_stack.index(imp_resolved)
                chain = " -> ".join(
                    [*import_stack[cycle_start:], imp_resolved])
                raise WorkflowUserError(f"循环 import 检测: {chain}")

            # 同一规范绝对路径在一次加载中只处理一次。
            if imp_resolved in imported_files:
                logger.debug(f"import: {imp.path} → 已加载，跳过")
                continue
            imported_files.add(imp_resolved)

            # 解析导入文件
            imp_program = parse_file(imp_path)
            new_stack = [*import_stack, imp_resolved]

            # 递归：路径恒从根算起，无需切换任何「当前目录」状态
            self._resolve_imports(
                imp_program,
                new_stack,
                imported_files,
                loaded_procs,
                loaded_sources,
            )

            # 收集子文件的 def（平铺到当前命名空间）
            for name, proc_def in imp_program.procs.items():
                self._register_loaded_proc(
                    name,
                    proc_def,
                    imp_program.source,
                    loaded_procs,
                    loaded_sources,
                )
            logger.debug(f"import: {imp.path} → 注册 {len(imp_program.procs)} 个过程")

    def _validate_refs_bound(self, program, *, reachable_only: bool = True):
        """解析后静态校验：脚本引用的坐标必须已在当前布局绑定

        遍历顶层语句与所有过程体（含 import 引入的），搜集全部静态引用，
        逐条比对布局中的区域 / 坐标点 / 方向 / 面板。key 拼错、把中文名当
        key 写、布局漏绑都在这里一次性列出，不进入执行阶段 —— 否则要等
        执行到那一行才炸，前面的步骤已经把游戏点到别处去了。
        """
        refs = collect_refs(program.body, self._procs,
                           proc_sources=self._proc_sources, source=program.source,
                           reachable_only=reachable_only)
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

    def _validate_literal_press_keys(self, program) -> None:
        """校验顶层及 import/def 中的 ``press \"KEY\"`` 字面量。"""
        problems: list[str] = []
        self._collect_invalid_press_keys(
            program.body, str(program.source or ""), problems)
        for name, proc in self._procs.items():
            self._collect_invalid_press_keys(
                proc.body, self._proc_sources.get(name, ""), problems)
        if problems:
            raise WorkflowUserError(
                "press 使用了非法按键：\n" + "\n".join(problems))

    def _collect_invalid_press_keys(
        self, stmts: list, source: str, problems: list[str],
    ) -> None:
        """递归收集非法 press 字面量；``press $var`` 不在静态范围内。"""
        for node in stmts:
            if isinstance(node, Press):
                for key in node.keys or (node.key,):
                    if not isinstance(key, str):
                        continue
                    try:
                        validate_key_name(key)
                    except ValueError:
                        location = (f"{source}:{node.line_no}"
                                    if source else f"行 {node.line_no}")
                        problems.append(f"{location}: {key!r}")
            if isinstance(node, If):
                self._collect_invalid_press_keys(
                    node.then_body, source, problems)
                self._collect_invalid_press_keys(
                    node.else_body, source, problems)
            elif isinstance(node, (For, ForRange, Loop, WhileLoop, UntilLoop)):
                self._collect_invalid_press_keys(node.body, source, problems)
            elif isinstance(node, Try):
                self._collect_invalid_press_keys(node.body, source, problems)
                self._collect_invalid_press_keys(
                    node.catch_body, source, problems)

    def _collect_missing_waits(self, stmts: list, missing: dict[str, int]):
        """递归收集语句体中引用未定义命名等待的 Wait / WaitStable 节点"""
        for node in stmts:
            match node:
                case Wait(delay=Literal(value=str() as name)):
                    if name not in self._delay_params:
                        missing.setdefault(name, node.line_no)
                case WaitStable():
                    # wait stable 各参数也可能是 @命名延迟，需校验
                    for field in (node.timeout, node.threshold, node.interval,
                                  node.stable_duration, node.least):
                        if (isinstance(field, Literal)
                                and isinstance(field.value, str)
                                and field.value not in self._delay_params):
                            missing.setdefault(field.value, node.line_no)
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
        # reset_state() 会刷新 reference 服务，必须先完成生命周期注入。
        workflow._reference_recognizer = self._reference_recognizer
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
        except _BreakSignal:
            # DSL 子过程和动作等待以 _BreakSignal 传递停止请求。Python 工作流
            # 调用这些能力时，信号会越过 call_subcall 回到此边界；它是正常
            # 用户停止，不应落入通用异常日志。
            logger.info("Python 工作流收到停止请求")
            result = workflow.output
        except Exception as exc:
            # 意外异常必须作为失败传播到运行线程/UI。过去这里返回部分 output，
            # 调用方会按“正常完成”保存 session 和结果，掩盖真实失败。
            #
            # 默认把失败前已产生的输出记进日志：异常一路抛到 UI 后走的是
            # 「异常退出」分支，那条路不落盘结果。拥有专用报告且输出很大的
            # 工作流可关闭该日志；partial_output 仍保留在异常对象中。
            name = workflow.__class__.__name__
            log_partial_output = getattr(
                workflow, "LOG_PARTIAL_OUTPUT_ON_FAILURE", True)
            if workflow.output and log_partial_output:
                # default=str：output 里可能有 dataclass / Path 等非 JSON 类型。
                # 整段再套 try：日志失败绝不能盖住真正的异常。
                try:
                    import json
                    dumped = json.dumps(
                        workflow.output, ensure_ascii=False,
                        indent=2, default=str)
                except Exception:  # noqa: BLE001 排障日志不值得再抛
                    dumped = repr(workflow.output)
                logger.error(f"Python 工作流 {name} 失败前已产生的输出:\n{dumped}")
            raise WorkflowExecutionError(name, workflow.output) from exc
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
            self._wait_if_paused()  # 暂停检查：阻塞直到恢复或停止

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
        self._wait_if_paused()  # 暂停检查
        self._debug_before_stmt(node)
        match node:
            case Click():
                self._exec_click(node)
            case MouseButton():
                self._exec_mouse_button(node)
            case Place():
                self._exec_place(node)
            case Move():
                self._exec_move(node)
            case ReplayInputTrace():
                self._exec_replay_input_trace(node)
            case Scroll():
                self._exec_scroll(node)
            case Drag():
                self._exec_drag(node)
            case Press():
                self._exec_press(node)
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

    def _exec_replay_input_trace(self, node: ReplayInputTrace):
        """绕过逐条 DSL 调度，一次交给桌面输入后端实时回放。"""
        replay = getattr(self._input, "replay_input_trace", None)
        if replay is None:
            raise WorkflowUserError(
                "replay input_trace 仅支持桌面 SendInput 前台模式")
        path = self._resolve_input_trace_path(node.path)
        trace = self._input_trace_cache.get(path)
        if trace is None:
            # 缓存未命中：正常运行下校验期已填充过，这里只覆盖 call_subcall
            # 等绕过完整校验流程的边界场景，兜底重新解码一次。
            try:
                trace = load_input_trace(path)
            except InputTraceError as exc:
                raise WorkflowUserError(str(exc)) from exc
        width, height = self._capture.get_capture_size()
        canvas = self._layout.get_canvas()
        replay(
            trace,
            canvas_width=max(1, round(canvas.w_ratio * width)),
            canvas_height=max(1, round(canvas.h_ratio * height)),
            stop_check=self._stop_check,
            pause_event=self._pause_event,
        )

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
