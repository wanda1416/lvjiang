"""目标资源换绑必须同步 TargetHandle，否则下一次启动跑在死后端上。

任务持有的是 `TargetHandle`，不是启动时的裸 capture/input 引用。于是只改
`ExecutionTarget.capture` 等于只改了主页面投影：handle 仍指向上一次连接时
的绑定，而那个后端已经被 `stop()` 过了。

这组回归对着最糟的那条路径写：流式截图停掉以后 `capture()` 仍然返回最后
一帧（`_latest_frame` 只在 session 重建时清），所以工作流不会报错，只会按
几分钟前的画面做识别和点击——无人值守下这是最不能接受的失败方式。
"""
from __future__ import annotations

import numpy as np
import pytest

from lvjiang.ui.main.execution_targets import (
    WINDOW_TARGET_ID,
    ExecutionTarget,
    ExecutionTargetRegistry,
    android_target_id,
)
from lvjiang.ui.main.window_ops import WindowOpsMixin


class _Backend:
    """可区分身份、能记录是否被停掉的假后端。"""

    def __init__(self, name: str) -> None:
        self.name = name
        self.stopped = False

    def start(self) -> bool:
        return True

    def stop(self) -> None:
        self.stopped = True


class _Log:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def append(self, text: str) -> None:
        self.lines.append(text)


class _Host(WindowOpsMixin):
    """只提供被测方法真正用到的协作者，其余靠 getattr 守卫自然跳过。"""

    def __init__(self, registry: ExecutionTargetRegistry) -> None:
        self._execution_targets = registry
        self.log_text = _Log()
        self._screen_recorder = None
        self._record_target_id = None
        # 预览信号在真实窗口里是 pyqtSignal；这里只要能被 emit 调用即可
        self._scrcpy_frame_ready = type(
            "Signal", (), {"emit": lambda self, *args: None})()

    def _target_has_active_run(self, target_id: str) -> bool:
        return False

    def _sync_active_target_compat(self) -> None:
        pass

    def _refresh_execution_targets_ui(self) -> None:
        pass

    def _capture_preview(self) -> None:
        pass


@pytest.fixture
def device_host(monkeypatch):
    """一台已连接的流式截图设备 + 一个可控的后端工厂。"""
    registry = ExecutionTargetRegistry()
    old_capture = _Backend("scrcpy")
    target = ExecutionTarget(
        id=android_target_id("stable-device"), kind="adb",
        display_name="手机", capture=old_capture, input_ctrl=_Backend("adb"),
        device=object(), capture_method="scrcpy", streaming=True,
    )
    registry.put(target)
    created: list[_Backend] = []

    def fake_create_capture_backend(*, device, method):
        backend = _Backend(method)
        created.append(backend)
        return backend

    monkeypatch.setattr(
        "lvjiang.core.android.create_capture_backend",
        fake_create_capture_backend)
    return _Host(registry), target, old_capture, created


def test_switching_capture_backend_rebinds_the_handle(device_host) -> None:
    """流式 → 单帧之后，下一次启动必须拿到新后端。

    修复前 handle 仍指向已 stop 的流式后端，任务会一直读到那一帧旧画面。
    """
    host, target, old_capture, _ = device_host

    host._set_android_target_streaming(target.id, False)

    assert old_capture.stopped, "旧后端应该被停掉"
    binding = target.handle.binding()
    assert binding.capture is target.capture
    assert binding.capture is not old_capture
    assert binding.generation == 2, "换绑必须递增代次"
    # 启动任务取的是这个快照，它必须解析到活着的后端
    assert target.snapshot().capture._resource() is target.capture


def test_switching_input_backend_rebinds_the_handle() -> None:
    """窗口前台/后台输入切换同样要换绑，否则下一个任务用错输入方式。"""
    registry = ExecutionTargetRegistry()
    target = ExecutionTarget(
        id=WINDOW_TARGET_ID, kind="windows", display_name="游戏窗口",
        capture=_Backend("mss"), input_ctrl=_Backend("sendinput"),
        window={"hwnd": 1, "left": 0, "top": 0, "width": 100, "height": 100},
    )
    registry.put(target)
    old_input = target.input_ctrl

    target.input_ctrl = _Backend("postmessage")
    WindowOpsMixin._rebind_target_resources(_Host(registry), target)

    binding = target.handle.binding()
    assert binding.input_ctrl is target.input_ctrl
    assert binding.input_ctrl is not old_input
    assert binding.generation == 2


def test_frame_callback_registered_on_backend_switch_carries_generation(
        device_host, monkeypatch) -> None:
    """换到流式截图时注册的帧回调也要带代次。

    连接路径一直带着代次，这条路径原来传的是 None，而 `_on_scrcpy_frame` 的
    守卫只对携带代次的回调生效——于是这条流的旧帧永远绕过校验，重连之后还能
    往预览和 `last_capture` 里写。
    """
    from lvjiang.core.android import AndroidStreamCapture

    host, target, _, _ = device_host
    callbacks: list = []
    stream = AndroidStreamCapture.__new__(AndroidStreamCapture)
    stream.start = lambda: True  # type: ignore[method-assign]
    stream.stop = lambda: None  # type: ignore[method-assign]
    stream.set_on_frame = callbacks.append  # type: ignore[method-assign]
    monkeypatch.setattr(
        "lvjiang.core.android.create_capture_backend",
        lambda *, device, method: stream)

    host._set_android_target_streaming(target.id, True)

    assert len(callbacks) == 1
    frame = np.zeros((2, 2, 3), dtype=np.uint8)
    callbacks[0](frame)
    assert target.last_capture is frame, "当前代次的帧应当被接受"

    # 模拟一次重连：代次 +1 之后，旧流的延迟帧必须被丢弃
    target.last_capture = None
    target.handle.rebind(target)
    callbacks[0](np.ones((2, 2, 3), dtype=np.uint8))
    assert target.last_capture is None, "旧代次的帧不能污染重连后的目标"
