"""执行目标的默认名字取「用户能对照的那个标识」。

Android 原先是「型号 · target_id 哈希前 6 位」：哈希那截是主键的碎片，对用户零
信息量（既不是序列号也不是 IP，查不到也对不上）；而重连时又把 display_name 当型号
传回去重算，于是每重连一次名字尾部就多追加一段，还会原样写进任务历史的
target_label，同一台设备在历史里出现好几个名字。

Windows 原先是窗口标题：标题在运行期会变（加载态、角色名、多开编号），而名字要能
跨一次运行保持同一个含义。
"""
from __future__ import annotations

from lvjiang.ui.main.execution_targets import (
    ExecutionTarget,
    ExecutionTargetRegistry,
    android_target_id,
    window_target_label,
)
from lvjiang.ui.main.window_ops import WindowOpsMixin


def _connected(serial: str) -> ExecutionTarget:
    """复刻 _on_connect_done 落到注册表里的那部分字段。"""
    return ExecutionTarget(
        id=android_target_id(f"identity-of-{serial}"), kind="adb",
        display_name=serial, serial=serial,
        capture=object(), input_ctrl=object(), device=object(),
    )


def test_device_name_is_the_serial_alone() -> None:
    target = _connected("192.168.1.9:5555")

    assert target.display_name == "192.168.1.9:5555"
    # 主键的哈希前缀不能出现在名字里
    assert target.id.rsplit(":", 1)[-1][:6] not in target.display_name


def test_reconnect_only_carries_the_serial() -> None:
    """重连的入参只有 serial，名字由它重算，因此重复重连是幂等的。

    原来传的是 `model=display_name`，而名字又由 model 拼出来，于是每次重连都给
    名字尾部多追加一段。
    """
    registry = ExecutionTargetRegistry()
    target = _connected("192.168.1.9:5555")
    registry.put(target)
    requested: list[dict] = []
    host = type("Host", (WindowOpsMixin,), {})()
    host._execution_targets = registry
    host._start_device_connection = (  # type: ignore[method-assign]
        lambda combo_data, **kwargs: requested.append(combo_data))

    host._reconnect_android_target(target.id)
    host._reconnect_android_target(target.id)

    assert requested == [{"serial": "192.168.1.9:5555"}] * 2
    # 把这份参数喂回名字构造，结果必须和原来逐字相同
    assert str(requested[0]["serial"]) == target.display_name


def test_two_devices_of_the_same_model_stay_distinguishable() -> None:
    """同型号两台设备由 serial 天然分开，不需要再拼型号或短哈希。"""
    usb = _connected("ABCD1234")
    wireless = _connected("192.168.1.9:5555")

    assert usb.display_name != wireless.display_name


def test_window_name_is_the_hwnd_not_the_title() -> None:
    """窗口名取 hwnd 的十六进制，与 Win32 工具里看到的一致。

    标题会变：游戏加载中、角色名、多开编号都会改它，而名字要能跨一次运行保持
    同一个含义，还会被原样写进任务历史快照。
    """
    window = {"hwnd": 0x1A2B3C, "title": "燕云十六声 - 加载中...",
              "left": 0, "top": 0, "width": 1920, "height": 1080}

    label = window_target_label(window)

    assert label == "HWND_1A2B3C"
    assert window["title"] not in label
    # 标题变了名字不跟着变
    window["title"] = "燕云十六声"
    assert window_target_label(window) == label


def test_window_name_pads_short_handles_to_a_stable_width() -> None:
    """裸句柄可能只有三四位，补齐到 6 位才看得出这是个句柄而不是别的编号。"""
    assert window_target_label({"hwnd": 0x7B}) == "HWND_00007B"
    # 超过 6 位不截断：唯一性优先于宽度
    assert window_target_label({"hwnd": 0xABCDEF12}) == "HWND_ABCDEF12"


def test_window_name_falls_back_when_there_is_no_hwnd() -> None:
    """拿不到 hwnd 时给个可读的兜底，而不是 HWND_000000。"""
    assert window_target_label(None) == "游戏窗口"
    assert window_target_label({"hwnd": 0}) == "游戏窗口"
