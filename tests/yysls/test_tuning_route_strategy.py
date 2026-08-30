"""自动调律平台路径策略的独立契约测试。"""

from types import SimpleNamespace
from unittest.mock import MagicMock, call

from lvjiang.apps.yysls.workflows.implementations.tuning.recycler import (
    RecycleOutcome,
    TuningRecycler,
)
from lvjiang.apps.yysls.workflows.implementations.tuning.resetter import (
    TuningResetter,
)
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
    routes = DesktopTuningRouteStrategy(wf)

    assert routes.open_recycle_dialog() is True
    wf.ocr_scene_by.assert_not_called()
    wf.press.assert_called_once_with("X", wait=None)
    wf.wait_stable.assert_called_once_with("page_refresh")


def test_desktop_checks_confirm_after_recycle_keypress():
    wf = MagicMock(EQUIP_DETAIL=EQUIP_DETAIL)
    wf.output = {}
    wf.ocr_scene.return_value = {"recycle_confirm": "确认"}
    routes = DesktopTuningRouteStrategy(wf)
    recycler = TuningRecycler(wf, routes)
    equip = SimpleNamespace(name="测试剑", type="剑", quality="gold")

    outcome = recycler.recycle_current(equip, "weapon_detail", "scan", "测试")

    assert outcome is RecycleOutcome.RECYCLED
    wf.ocr_scene.assert_called_once_with(
        EQUIP_DETAIL, ["recycle_confirm"])
    # 端游确认走空格，不点确认区域——那个区域在端游布局下点不动
    wf.click_region.assert_not_called()
    calls = wf.method_calls
    assert calls.index(call.press("X", wait=None)) < calls.index(
        call.ocr_scene(EQUIP_DETAIL, ["recycle_confirm"]))
    assert calls.index(call.ocr_scene(EQUIP_DETAIL, ["recycle_confirm"])) < \
        calls.index(call.press("SPACE", wait=None))


def test_desktop_waits_after_locked_recycle():
    wf = MagicMock()
    routes = DesktopTuningRouteStrategy(wf)

    routes.close_recycle_entry_on_lock()

    wf.wait_delay.assert_called_once_with("step_interval")


def test_desktop_closes_equipment_detail_with_escape():
    wf = MagicMock()
    routes = DesktopTuningRouteStrategy(wf)

    routes.close_equipment_detail()

    wf.press.assert_called_once_with("ESC", wait=None)
    wf.wait_stable.assert_called_once_with("page_refresh")


def test_android_does_not_close_equipment_detail_explicitly():
    wf = MagicMock()
    routes = AndroidTuningRouteStrategy(wf)

    routes.close_equipment_detail()

    wf.press.assert_not_called()


def test_android_opens_reset_dialog_by_clicking_existing_region():
    wf = MagicMock(TUNE_SCENE="equip_tune_detail")
    routes = AndroidTuningRouteStrategy(wf)

    routes.open_reset_dialog()

    wf.click_region.assert_called_once_with("equip_tune_detail", "reset_tune")
    wf.press.assert_not_called()


def test_desktop_opens_reset_dialog_with_r_but_keeps_region_for_scanning():
    wf = MagicMock(TUNE_SCENE="equip_tune_detail")
    routes = DesktopTuningRouteStrategy(wf)

    routes.open_reset_dialog()

    wf.press.assert_called_once_with("R", wait=None)
    wf.click_region.assert_not_called()


def test_android_confirms_reset_by_clicking_each_region():
    wf = MagicMock(TUNE_SCENE="equip_tune_detail")
    routes = AndroidTuningRouteStrategy(wf)

    routes.confirm_reset("reset_confirm")
    routes.confirm_reset("reset_confirm_2")

    assert wf.click_region.call_args_list == [
        call("equip_tune_detail", "reset_confirm"),
        call("equip_tune_detail", "reset_confirm_2"),
    ]
    wf.press.assert_not_called()


def test_desktop_confirms_both_reset_stages_with_space():
    wf = MagicMock(TUNE_SCENE="equip_tune_detail")
    routes = DesktopTuningRouteStrategy(wf)

    routes.confirm_reset("reset_confirm")
    routes.confirm_reset("reset_confirm_2")

    assert wf.press.call_args_list == [
        call("SPACE", wait=None),
        call("SPACE", wait=None),
    ]
    wf.click_region.assert_not_called()


def test_desktop_reset_confirms_with_space_around_secondary_delay():
    wf = MagicMock(TUNE_SCENE="equip_tune_detail")

    def ocr_scene(_scene, fields):
        values = {
            "reset_tune": "重置调律 3/3",
            "reset_check": "当前装备剩余可重置次数：3",
            "reset_info": "持有 4",
        }
        return {key: values[key] for key in fields}

    wf.ocr_scene.side_effect = ocr_scene
    routes = DesktopTuningRouteStrategy(wf)
    resetter = TuningResetter(wf, routes)

    result = resetter.try_reset_tune(
        SimpleNamespace(max_resets=3),
        resets_used=0,
        why="测试规则命中",
        min_material_count=2,
    )

    assert result is True
    calls = wf.method_calls
    first_space = calls.index(call.press("SPACE", wait=None))
    delay = calls.index(call.wait_delay("secondary_confirm"))
    second_space = calls.index(call.press("SPACE", wait=None), first_space + 1)
    assert first_space < delay < second_space
    assert call.click_region("equip_tune_detail", "reset_confirm") not in calls
    assert call.click_region("equip_tune_detail", "reset_confirm_2") not in calls


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


def test_android_confirms_recycle_by_clicking_region():
    """安卓侧保持点击确认区域——空格是端游特有的确认方式。"""
    wf = MagicMock(EQUIP_DETAIL=EQUIP_DETAIL)
    wf.output = {}
    wf.ocr_scene.return_value = {"recycle_confirm": "确认"}
    wf.ocr_scene_by.return_value = "sub_func_1"
    routes = AndroidTuningRouteStrategy(wf)
    recycler = TuningRecycler(wf, routes)
    equip = SimpleNamespace(name="测试剑", type="剑", quality="gold")

    outcome = recycler.recycle_current(equip, "weapon_detail", "scan", "测试")

    assert outcome is RecycleOutcome.RECYCLED
    assert call.press("SPACE", wait=None) not in wf.method_calls
    assert call.click_region(EQUIP_DETAIL, "recycle_confirm") in wf.method_calls
