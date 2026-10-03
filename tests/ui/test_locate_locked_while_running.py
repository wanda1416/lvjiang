"""任务运行中只许扫描，不许定位/连接。

定位会替换窗口目标并 dispose 旧目标，而旧目标的 ``capture`` 正是运行中引擎
持有的那一个——快照存的是同一个对象引用，不是副本（见
``ExecutionTarget.snapshot``）。stop 掉它等于把正在跑的任务的截图通道拆了：
前台截图之后一路取不到帧，WGC 后台截图则是在工作线程取帧的同时释放 frame
pool，那是原生崩溃的形态。

ADB 候选共用同一个按钮（``_on_locate_window`` 在候选是 adb 时转给
``_on_connect_device``），所以这道锁顺带堵住「点一台已连接设备把执行目标
切走」——那条路径原来也没有运行中判定。
"""
from unittest.mock import MagicMock

from lvjiang.ui.main.execution_targets import (
    WINDOW_TARGET_ID,
    ExecutionTarget,
    ExecutionTargetRegistry,
    android_target_id,
)
from lvjiang.ui.main.window_ops import WindowOpsMixin


class _Host:
    """只带这两条路径碰到的协作者；其余调用记录下来不执行。"""

    _set_locate_enabled = WindowOpsMixin._set_locate_enabled
    _apply_locate_lock = WindowOpsMixin._apply_locate_lock
    _on_locate_window = WindowOpsMixin._on_locate_window
    _on_connect_device = WindowOpsMixin._on_connect_device

    def __init__(self, *, running: bool, candidate: str = "windows"):
        self._running = running
        self._candidate_backend = candidate
        self._execution_targets = ExecutionTargetRegistry()
        self.btn_locate = MagicMock()
        self.window_combo = MagicMock()
        self.log_text = MagicMock()
        self.statusBar = lambda: MagicMock()
        self.disposed: list[str] = []

    def _dispose_execution_target(self, target) -> None:
        self.disposed.append(target.id)
        if target.capture is not None:
            target.capture.stop()

    def __getattr__(self, name):
        # 其余协作者（红框计时器、遮罩、UI 刷新…）都不是本用例关心的。
        # 返回 MagicMock 而不是裸函数，这样 `self._overlay.show_border(...)`
        # 这种链式调用也走得通。
        return MagicMock()


def _window_target(capture) -> ExecutionTarget:
    return ExecutionTarget(
        id=WINDOW_TARGET_ID, kind="windows", display_name="游戏窗口",
        capture=capture, input_ctrl=MagicMock(),
        window={"hwnd": 1, "left": 0, "top": 0, "width": 100, "height": 100},
    )


def test_locate_button_is_disabled_while_running() -> None:
    """候选侧说可以定位，运行锁仍要压住——两者取与。"""
    host = _Host(running=True)

    host._set_locate_enabled(True)

    host.btn_locate.setEnabled.assert_called_with(False)
    assert "运行中" in host.btn_locate.setToolTip.call_args[0][0]


def test_locate_button_comes_back_only_with_a_candidate() -> None:
    """任务结束不能无条件放开：下拉框里没有候选时定位依然无从执行。"""
    host = _Host(running=False)

    host._set_locate_enabled(False)
    host.btn_locate.setEnabled.assert_called_with(False)

    host._set_locate_enabled(True)
    host.btn_locate.setEnabled.assert_called_with(True)
    assert host.btn_locate.setToolTip.call_args[0][0] == ""


def test_auto_locate_by_window_title_is_refused_while_running() -> None:
    """按窗口标题自动定位是直接调方法、不经过按钮，必须各守一道。

    禁用按钮只能挡住鼠标；扫描流程里匹配到关键字时会直接
    ``self._on_locate_window()``，少了入口判定就还是会 dispose 掉运行中引擎
    持有的那个 capture。
    """
    engine_capture = MagicMock()
    host = _Host(running=True)
    host._execution_targets.put(_window_target(engine_capture))
    host.window_combo.currentData.return_value = {
        "hwnd": 2, "title": "游戏", "left": 0, "top": 0,
        "width": 100, "height": 100,
    }

    host._on_locate_window()

    assert host.disposed == [], "运行中不该替换窗口目标"
    assert not engine_capture.stop.called, "运行中引擎的截图通道被拆了"


def test_connecting_an_already_connected_device_cannot_steal_the_target() -> None:
    """ADB 候选共用定位按钮：运行中点「连接」不得切走执行目标。"""
    host = _Host(running=True, candidate="adb")
    host._execution_targets.put(_window_target(MagicMock()))
    phone = ExecutionTarget(
        id=android_target_id("A"), kind="adb", display_name="手机 A",
        capture=MagicMock(), input_ctrl=MagicMock())
    host._execution_targets.put(phone)
    host.window_combo.currentData.return_value = {"serial": "A"}

    host._on_locate_window()

    assert host._execution_targets.active_target_id == WINDOW_TARGET_ID


def test_idle_locate_still_replaces_the_window_target() -> None:
    """反向契约：空闲时定位照旧生效，锁不能把正常路径一起堵死。"""
    old_capture = MagicMock()
    host = _Host(running=False)
    host._execution_targets.put(_window_target(old_capture))
    host.window_combo.currentData.return_value = {
        "hwnd": 2, "title": "游戏", "left": 0, "top": 0,
        "width": 100, "height": 100,
    }
    new_target = _window_target(MagicMock())
    host._build_window_execution_target = lambda _w: new_target
    host._get_window_dpi_ratio = lambda _hwnd: 1.0

    host._on_locate_window()

    assert host.disposed == [WINDOW_TARGET_ID]
    assert old_capture.stop.called
    assert host._execution_targets.get(WINDOW_TARGET_ID) is new_target


# ─── 红框归属 ────────────────────────────────────────────


class _RedBoxHost:
    _hide_red_box_after_locate = WindowOpsMixin._hide_red_box_after_locate

    def __init__(self, *, backend: str, persistent: bool):
        # 定时器触发那一刻的执行目标类型；红框不该受它影响
        self._backend = backend
        self._target_window = None if backend == "adb" else {"left": 0}
        self.chk_red_box = MagicMock()
        self.chk_red_box.isChecked.return_value = persistent
        self._overlay = MagicMock()


def test_locate_flash_is_cleared_whatever_the_active_target_is() -> None:
    """红框属于窗口目标，不该因为执行目标是手机就永远留在桌面上。

    两条触发路径：手机已经是执行目标时去定位窗口（put 刻意不夺取选中，
    `_backend` 全程是 adb），或者定位后一秒内把执行目标切到手机（切换不碰
    这个定时器，一秒后读到的已经变了）。原来的判据读 `_backend`，两种情况
    都收不掉框。
    """
    for backend in ("windows", "adb"):
        host = _RedBoxHost(backend=backend, persistent=False)

        host._hide_red_box_after_locate()

        assert host._overlay.hide_border.called, backend


def test_persistent_marker_survives_the_one_second_flash() -> None:
    """勾了持续标定就不能被这个定时器收掉——它只负责收「闪一下」那次。"""
    host = _RedBoxHost(backend="windows", persistent=True)

    host._hide_red_box_after_locate()

    assert not host._overlay.hide_border.called
