"""wait stable 执行测试

验证画面稳定检测逻辑：
- 画面从变化到稳定时正常返回
- 超时未稳定时记警告并继续（不抛异常）
- 停止检查生效
"""

import numpy as np

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

    def test_timeout_continues_without_raising(self):
        """画面持续变化，超时后记警告并继续（不抛异常）"""
        frames = [_frame(i * 10) for i in range(50)]
        wf = _workflow_with_capture(_SeqCapture(frames))
        # 新语义：timeout 是预算而非断言，耗尽后正常返回
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

    def test_least_prevents_false_stable(self):
        """least 期间即使画面相同也不判定为稳定"""
        # 前 10 帧全部相同（模拟点击后画面还没开始变化）
        # 之后帧变化，再稳定
        frames = [
            *[_frame(100) for _ in range(10)],  # 相同帧（least 期间应忽略）
            _frame(200),                         # 变化
            *[_frame(200) for _ in range(20)],   # 稳定
        ]
        wf = _workflow_with_capture(_SeqCapture(frames))
        # least=0.5s，在 least 期间前 10 帧（0.05s * 10 = 0.5s）不应触发稳定判定
        # 之后帧变化再稳定，应正常返回
        wf.wait_stable(timeout=5.0, threshold=0.02, interval=0.05,
                       stable_duration=0.1, least=0.5)


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

    def test_timeout_via_engine_continues(self, tmp_path):
        """通过引擎执行 wait stable，超时仅记警告，后续语句继续执行"""
        frames = [_frame(i * 10) for i in range(100)]
        cap = _SeqCapture(frames)
        eng = make_engine()
        eng._capture = cap
        eng._workflow = None

        wf = tmp_path / "t.wf"
        wf.write_text(
            "wait stable 0.3 interval 0.05 duration 0.1\n"
            "log \"timeout 后仍继续执行\"\n",
            encoding="utf-8",
        )
        # 不抛异常，流程正常走完
        eng.execute(wf)
