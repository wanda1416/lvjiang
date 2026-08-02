"""workflows 测试共享 fixture

make_engine / run 被多个测试文件使用，集中定义避免重复。
"""

from unittest.mock import MagicMock

from lvjiang.workflows.engine import WorkflowEngine
from lvjiang.workflows.grammar import parse_text


def make_engine(**overrides) -> WorkflowEngine:
    """创建最小化引擎实例（capture/layout/ocr/input 全 mock）

    overrides 可覆盖任意构造器参数（如 delay_params=...）。
    """
    capture = MagicMock()
    capture.get_capture_size.return_value = (1920, 1080)
    layout = MagicMock()
    layout.get_canvas.return_value = MagicMock(
        x_ratio=0, y_ratio=0, w_ratio=1, h_ratio=1)
    defaults = dict(
        capture=capture, ocr=MagicMock(), input_ctrl=MagicMock(),
        layout=layout, input_sim=MagicMock(), delay_params={},
    )
    defaults.update(overrides)
    return WorkflowEngine(**defaults)


def run(code: str, initial: dict | None = None) -> dict:
    """执行 DSL 片段并返回变量表"""
    eng = make_engine()
    eng.variables = dict(initial or {})
    program = parse_text(code)
    # 注册 def 定义
    eng._procs = dict(program.procs)
    eng._exec_body(program.body)
    return eng.variables
