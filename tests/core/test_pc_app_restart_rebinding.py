"""PC 客户端重启后的窗口绑定契约。

批量调度里「结束时直接停止应用」+「准备时允许重启应用」是常用组合，重启
必然换掉窗口句柄与位置。这里锁住：运行期身份绑定与旧窗口是否存活无关——
否则客户端已经退出时永远绑不上可执行文件，`start()` 会把游戏拉起来却枚举
不到窗口，直到超时报错。
"""

import pytest

from lvjiang.core import app_controller as ac
from lvjiang.core.config import AndroidAppConfig

_OLD = {"executable": r"C:\Game\yyslscn.exe", "title": "燕云十六声",
        "hwnd": 111, "pid": 222,
        "left": 10, "top": 20, "width": 1936, "height": 1119}
_NEW = {"executable": r"C:\Game\yyslscn.exe", "title": "燕云十六声",
        "hwnd": 333, "pid": 444,
        "left": 10, "top": 20, "width": 1936, "height": 1119}


@pytest.fixture
def located(monkeypatch):
    """复现「用窗口模式定位过，但注册项本身没有配可执行文件」的真实配置。"""
    monkeypatch.setattr(ac, "_observed_window", dict(_OLD), raising=False)
    monkeypatch.setattr(ac.sys, "platform", "win32")
    # record_connected_window 写的是模块级全局，别把它漏给其他用例。
    monkeypatch.setattr(ac, "_connected_apps", {}, raising=False)
    monkeypatch.setattr(ac, "_active_connection_platform", "", raising=False)
    return AndroidAppConfig(package="com.netease.yyslscn", platform="both")


def _controller(app, visible, *, alive):
    controller = ac.WindowsAppController({"yyslscn": app})
    controller._windows = staticmethod(lambda: list(visible))
    controller._window_is_current = lambda window: (
        alive[0] and window["hwnd"] == _OLD["hwnd"])
    return controller


def test_dead_client_still_binds_the_executable_for_the_restart(located):
    """客户端已经退出时也要完成身份绑定，重启才枚举得到新窗口。"""
    controller = _controller(located, [], alive=[False])

    assert controller.is_running("yyslscn") is False
    assert located.executable == _OLD["executable"]

    # 重启后新窗口只能靠可执行文件认出来（hwnd/pid 全变了）
    controller._windows = staticmethod(lambda: [dict(_NEW)])
    assert controller._find(located)["hwnd"] == _NEW["hwnd"]


def test_live_client_keeps_reporting_the_observed_window(located):
    """旧窗口还活着时仍然直接复用定位结果，不去枚举。"""
    controller = _controller(located, [], alive=[True])

    assert controller.is_running("yyslscn") is True
    assert controller._find(located)["hwnd"] == _OLD["hwnd"]
