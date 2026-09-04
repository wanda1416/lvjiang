"""自动调律平台路径策略的独立契约测试。"""

from types import SimpleNamespace
from unittest.mock import MagicMock, call

from lvjiang.apps.yysls.tuning_history.models import (
    RESET_COOLDOWN,
    RESET_COUNT_UNREADABLE,
    RESET_FAILED,
    RESET_MATERIAL_SHORTAGE,
)
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
CONTROL_SCENE = "general_control"


def test_route_dependencies_include_shared_page_actions():
    wf = MagicMock()
    routes = AndroidTuningRouteStrategy(wf)

    routes.load_dependencies()

    assert wf.engine.load_subcalls.call_args_list == [
        call("subcall/navigation.wf"),
        call("subcall/page_detection.wf"),
        call("subcall/page_action.wf"),
        call("subcall/equipment_scan.wf"),
    ]


def test_android_opens_recycle_dialog():
    wf = MagicMock(EQUIP_DETAIL=EQUIP_DETAIL, CONTROL_SCENE=CONTROL_SCENE)
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
    wf = MagicMock(EQUIP_DETAIL=EQUIP_DETAIL, CONTROL_SCENE=CONTROL_SCENE)
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
    wf = MagicMock(EQUIP_DETAIL=EQUIP_DETAIL, CONTROL_SCENE=CONTROL_SCENE)
    wf.output = {}
    wf.engine.call_subcall.return_value = 1
    routes = DesktopTuningRouteStrategy(wf)
    recycler = TuningRecycler(wf, routes)
    equip = SimpleNamespace(name="测试剑", type="剑", quality="gold")

    outcome = recycler.recycle_current(equip, "weapon_detail", "scan", "测试")

    assert outcome is RecycleOutcome.RECYCLED
    wf.engine.call_subcall.assert_called_once_with(
        "scan_and_confirm", ["回收装备", 1])


def test_android_recycle_delegates_confirmation_to_shared_subcall():
    wf = MagicMock(EQUIP_DETAIL=EQUIP_DETAIL, CONTROL_SCENE=CONTROL_SCENE)
    wf.output = {}
    wf.ocr_scene_by.return_value = "sub_func_2"
    wf.engine.call_subcall.return_value = 1
    routes = AndroidTuningRouteStrategy(wf)
    recycler = TuningRecycler(wf, routes)
    equip = SimpleNamespace(name="测试剑", type="剑", quality="gold")

    outcome = recycler.recycle_current(equip, "weapon_detail", "scan", "测试")

    assert outcome is RecycleOutcome.RECYCLED
    wf.engine.call_subcall.assert_called_once_with(
        "scan_and_confirm", ["回收装备", 1])


def test_android_missing_both_dialog_labels_means_locked_on_bag_page():
    wf = MagicMock(EQUIP_DETAIL=EQUIP_DETAIL, CONTROL_SCENE=CONTROL_SCENE)
    wf.output = {}
    wf.ocr_scene_by.return_value = "sub_func_2"
    wf.engine.call_subcall.side_effect = lambda name, _args=None: {
        "scan_and_confirm": 0,
        "is_in_bag_page": 1,
    }[name]
    routes = AndroidTuningRouteStrategy(wf)
    recycler = TuningRecycler(wf, routes)
    equip = SimpleNamespace(name="测试剑", type="剑", quality="gold")

    outcome = recycler.recycle_current(
        equip, "weapon_detail", "scan", "测试")

    assert outcome is RecycleOutcome.LOCKED
    assert wf.engine.call_subcall.call_args_list == [
        call("scan_and_confirm", ["回收装备", 1]),
        call("is_in_bag_page", None),
    ]
    wf.engine._ui_callback.assert_not_called()
    assert wf.click_region.call_args_list == [
        call(EQUIP_DETAIL, "more_func"),
        call(EQUIP_DETAIL, "sub_func_2"),
        call(EQUIP_DETAIL, "more_func"),
    ]


