"""自动调律平台路径策略的独立契约测试。"""

from unittest.mock import MagicMock, call

from lvjiang.apps.yysls.workflows.implementations.tuning.route_strategy import (
    AndroidTuningRouteStrategy,
    DesktopTuningRouteStrategy,
)

EQUIP_DETAIL = "equip_detail"


def test_android_opens_recycle_dialog():
    wf = MagicMock(EQUIP_DETAIL=EQUIP_DETAIL)
    wf.ocr_scene_by.return_value = "sub_func_2"
    routes = AndroidTuningRouteStrategy(wf)

    assert routes.open_recycle_dialog() is True
    wf.click_region.assert_any_call(EQUIP_DETAIL, "more_func")
    wf.ocr_scene_by.assert_called_once_with(
        EQUIP_DETAIL,
        ["sub_func_1", "sub_func_2", "sub_func_3", "sub_func_4"],
        "回收", "contains")
    wf.click_region.assert_any_call(EQUIP_DETAIL, "sub_func_2")
    assert wf.wait_stable.call_count == 2


def test_android_closes_menu_when_recycle_entry_missing():
    wf = MagicMock(EQUIP_DETAIL=EQUIP_DETAIL)
    wf.ocr_scene_by.return_value = ""
    routes = AndroidTuningRouteStrategy(wf)

    assert routes.open_recycle_dialog() is False
    assert wf.click_region.call_args_list == [
        call(EQUIP_DETAIL, "more_func"),
        call(EQUIP_DETAIL, "more_func"),
    ]
    wf.wait_delay.assert_called_once_with("step_interval")


def test_android_restores_menu_after_locked_recycle():
    wf = MagicMock(EQUIP_DETAIL=EQUIP_DETAIL)
    routes = AndroidTuningRouteStrategy(wf)

    routes.close_recycle_entry_on_lock()

    wf.click_region.assert_called_once_with(EQUIP_DETAIL, "more_func")
    wf.wait_delay.assert_called_once_with("step_interval")


def test_desktop_opens_recycle_dialog_with_keypress():
    wf = MagicMock(EQUIP_DETAIL=EQUIP_DETAIL)
    wf.ocr_scene_by.return_value = "func_area"
    routes = DesktopTuningRouteStrategy(wf)

    assert routes.open_recycle_dialog() is True
    wf.ocr_scene_by.assert_called_once_with(
        EQUIP_DETAIL, ["func_area"], "回收", "contains")
    wf.press.assert_called_once_with("X", wait=None)
    wf.wait_stable.assert_called_once_with("page_refresh")


def test_desktop_keeps_page_when_recycle_entry_missing():
    wf = MagicMock(EQUIP_DETAIL=EQUIP_DETAIL)
    wf.ocr_scene_by.return_value = ""
    routes = DesktopTuningRouteStrategy(wf)

    assert routes.open_recycle_dialog() is False
    wf.press.assert_not_called()
    wf.wait_stable.assert_not_called()


def test_desktop_waits_after_locked_recycle():
    wf = MagicMock()
    routes = DesktopTuningRouteStrategy(wf)

    routes.close_recycle_entry_on_lock()

    wf.wait_delay.assert_called_once_with("step_interval")


def test_grid_click_ratios():
    wf = MagicMock()
    android = AndroidTuningRouteStrategy(wf)
    desktop = DesktopTuningRouteStrategy(wf)

    assert android.grid_click_x_ratio(1) == 0.5
    assert desktop.grid_click_x_ratio(1) == 0.75
    assert desktop.grid_click_x_ratio(2) == 0.5


def test_navigation_subcall_failure_is_propagated():
    wf = MagicMock()
    wf.engine.call_subcall.return_value = -1
    routes = DesktopTuningRouteStrategy(wf)

    assert routes.enter_equip() is False
    assert routes.return_main() is False
    assert routes.enter_tune_detail() is False


def test_navigation_subcall_success_is_propagated():
    wf = MagicMock()
    wf.engine.call_subcall.return_value = 0
    routes = DesktopTuningRouteStrategy(wf)

    assert routes.enter_equip() is True
    assert routes.return_main() is True
    assert routes.enter_tune_detail() is True
