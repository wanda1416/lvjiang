"""wf 加载期命名等待参数静态校验测试

_execute_dsl 在解析与 import 展开后、执行前遍历全部语句体，
wait 引用的命名等待参数（DelayConfig.custom）不存在时
直接抛 WorkflowUserError，不进入执行阶段。
"""

from unittest.mock import MagicMock

import pytest

from lvjiang.config import DelayConfig
from lvjiang.workflows.engine import WorkflowEngine, WorkflowUserError


def make_engine(custom: dict | None = None) -> WorkflowEngine:
    """创建最小化引擎实例，custom 为命名等待参数定义"""
    capture = MagicMock()
    capture.get_capture_size.return_value = (1920, 1080)
    layout = MagicMock()
    layout.get_canvas.return_value = MagicMock(
        x_ratio=0, y_ratio=0, w_ratio=1, h_ratio=1)
    return WorkflowEngine(
        capture=capture, ocr=MagicMock(), input_ctrl=MagicMock(),
        layout=layout,
        delay_config=DelayConfig(custom=custom or {}),
    )


def write_wf(tmp_path, text: str):
    wf = tmp_path / "t.wf"
    wf.write_text(text, encoding="utf-8")
    return wf


class TestNamedWaitValidation:
    def test_undefined_wait_raises_before_execution(self, tmp_path):
        """顶层 wait 引用未定义参数：加载即报错，log 不会执行"""
        wf = write_wf(tmp_path, 'log "start"\nwait no_such_wait\n')
        engine = make_engine()
        with pytest.raises(WorkflowUserError, match="no_such_wait"):
            engine.execute(wf)

    def test_defined_wait_passes(self, tmp_path):
        """已定义的命名等待正常执行"""
        wf = write_wf(tmp_path, 'wait step_interval\n')
        engine = make_engine({"step_interval": {"range": [0.0, 0.0]}})
        engine.execute(wf)  # 不抛异常

    def test_numeric_and_range_wait_not_affected(self, tmp_path):
        """数值/范围等待不参与命名校验"""
        wf = write_wf(tmp_path, 'wait 0\nwait (0, 0)\n')
        engine = make_engine()
        engine.execute(wf)  # 不抛异常

    def test_undefined_wait_in_nested_body_detected(self, tmp_path):
        """loop / def 过程体内的未定义引用同样在加载期检出"""
        wf = write_wf(tmp_path, (
            'def p()\n'
            '    wait proc_wait\n'
            'end\n'
            'loop 2\n'
            '    wait loop_wait\n'
            'end\n'
            'call p()\n'
        ))
        engine = make_engine()
        with pytest.raises(WorkflowUserError) as exc:
            engine.execute(wf)
        assert "loop_wait" in str(exc.value)
        assert "proc_wait" in str(exc.value)

    def test_wait_clause_desugared_and_validated(self, tmp_path):
        """click ... after wait <name> 语法糖展开后同样校验"""
        wf = write_wf(tmp_path, 'click (0.5, 0.5) after wait clause_wait\n')
        engine = make_engine()
        with pytest.raises(WorkflowUserError, match="clause_wait"):
            engine.execute(wf)
