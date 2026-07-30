"""工作流基类主体 - 生命周期、变量、内置函数调用，组合各操作 Mixin"""

from typing import Callable, Optional

from ...config import DelayConfig
from ...core.capture_base import CaptureBackend
from ...core.ocr import OCREngine
from ...core.input_base import InputBackend
from ...core.scene_registry import Layout
from .. import builtins  # noqa: F401  触发内置函数注册
from ..align import GridAlignment
from .recognition import _RecognitionMixin
from .actions import _ActionMixin
from .coords import _CoordMixin
from .panel import _PanelMixin


class BaseWorkflow(_RecognitionMixin, _ActionMixin, _CoordMixin, _PanelMixin):
    """工作流基类

    运行时状态：
    - variables: 用户变量表（scan/eval 赋值，所有数据显式存储）
    - output: 收集的输出数据字典（由 collect 语句写入，key 为 alias 或变量名）
    """

    # 类级别共享：MaterialRecognizer 跨所有实例复用，避免重复加载参考图
    _shared_material_recognizer = None

    def __init__(
        self,
        capture: CaptureBackend,
        ocr: OCREngine,
        input_ctrl: InputBackend,
        layout: Layout,
        delay_config: DelayConfig | None = None,
        window_left: int = 0,
        window_top: int = 0,
        stop_check: Optional[Callable[[], bool]] = None,
    ):
        self._capture = capture
        self._ocr = ocr
        self._input = input_ctrl
        self._layout = layout
        self._delay = delay_config or DelayConfig()
        self._window_left = window_left
        self._window_top = window_top
        self._stop_check = stop_check or (lambda: False)

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

    @property
    def is_stopped(self) -> bool:
        """是否请求了停止"""
        return self._stop_check()

    # ─── 材料识别器（类级别共享） ──────────────────────────

    @property
    def material_recognizer(self):
        """延迟构造 MaterialRecognizer（类级别共享，跨工作流运行复用）"""
        if BaseWorkflow._shared_material_recognizer is None:
            from lvjiang.apps.yysls.core.material_recognizer import MaterialRecognizer
            BaseWorkflow._shared_material_recognizer = MaterialRecognizer(self._ocr)
        return BaseWorkflow._shared_material_recognizer

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
        # 检查函数是否需要 engine 注入（第一参数名为 _engine）
        import inspect
        sig = inspect.signature(fn)
        params = list(sig.parameters.keys())
        if params and params[0] == '_engine' and engine is not None:
            return fn(engine, *args)
        if params and params[0] == '_wf':
            return fn(self, *args)
        return fn(*args)
