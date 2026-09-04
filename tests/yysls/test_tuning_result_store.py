"""自动调律装备总览的旁路结果聚合。"""


from lvjiang.apps.yysls.ui.tuning.progress_hub import TuningProgressHub
from lvjiang.apps.yysls.ui.tuning.result_store import (
    RESET_COMPLETED,
    RESET_COOLDOWN,
    RESET_COUNT_UNREADABLE,
    RESET_EXHAUSTED_RECYCLED,
    RESET_MATERIAL_SHORTAGE,
    RESULT_RECYCLED,
    RESULT_RESET,
    RESULT_SKIPPED,
    RESULT_TUNED,
    RESULT_TUNED_RECYCLED,
    TuningResultStore,
)
from tests.case_matrix import case_matrix


def _start(hub, name="测试装备", equip_type="腕甲"):
    hub.equipment_started.emit({
        "name": name,
        "type": equip_type,
        "level": 110,
        "quality": "gold",
        "affixes": [{"name": "劲", "value": 76}],
    })


def test_store_collects_skipped_recycled_and_tuned_in_processing_order(qtbot):
    hub = TuningProgressHub()
    store = TuningResultStore(hub)

    hub.slot_entered.emit("wrist", "腕甲")
    _start(hub, "跳过装备")
    hub.equipment_finished.emit({
        "name": "跳过装备", "rounds": 0, "status": "done",
        "final_affixes": [],
    })
    _start(hub, "回收装备")
    hub.scan_decision.emit({
        "name": "回收装备", "action": "recycled", "reason": "命中回收规则",
    })
    hub.equipment_finished.emit({
        "name": "回收装备", "rounds": 0, "status": "recycled",
        "final_affixes": [{"name": "劲", "value": 76}],
    })
    _start(hub, "调律装备")
    hub.operation_updated.emit({
        "phase": "decision", "action": "skip", "reason": "达到目标，保留",
    })
    hub.equipment_finished.emit({
        "name": "调律装备", "rounds": 2, "status": "done",
        "final_rating": "excellent",
        "final_affixes": [{"name": "会意率", "value": 4.2}],
    })

    assert [item.name for item in store.results] == [
        "跳过装备", "回收装备", "调律装备"]
    assert [item.result for item in store.results] == [
        RESULT_SKIPPED, RESULT_RECYCLED, RESULT_TUNED]
    assert [item.equipment_id for item in store.results] == [1, 2, 3]
    assert store.results[0].reason == "扫描处理后跳过"
    assert store.results[1].reason == "命中回收规则"
    assert store.results[2].reason == "达到目标，保留"


def test_tuned_recycle_and_weapon_slots_share_filter(qtbot):
    hub = TuningProgressHub()
    store = TuningResultStore(hub)

    hub.slot_entered.emit("main_weapon", "主武器")
    _start(hub, "长枪", "枪")
    hub.equipment_finished.emit({
        "name": "长枪", "rounds": 3, "status": "recycled",
        "final_affixes": [],
    })

    assert store.results[0].result == RESULT_TUNED_RECYCLED
    assert store.results_for_slot("main_weapon") == store.results
    assert store.results_for_slot("sub_weapon") == store.results
    assert store.count_for_slot("sub_weapon") == 1


def test_clear_resets_ids_for_a_new_run(qtbot):
    hub = TuningProgressHub()
    store = TuningResultStore(hub)
    hub.slot_entered.emit("ring", "环")
    _start(hub, "旧环", "环")
    hub.equipment_finished.emit({
        "name": "旧环", "rounds": 1, "status": "done", "final_affixes": [],
    })

    store.clear()
    _start(hub, "新环", "环")
    hub.equipment_finished.emit({
        "name": "新环", "rounds": 1, "status": "done", "final_affixes": [],
    })

    assert len(store.results) == 1
    assert store.results[0].equipment_id == 1
    assert store.results[0].name == "新环"


def test_reset_only_equipment_is_still_a_tuning_result(qtbot):
    hub = TuningProgressHub()
    store = TuningResultStore(hub)
    hub.slot_entered.emit("head", "冠胄")
    _start(hub, "重置冠胄", "冠胄")
    hub.equipment_reset.emit({"name": "重置冠胄", "after_affixes": []})
    hub.equipment_finished.emit({
        "name": "重置冠胄", "rounds": 0, "status": "done",
        "final_affixes": [{"name": "劲", "value": 76}],
    })

    assert len(store.results) == 1
    assert store.results[0].result == RESULT_TUNED
    assert store.results[0].reset_outcome == RESET_COMPLETED


