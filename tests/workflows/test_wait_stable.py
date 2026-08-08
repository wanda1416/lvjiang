"""wait stable 执行测试

验证画面稳定检测逻辑：
- 画面从变化到稳定时正常返回
- 超时未稳定时抛出 WorkflowUserError / TimeoutError
- 停止检查生效
"""

import numpy as np
import pytest

from lvjiang.workflows.engine import WorkflowUserError
from tests.workflows.conftest import make_engine


def _frame(value: int) -> np.ndarray:
    """生成纯色 BGR 帧（value 自动截断到 0-255）"""
    return np.full((100, 100, 3), value % 256, dtype=np.uint8)


class _SeqCapture:
    """按序返回预设帧的 mock capture，序列耗尽后重复最后一帧"""

    def __init__(self, frames: list[np.ndarray | None]):
        self._frames = frames
        self._idx = 0

    def capture(self, timeout: float = 5.0) -> np.ndarray | None:
        if self._idx < len(self._frames):
            f = self._frames[self._idx]
            self._idx += 1
            return f
        return self._frames[-1] if self._frames else None


def _workflow_with_capture(cap: _SeqCapture, **kw):
    """创建 BaseWorkflow，使用自定义 capture"""
    eng = make_engine(**kw)
    eng._capture = cap
    eng._workflow = None
    return eng._ensure_workflow()


class TestWaitStableExecution:
    def test_stable_screen_returns(self):
        """画面变化后稳定 → 正常返回"""
        frames = [
            _frame(100),
            _frame(200),   # 变化
            *[_frame(200) for _ in range(20)],  # 大量相同帧
        ]
        wf = _workflow_with_capture(_SeqCapture(frames))
        # timeout=5s 总超时，stable_duration=0.1s 需连续稳定 0.1s
        wf.wait_stable(timeout=5.0, threshold=0.02, interval=0.05, stable_duration=0.1)

    def test_timeout_raises(self):
        """画面持续变化，超时抛出 TimeoutError"""
        frames = [_frame(i * 10) for i in range(50)]
        wf = _workflow_with_capture(_SeqCapture(frames))
        with pytest.raises(TimeoutError, match="未稳定"):
            wf.wait_stable(timeout=0.3, threshold=0.02, interval=0.05, stable_duration=0.1)

    def test_stop_check_exits_early(self):
        """停止标志置位时立即返回"""
        frames = [_frame(i * 20) for i in range(100)]
        wf = _workflow_with_capture(_SeqCapture(frames), stop_check=lambda: True)
        wf.wait_stable(timeout=5.0, threshold=0.02, interval=0.05)

    def test_capture_none_continues(self):
        """截图失败（返回 None）时继续循环"""
        frames = [
            None,
            _frame(100),
            *[_frame(100) for _ in range(20)],
        ]
        wf = _workflow_with_capture(_SeqCapture(frames))
        wf.wait_stable(timeout=5.0, threshold=0.02, interval=0.05, stable_duration=0.1)


class TestWaitStableDSL:
    """端到端：DSL wait stable 语句通过引擎执行"""

    def test_not_validated_as_named_wait(self, tmp_path):
        """wait stable 不参与命名等待参数校验"""
        wf = tmp_path / "t.wf"
        wf.write_text("wait stable 5 duration 0.1 interval 0.05\n", encoding="utf-8")
        eng = make_engine()
        eng._capture.capture.return_value = _frame(128)
        # 正常执行不报"未定义等待参数"
        eng.execute(wf)

    def test_timeout_via_engine(self, tmp_path):
        """通过引擎执行 wait stable，超时转 WorkflowUserError"""
        frames = [_frame(i * 10) for i in range(100)]
        cap = _SeqCapture(frames)
        eng = make_engine()
        eng._capture = cap
        eng._workflow = None

        wf = tmp_path / "t.wf"
        wf.write_text("wait stable 0.3 interval 0.05 duration 0.1\n", encoding="utf-8")
        with pytest.raises(WorkflowUserError, match="未稳定"):
            eng.execute(wf)