def _run_manual_recycle_choice(choice: str):
    wf = MagicMock(EQUIP_DETAIL=EQUIP_DETAIL, CONTROL_SCENE=CONTROL_SCENE)
    wf.output = {}
    wf.ocr_scene_by.return_value = "sub_func_2"
    wf.engine.call_subcall.side_effect = lambda name, _args=None: {
        "scan_and_confirm": 0,
        "is_in_bag_page": 0,
    }[name]
    wf.engine._ui_callback.return_value = choice
    routes = AndroidTuningRouteStrategy(wf)
    recycler = TuningRecycler(wf, routes)
    equip = SimpleNamespace(name="测试剑", type="剑", quality="gold")

    outcome = recycler.recycle_current(equip, "weapon_detail", "scan", "测试")
    return wf, outcome


def test_manual_recycle_choice_records_actual_recycle():
    wf, outcome = _run_manual_recycle_choice("recycled")

    assert outcome is RecycleOutcome.RECYCLED
    assert wf.output["recycled_items"][0]["name"] == "测试剑"
    action, kwargs = wf.engine._ui_callback.call_args
    assert action == ("choose",)
    assert [item["value"] for item in kwargs["choices"]] == [
        "recycled", "kept", "stopped"]
    assert kwargs["cancel_value"] == "stopped"
    # 异常状态不猜测菜单层级，也不重新寻找回收入口。
    assert wf.click_region.call_args_list == [
        call(EQUIP_DETAIL, "more_func"),
        call(EQUIP_DETAIL, "sub_func_2"),
    ]


def test_manual_keep_choice_records_locked_equipment():
    wf, outcome = _run_manual_recycle_choice("kept")

    assert outcome is RecycleOutcome.LOCKED
    assert not wf.output.get("recycled_items")


def test_manual_end_choice_does_not_guess_equipment_outcome():
    wf, outcome = _run_manual_recycle_choice("stopped")

    assert outcome is RecycleOutcome.STOPPED
    assert not wf.output.get("recycled_items")


def test_desktop_missing_both_dialog_labels_means_locked():
    wf = MagicMock(EQUIP_DETAIL=EQUIP_DETAIL, CONTROL_SCENE=CONTROL_SCENE)
    wf.output = {}
    wf.engine.call_subcall.side_effect = lambda name, _args=None: {
        "scan_and_confirm": 0,
        "is_in_bag_page": 1,
    }[name]
    routes = DesktopTuningRouteStrategy(wf)
    recycler = TuningRecycler(wf, routes)
    equip = SimpleNamespace(name="测试剑", type="剑", quality="gold")

    outcome = recycler.recycle_current(
        equip, "weapon_detail", "scan", "测试")

    assert outcome is RecycleOutcome.LOCKED
    wf.click_region.assert_not_called()
    assert wf.engine.call_subcall.call_args_list == [
        call("scan_and_confirm", ["回收装备", 1]),
        call("is_in_bag_page", None),
    ]


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
    wf = MagicMock(TUNE_SCENE="equip_tune_detail", CONTROL_SCENE=CONTROL_SCENE)
    routes = AndroidTuningRouteStrategy(wf)

    routes.open_reset_dialog()

    wf.click_region.assert_called_once_with("equip_tune_detail", "reset_tune")
    wf.press.assert_not_called()


def test_desktop_opens_reset_dialog_through_layout_action():
    wf = MagicMock(TUNE_SCENE="equip_tune_detail")
    routes = DesktopTuningRouteStrategy(wf)

    routes.open_reset_dialog()

    wf.click_region.assert_called_once_with("equip_tune_detail", "reset_tune")
    wf.press.assert_not_called()


def test_android_confirms_reset_entry_only_in_tune_scene():
    wf = MagicMock(TUNE_SCENE="equip_tune_detail", CONTROL_SCENE=CONTROL_SCENE)
    routes = AndroidTuningRouteStrategy(wf)

    routes.confirm_reset_entry()

    wf.click_region.assert_called_once_with(
        "equip_tune_detail", "reset_confirm")
    wf.press.assert_not_called()


def test_desktop_confirms_reset_entry_only_in_tune_scene():
    wf = MagicMock(TUNE_SCENE="equip_tune_detail", CONTROL_SCENE=CONTROL_SCENE)
    routes = DesktopTuningRouteStrategy(wf)

    routes.confirm_reset_entry()

    wf.click_region.assert_called_once_with(
        "equip_tune_detail", "reset_confirm")
    wf.press.assert_not_called()


