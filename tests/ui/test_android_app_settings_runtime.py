"""安卓应用注册表保存后的运行期热更新。"""

from types import SimpleNamespace

from lvjiang.core.config import UserConfig
from lvjiang.ui.main.menu_ops import MenuOpsMixin
from lvjiang.ui.main.run_control import RunControlMixin


def test_saved_android_apps_update_main_and_existing_engine():
    engine = SimpleNamespace(
        _android_apps={},
        _android_app_controller=object(),
        _app_controller=object(),
    )
    host = SimpleNamespace(
        _user_config=UserConfig(),
        _current_engine=engine,
    )

    MenuOpsMixin._apply_android_app_settings(host, {
        "game": {
            "package": "com.example.game",
            "activity": ".MainActivity",
            "orientation": "landscape",
        },
    })

    assert host._user_config.android_apps["game"].package == "com.example.game"
    assert engine._android_apps["game"].activity == ".MainActivity"
    assert engine._android_app_controller is None
    assert engine._app_controller is None


def test_adb_reconnect_rebuilds_both_application_controllers():
    capture = object()
    input_ctrl = SimpleNamespace(stop_check=None)
    device = object()
    stop_check = lambda: False  # noqa: E731
    engine = SimpleNamespace(
        _capture=object(),
        _input=object(),
        _android_device=object(),
        _android_app_controller=object(),
        _app_controller=object(),
        _workflow=None,
        _stop_check=stop_check,
    )
    host = SimpleNamespace(
        _current_engine=engine,
        _capture=capture,
        _input=input_ctrl,
        _device=device,
    )

    RunControlMixin._refresh_running_engine_backends(host)

    assert engine._capture is capture
    assert engine._input is input_ctrl
    assert input_ctrl.stop_check is stop_check
    assert engine._android_device is device
    assert engine._android_app_controller is None
    assert engine._app_controller is None
