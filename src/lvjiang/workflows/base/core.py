"""工作流基类主体 - 生命周期、变量、内置函数调用，组合各操作 Mixin"""

import threading
from typing import TYPE_CHECKING, Callable, Optional

if TYPE_CHECKING:
    from ...core.recognizers import ReferenceRecognizer
    from ..engine.core import WorkflowEngine

from ...core.capture_base import CaptureBackend
from ...core.config import DelayParam, InputSimConfig
from ...core.input_base import InputBackend
from ...core.layout_models import Layout
from ...core.ocr import OCREngine
from .. import builtins  # noqa: F401  触发内置函数注册
from ..align import GridAlignment
from .actions import _ActionMixin
from .coords import _CoordMixin
from .panel import _PanelMixin
from .recognition import _RecognitionMixin


class BaseWorkflow(_RecognitionMixin, _ActionMixin, _CoordMixin, _PanelMixin):
    """工作流基类

    运行时状态：
    - variables: 用户变量表（scan/eval 赋值，所有数据显式存储）
    - output: 收集的输出数据字典（由 collect 语句写入，key 为 alias 或变量名）
    """

    # 执行引擎引用（engine._execute_python_workflow 注入，供内置
    # 函数的 UI 交互 confirm/pause/input 走 Qt 主线程桥）
    _engine: Optional["WorkflowEngine"] = None

    @property
    def engine(self) -> Optional["WorkflowEngine"]:
        """获取执行引擎引用（供 subcall 桥等需要访问引擎的场景使用）"""
        return self._engine

    def __init__(
        self,
        capture: CaptureBackend,
        ocr: OCREngine,
        input_ctrl: InputBackend,
        layout: Layout,
        input_sim: InputSimConfig | None = None,
        delay_params: dict[str, DelayParam] | None = None,
        window_left: int = 0,
        window_top: int = 0,
        stop_check: Optional[Callable[[], bool]] = None,
        pause_event: threading.Event | None = None,
        reference_recognizer: "ReferenceRecognizer | None" = None,
    ):
        self._capture = capture
        self._ocr = ocr
        self._input = input_ctrl
        self._layout = layout
        self._input_sim = input_sim or InputSimConfig()
        self._delay_params = delay_params or {}
        self._window_left = window_left
        self._window_top = window_top
        self._stop_check = stop_check or (lambda: False)
        # 暂停事件（由 UI 层注入）：set=运行，clear=暂停阻塞
        self._pause_event = pause_event
        self._reference_recognizer = reference_recognizer

        # 运行时状态
        self.output: dict = {}  # collect 语句写入的输出字典
        self.variables: dict = {}
        # panel 对齐缓存：(scene_key, panel_key) → GridAlignment
        self._panel_alignments: dict[tuple[str, str], GridAlignment] = {}

    def run(self) -> dict:
        """执行工作流（子类重写）

        Returns:
            dict: collect 累积结果
        """
        raise NotImplementedError

    def reset_state(self):
        """重置运行时状态（在 run 开始前调用）"""
        self.output = {}
        self.variables = {}
        if self._reference_recognizer is not None:
            self._reference_recognizer.reload()

    @property
    def is_stopped(self) -> bool:
        """是否请求了停止（检查前先过暂停检查点）"""
        self._wait_if_paused()  # 暂停时阻塞，恢复或停止后才继续
        return self._stop_check()

    def _wait_if_paused(self):
        """暂停检查：若 pause_event 未 set 则阻塞等待，期间响应 stop_check

        Python 工作流子类在关键操作前调用此方法以支持暂停/恢复。
        """
        if self._pause_event is None:
            return
        while not self._pause_event.is_set():
            if self._stop_check():
                from ..engine.signals import _BreakSignal
                raise _BreakSignal()
            self._pause_event.wait(timeout=1.0)

    # ─── 通用参考图识别器 ──────────────────────────────────

    @property
    def reference_recognizer(self):
        """返回 engine 注入的 reference 服务；独立使用时按实例懒构造。"""
        if self._reference_recognizer is None:
            from ...core.recognizers import ReferenceRecognizer
            self._reference_recognizer = ReferenceRecognizer(self._ocr)
        return self._reference_recognizer

    # ─── 变量与函数调用 ────────────────────────────────────

    def get_variable(self, name: str):
        """获取变量值"""
        return self.variables.get(name)

    def set_variable(self, name: str, value):
        """设置变量"""
        self.variables[name] = value

    def call_function(self, func_name: str, args: list, engine=None) -> any:
        """调用内置函数

        若函数第一参数名为 _engine，自动注入当前 Engine 实例。
        若函数第一参数名为 _wf，自动注入 workflow 实例（兼容旧代码）。
        """
        fn = builtins.get_function(func_name)
        if fn is None:
            available = ", ".join(builtins.list_functions())
            raise ValueError(f"未知内置函数: {func_name}，可用函数: {available}")
        # 注入类型已在注册时缓存到 fn._inject，无需每次反射
        inject = getattr(fn, '_inject', None)
        if inject == 'engine':
            # engine 为 None 时也要占位注入，否则用户参数会错位绑到 _engine 形参上
            return fn(engine, *args)
        if inject == 'wf':
            return fn(self, *args)
        return fn(*args)