@case_matrix(("outcome", "reason"), [
    (RESET_COOLDOWN, "冷却期中，跳过该装备"),
    (RESET_MATERIAL_SHORTAGE, "传律石不够，跳过该装备"),
])
def test_failed_reset_attempt_is_visible(qtbot, outcome, reason):
    hub = TuningProgressHub()
    store = TuningResultStore(hub)
    hub.slot_entered.emit("chest", "胸甲")
    _start(hub, "重置胸甲", "胸甲")
    hub.operation_updated.emit({
        "phase": "reset", "message": reason, "reason": reason,
        "reset_outcome": outcome,
    })
    hub.equipment_finished.emit({
        "name": "重置胸甲", "rounds": 0, "status": "done",
        "reason": reason, "final_affixes": [],
    })

    assert len(store.results) == 1
    assert store.results[0].result == RESULT_RESET
    assert store.results[0].reset_outcome == outcome
    assert store.results[0].reason == reason


def test_reset_exhausted_recycle_keeps_reset_outcome(qtbot):
    hub = TuningProgressHub()
    store = TuningResultStore(hub)
    hub.slot_entered.emit("ring", "环")
    _start(hub, "用尽环", "环")
    reason = "重置次数已用尽转回收"
    hub.operation_updated.emit({
        "phase": "reset", "message": reason, "reason": reason,
        "reset_outcome": RESET_EXHAUSTED_RECYCLED,
    })
    hub.equipment_finished.emit({
        "name": "用尽环", "rounds": 0, "status": "recycled",
        "reason": reason, "final_affixes": [],
    })

    assert store.results[0].result == RESULT_RECYCLED
    assert store.results[0].reset_outcome == RESET_EXHAUSTED_RECYCLED


def test_round_prepared_abort_reaches_live_overview(qtbot):
    """狗粮不足在准备阶段中止的轮次，实时总览必须和历史详情看到同一份数据。

    这一轮只有 round_prepared（will_tune=False），没有 tune_round_completed。
    store 不订阅 round_prepared 时，它只存在于历史投影，实时视图会缺掉。
    """
    hub = TuningProgressHub()
    store = TuningResultStore(hub)
    hub.slot_entered.emit("chest", "胸甲")
    _start(hub, "断粮胸甲", "胸甲")
    hub.tune_round_completed.emit({
        "round_no": 1, "food_used": "垃圾狗粮", "affix_count": 2,
        "new_affix_data": {"name": "会心", "value": 3.1, "unit": "%"},
        "resets": 0,
    })
    hub.round_prepared.emit({
        "round_no": 2, "food_used": "", "food_reason": "狗粮不足，停止调律",
        "will_tune": False,
    })
    hub.equipment_finished.emit({
        "name": "断粮胸甲", "rounds": 1, "status": "done", "final_affixes": [],
    })

    details = store.results[0].round_details
    assert [d["round_no"] for d in details] == [1, 2]
    assert details[0].get("completed") is True
    assert details[1].get("completed") is False
    assert details[1]["food_reason"] == "狗粮不足，停止调律"


def test_unreadable_reset_count_reaches_the_equipment_card(qtbot):
    """识别异常必须一路走到装备卡片，并且和普通重置结果视觉上分开。"""
    from lvjiang.apps.yysls.ui.tuning.result_card import (
        TuningResultCard,
        _result_label,
    )

    hub = TuningProgressHub()
    store = TuningResultStore(hub)
    hub.slot_entered.emit("leg", "胫甲")
    _start(hub, "看不清胫甲", "胫甲")
    reason = "无法识别重置次数，跳过该装备"
    hub.operation_updated.emit({
        "phase": "reset", "message": reason, "reason": reason,
        "reset_outcome": RESET_COUNT_UNREADABLE,
    })
    hub.equipment_finished.emit({
        "name": "看不清胫甲", "rounds": 0, "status": "done",
        "reason": reason, "final_affixes": [],
    })

    result = store.results[0]
    assert result.reset_outcome == RESET_COUNT_UNREADABLE
    assert result.reason == reason
    assert _result_label(result) == "无法识别重置次数"

    card = TuningResultCard(result)
    qtbot.addWidget(card)
    assert card.reset_label is not None
    # 标成「异常」而不是「重置结果」，并且标红。
    assert card.reset_label.text().startswith("异常：")
    assert card.reset_label.property("anomaly") is True
    assert "#D32F2F" in card.reset_label.styleSheet()

