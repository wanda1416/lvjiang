"""安卓应用注册表保存后的运行期热更新。"""

from types import SimpleNamespace

from lvjiang.core.config import UserConfig
from lvjiang.ui.main.menu_ops import MenuOpsMixin


def test_saved_android_apps_update_main_and_existing_engine():
    engine = SimpleNamespace(
        _android_apps={},
        _android_app_controller=object(),
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