def test_desktop_reset_confirms_with_layout_actions_around_secondary_delay():
    wf = MagicMock(TUNE_SCENE="equip_tune_detail", CONTROL_SCENE=CONTROL_SCENE)
    wf.engine.call_subcall.return_value = 1

    def ocr_scene(_scene, fields):
        values = {
            "reset_tune": "重置调律 3/3",
            "reset_check": "当前装备剩余可重置次数：3",
            "reset_info": "持有 4",
            "reset_confirm": "确认",
            "confirm": "确认",
            "cancel": "取消",
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
    first_confirm = calls.index(
        call.click_region("equip_tune_detail", "reset_confirm"))
    delay = calls.index(call.wait_delay("secondary_confirm"))
    assert first_confirm < delay
    wf.engine.call_subcall.assert_called_once_with(
        "scan_and_confirm", ["重置二次确认", 1])


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
    """安卓回收也经公共确认子过程，具体点击方式由布局负责。"""
    wf = MagicMock(EQUIP_DETAIL=EQUIP_DETAIL, CONTROL_SCENE=CONTROL_SCENE)
    wf.output = {}
    wf.ocr_scene_by.return_value = "sub_func_1"
    wf.engine.call_subcall.return_value = 1
    routes = AndroidTuningRouteStrategy(wf)
    recycler = TuningRecycler(wf, routes)
    equip = SimpleNamespace(name="测试剑", type="剑", quality="gold")

    outcome = recycler.recycle_current(equip, "weapon_detail", "scan", "测试")

    assert outcome is RecycleOutcome.RECYCLED
    assert call.press("SPACE", wait=None) not in wf.method_calls
    wf.engine.call_subcall.assert_called_once_with(
        "scan_and_confirm", ["回收装备", 1])


def _reset_wf(**ocr_overrides):
    """构造一个各项门槛都通过的重置宿主；用 overrides 只打穿想测的那一步。"""
    wf = MagicMock(TUNE_SCENE="equip_tune_detail", CONTROL_SCENE=CONTROL_SCENE)
    values = {
        "reset_tune": "重置调律 3/3",
        "reset_check": "当前装备剩余可重置次数：3",
        "reset_info": "持有 4",
        "reset_confirm": "确认",
        "confirm": "确认",
        "cancel": "取消",
    }
    values.update(ocr_overrides)
    wf.ocr_scene.side_effect = (
        lambda _scene, fields: {key: values[key] for key in fields})
    wf.engine.call_subcall.return_value = 1
    return wf


def test_first_confirm_ocr_failure_skips_instead_of_exhausting_resets():
    """首次确认识别失败必须走强制跳过。

    返回 False 会被 _handle_reset 当成"重置次数已用尽"，进而按
    reset_exhausted_action 处置——该配置可以是 recycle，装备会被误回收。
    """
    wf = _reset_wf(reset_confirm="")
    resetter = TuningResetter(wf, DesktopTuningRouteStrategy(wf))

    result = resetter.try_reset_tune(
        SimpleNamespace(max_resets=3), resets_used=0, why="测试规则命中",
        min_material_count=2)

    outcome, message = result
    assert outcome == RESET_FAILED and message
    # 一次确认都不能点出去。
    assert call.click_region("equip_tune_detail", "reset_confirm") \
        not in wf.method_calls
    assert call.click_region(CONTROL_SCENE, "confirm") not in wf.method_calls


def test_second_confirm_missing_pauses_for_the_user(monkeypatch):
    """二次确认弹不出来 → 暂停等人工，不替用户猜着走取消路径。

    首次确认点下去后必然弹二次确认；弹不出来通常是账号开了安全锁，机器
    点不出来。取消要先退模态再退确认重置视图，中途状态不可控，所以停下来
    交给人，人处理完再复查一次。
    """
    paused: list[str] = []
    monkeypatch.setattr(
        "lvjiang.apps.yysls.workflows.implementations.tuning.resetter.pause_user",
        lambda _engine, message: paused.append(message))

    wf = _reset_wf(confirm="", cancel="")
    wf.engine.call_subcall.side_effect = [0, 0]
    resetter = TuningResetter(wf, DesktopTuningRouteStrategy(wf))

    result = resetter.try_reset_tune(
        SimpleNamespace(max_resets=3), resets_used=0, why="测试规则命中",
        min_material_count=2)

    assert len(paused) == 1
    assert "安全锁" in paused[0]
    # 绝不点取消，也绝不把二次确认点下去。
    assert call.click_region(CONTROL_SCENE, "cancel") not in wf.method_calls
    assert call.click_region(CONTROL_SCENE, "confirm") not in wf.method_calls
    outcome, message = result
    assert outcome == RESET_FAILED and message


def test_second_confirm_recovers_after_the_user_intervenes(monkeypatch):
    """人工解锁后复查能读到确认，就继续把重置做完。"""
    wf = _reset_wf()
    wf.engine.call_subcall.side_effect = [0, 1]
    monkeypatch.setattr(
        "lvjiang.apps.yysls.workflows.implementations.tuning.resetter.pause_user",
        lambda _engine, message: None)
    resetter = TuningResetter(wf, DesktopTuningRouteStrategy(wf))

    assert resetter.try_reset_tune(
        SimpleNamespace(max_resets=3), resets_used=0, why="测试规则命中",
        min_material_count=2) is True
    assert wf.engine.call_subcall.call_args_list == [
        call("scan_and_confirm", ["重置二次确认", 1]),
        call("scan_and_confirm", ["重置二次确认", 1]),
    ]


def test_second_confirm_is_delegated_to_shared_subcall():
    """二次确认必须走公共过程，不能重新实现单字段 OCR。"""
    wf = _reset_wf()
    resetter = TuningResetter(wf, DesktopTuningRouteStrategy(wf))

    assert resetter.try_reset_tune(
        SimpleNamespace(max_resets=3), resets_used=0, why="测试规则命中",
        min_material_count=2) is True
    wf.engine.call_subcall.assert_called_once_with(
        "scan_and_confirm", ["重置二次确认", 1])


def test_reset_success_closes_the_refund_prompt_with_close_btn():
    """重置提交后用 close_btn 关掉可能存在的材料返还提示，不碰 blank_area。

    110 级会返还材料、多一层提示要关掉；105 级直接回调律页。close_btn 是一块
    安全的空关闭区（桌面布局绑定 SPACE），有提示就关掉、没提示也无副作用，
    所以不按等级分支。而 general_control.blank_area 在自动调律的弹窗上可能与
    按钮叠加，点下去会误触，调律流程里它只作识别区。
    """
    wf = _reset_wf()
    resetter = TuningResetter(wf, DesktopTuningRouteStrategy(wf))

    assert resetter.try_reset_tune(
        SimpleNamespace(max_resets=3), resets_used=0, why="测试规则命中",
        min_material_count=2) is True

    calls = wf.method_calls
    close = calls.index(call.click_region("equip_tune_detail", "close_btn"))
    secondary_delay = calls.index(call.wait_delay("secondary_confirm"))
    assert secondary_delay < close
    wf.engine.call_subcall.assert_called_once_with(
        "scan_and_confirm", ["重置二次确认", 1])
    assert call.click_region(CONTROL_SCENE, "blank_area") not in calls
    assert call.wait_stable("page_refresh") in calls[close + 1:]


def test_reset_count_threshold_still_returns_false():
    """False 的唯一合法用途——次数门槛——必须保留，否则耗尽后不再转处置。"""
    wf = _reset_wf(reset_tune="重置调律 0/3")
    resetter = TuningResetter(wf, DesktopTuningRouteStrategy(wf))

    result = resetter.try_reset_tune(
        SimpleNamespace(max_resets=3), resets_used=0, why="测试规则命中")

    assert result is False


def test_unreadable_reset_count_is_an_anomaly_not_an_exhausted_count():
    """读不出次数 → 记 count_unreadable 并跳过，绝不走次数耗尽转处置。

    转处置可以被配成 recycle。把"这一帧没看清"和"确实没次数了"混为一谈，
    等于让一次 OCR 抖动决定装备的生死。
    """
    wf = _reset_wf(reset_tune="重置调律")
    resetter = TuningResetter(wf, DesktopTuningRouteStrategy(wf))

    result = resetter.try_reset_tune(
        SimpleNamespace(max_resets=3), resets_used=0, why="测试规则命中")

    assert result is not False
    outcome, message = result
    assert outcome == RESET_COUNT_UNREADABLE
    assert message
    # 连重置弹窗都不该打开。
    assert call.click_region("equip_tune_detail", "reset_tune") \
        not in wf.method_calls


def test_explicit_zero_still_reports_exhausted():
    """明确读到 0 是无疑义的用尽，仍交给 reset_exhausted_action。"""
    wf = _reset_wf(reset_tune="重置调律(0)")
    resetter = TuningResetter(wf, DesktopTuningRouteStrategy(wf))

    assert resetter.try_reset_tune(
        SimpleNamespace(max_resets=3), resets_used=0, why="测试规则命中") is False


def test_cooldown_text_is_not_mistaken_for_availability():
    """冷却期文案不能被判成可重置，OCR 漏字之后也不能。

    游戏原文：可重置「当前装备剩余可重置次数:1」/ 冷却「6小时32分后可调律重置」。
    判据一度是「可重置」——它不是冷却文案的子串，却是它的子序列
    （可…调律…重置）。OCR 少认「调律」两字，冷却中的装备就会被判成可重置，
    然后去点一个根本不存在的确认按钮。
    """
    for text in ("6小时32分后可调律重置",      # 游戏原文
                 "6小时32分后可重置",          # OCR 漏认「调律」
                 "1小时5分后可调律重置",
                 ""):                          # 整段没识别上
        wf = _reset_wf(reset_check=text)
        resetter = TuningResetter(wf, DesktopTuningRouteStrategy(wf))

        result = resetter.try_reset_tune(
            SimpleNamespace(max_resets=3), resets_used=0, why="测试规则命中",
            min_material_count=2)

        assert result is not False, text
        outcome, _message = result
        assert outcome == RESET_COOLDOWN, text
        # 冷却期绝不能走到确认。
        assert call.click_region("equip_tune_detail", "reset_confirm") \
            not in wf.method_calls


def test_available_text_is_recognized():
    """可重置的游戏原文要能通过检查，走到确认。"""
    wf = _reset_wf(reset_check="当前装备剩余可重置次数:1")
    resetter = TuningResetter(wf, DesktopTuningRouteStrategy(wf))

    assert resetter.try_reset_tune(
        SimpleNamespace(max_resets=3), resets_used=0, why="测试规则命中",
        min_material_count=2) is True


def test_cooldown_closes_the_dialog_with_the_reset_view_back():
    """关重置弹窗要用 reset_tune 视图的 reset_back，不是基底视图的 back。

    两者都在右上角，坐标只差一点点；拿基底的 back 关弹窗是赌它们恰好重合，
    偏出去就点空，弹窗留在屏上，后面几步全对着错的页面做。
    """
    wf = _reset_wf(reset_check="6小时32分后可调律重置")
    resetter = TuningResetter(wf, DesktopTuningRouteStrategy(wf))

    outcome, message = resetter.try_reset_tune(
        SimpleNamespace(max_resets=3), resets_used=0, why="测试规则命中",
        min_material_count=2)

    assert outcome == RESET_COOLDOWN and message
    assert call.click_region("equip_tune_detail", "reset_back") \
        in wf.method_calls
    assert call.click_region("equip_tune_detail", "back") not in wf.method_calls


def test_material_shortage_closes_the_dialog_with_the_reset_view_back():
    wf = _reset_wf(reset_info="持有 1")
    resetter = TuningResetter(wf, DesktopTuningRouteStrategy(wf))

    outcome, message = resetter.try_reset_tune(
        SimpleNamespace(max_resets=3), resets_used=0, why="测试规则命中",
        min_material_count=2)

    assert outcome == RESET_MATERIAL_SHORTAGE and message
    assert call.click_region("equip_tune_detail", "reset_back") \
        in wf.method_calls
    assert call.click_region("equip_tune_detail", "back") not in wf.method_calls
