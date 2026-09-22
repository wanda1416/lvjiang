"""PC 客户端重启后的窗口重绑契约。

批量调度里「结束时直接停止应用」+「准备时允许重启应用」是常用组合，
重启必然换掉窗口句柄与位置。这里锁两件事：

1. 运行期身份绑定与旧窗口是否存活无关——否则客户端已经退出时永远绑不上
   可执行文件，`start()` 会把游戏拉起来却枚举不到窗口，直到超时报错；
2. 启动成功后新窗口必须发布出去——否则宿主继续用启动时快照的句柄投递，
   PostMessage 不检查返回值，点击全部静默落空。
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


def _controller(app, visible, *, alive, on_window_rebound=None):
    controller = ac.WindowsAppController(
        {"yyslscn": app}, on_window_rebound=on_window_rebound)
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


def test_restart_publishes_the_new_window(located, monkeypatch):
    """启动成功后把新窗口发布给宿主，并更新全局定位记录。"""
    import ctypes

    class _Windll:
        class user32:
            @staticmethod
            def SetWindowPos(*_args):
                return True
    monkeypatch.setattr(ctypes, "windll", _Windll, raising=False)

    rebound: list[dict] = []
    # 客户端此刻不在：窗口要等 Popen 之后才出现
    visible: list[dict] = []
    controller = _controller(located, visible, alive=[False],
                             on_window_rebound=rebound.append)
    controller._windows = staticmethod(lambda: list(visible))
    controller._placements["yyslscn"] = {
        "left": 10, "top": 20, "width": 1936, "height": 1119}
    monkeypatch.setattr(ac.subprocess, "Popen",
                        lambda *a, **k: visible.append(dict(_NEW)))

    assert controller.start("yyslscn", timeout=5) is True

    assert [item["hwnd"] for item in rebound] == [_NEW["hwnd"]]
    assert ac.get_connected_app_info("pc")["hwnd"] == _NEW["hwnd"]


def test_already_running_client_is_not_republished(located):
    """没有真的重启就不发布——宿主的绑定本来就还有效。"""
    rebound: list[dict] = []
    controller = _controller(located, [dict(_OLD)], alive=[True],
                             on_window_rebound=rebound.append)

    assert controller.start("yyslscn", timeout=5) is True
    assert rebound == []
