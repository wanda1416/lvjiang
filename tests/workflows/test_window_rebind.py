"""引擎在客户端重启后必须把绑定整体挪到新窗口。

句柄、窗口原点和截图区域都是运行启动时的快照。重启换了窗口不重绑，后台
模式会继续 PostMessage 到已销毁的句柄——底层不检查投递返回值，点击全部
静默落空；坐标换算和截图也还停在旧位置。批量里一旦发生，剩余条目会整批
空转到各处 pause。
"""

from unittest.mock import MagicMock

from lvjiang.core.input_base import InputBackendKind
from lvjiang.workflows.builtins.system import _apps
from tests.workflows.conftest import make_engine

_NEW = {"hwnd": 333, "pid": 444, "title": "燕云十六声",
        "executable": r"C:\Game\yyslscn.exe",
        "left": 64, "top": 32, "width": 1936, "height": 1119}


def _engine(*, background: bool):
    input_ctrl = MagicMock()
    input_ctrl.background_mode = background
    input_ctrl.target_hwnd = 111
    engine = make_engine(input_ctrl=input_ctrl, window_left=10, window_top=20)
    return engine


def test_rebind_moves_input_capture_and_origin_to_the_new_window():
    engine = _engine(background=True)
    engine._last_capture_frame = object()

    engine.rebind_target_window(dict(_NEW))

    assert engine._input.target_hwnd == _NEW["hwnd"]
    assert (engine._window_left, engine._window_top) == (64, 32)
    engine._capture.set_capture_region.assert_called_once_with(
        64, 32, 1936, 1119)
    # 旧窗口的像素不能再代表当前画面
    assert engine._last_capture_frame is None


def test_rebind_keeps_foreground_backend_untouched():
    """前台注入按屏幕坐标走，不该被塞进一个它不使用的句柄。"""
    engine = _engine(background=False)

    engine.rebind_target_window(dict(_NEW))

    assert engine._input.target_hwnd == 111
    assert (engine._window_left, engine._window_top) == (64, 32)


def test_rebind_follows_through_to_the_python_workflow_facade():
    """Python 类工作流共用同一次运行的原点，不能各留一份旧值。"""
    engine = _engine(background=True)
    facade = engine._ensure_workflow()
    assert (facade._window_left, facade._window_top) == (10, 20)

    engine.rebind_target_window(dict(_NEW))

    assert (facade._window_left, facade._window_top) == (64, 32)


def test_rebind_notifies_the_host_so_the_next_run_starts_bound():
    """宿主据此跟随定位状态；不通知的话下次运行又快照回旧句柄。"""
    engine = _engine(background=True)
    seen: list[dict] = []
    engine.window_rebind_hook = seen.append

    engine.rebind_target_window(dict(_NEW))

    assert [item["hwnd"] for item in seen] == [_NEW["hwnd"]]


def test_app_controller_is_wired_to_the_engine_rebind():
    """应用控制器必须真的拿到引擎的重绑入口——这是整条链路的接缝。"""
    engine = _engine(background=True)
    engine._input.kind = InputBackendKind.POST

    controller = _apps(engine)

    assert (controller._windows.on_window_rebound
            == engine.rebind_target_window)
