"""自动调律装备总览的旁路结果聚合。"""


from lvjiang.apps.yysls.ui.tuning.progress_hub import TuningProgressHub
from lvjiang.apps.yysls.ui.tuning.progress_widget import TuningProgressWidget
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
    assert store.results[2].final_rating == ""


def test_only_full_equipment_keeps_final_rating_in_history(qtbot):
    """调用方误传预期评级时，历史边界仍拒绝给未满装备定级。"""
    hub = TuningProgressHub()
    store = TuningResultStore(hub)
    hub.slot_entered.emit("wrist", "腕甲")

    _start(hub, "未满装备")
    hub.equipment_finished.emit({
        "name": "未满装备", "rounds": 2, "status": "done",
        "final_rating": "excellent", "telemetry_final_rating": "excellent",
        "final_affixes": [{"name": f"词条{i}"} for i in range(1, 5)],
    })
    _start(hub, "满词条装备")
    hub.equipment_finished.emit({
        "name": "满词条装备", "rounds": 3, "status": "done",
        "final_rating": "excellent", "telemetry_final_rating": "excellent",
        "final_affixes": [{"name": f"词条{i}"} for i in range(1, 6)],
    })

    assert store.results[0].final_rating == ""
    assert store.results[0].telemetry_final_rating == ""
    assert store.results[1].final_rating == "excellent"
    assert store.results[1].telemetry_final_rating == "excellent"


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


def test_terminal_smart_tuning_opinion_is_used_as_history_reason(qtbot):
    hub = TuningProgressHub()
    store = TuningResultStore(hub)
    hub.slot_entered.emit("ring", "环")
    _start(hub, "智能跳过环", "环")
    opinion = (
        "智能调律：分析最大极限毕业率 94.50%，"
        "低于备战方案，处理结果：跳过。")
    hub.smart_tuning_updated.emit({
        "enabled": True, "state": "final", "final_action": "skip",
        "opinion": opinion,
    })
    hub.equipment_finished.emit({
        "name": "智能跳过环", "rounds": 0, "status": "done",
        "reason": "普通结束原因", "final_affixes": [],
    })

    assert store.results[0].reason == opinion


def test_smart_continue_is_not_a_terminal_history_opinion(qtbot):
    hub = TuningProgressHub()
    store = TuningResultStore(hub)
    hub.slot_entered.emit("ring", "环")
    _start(hub, "继续调律环", "环")
    hub.smart_tuning_updated.emit({
        "enabled": True, "state": "evaluated", "final_action": "continue",
        "opinion": "不应写入",
    })
    hub.equipment_finished.emit({
        "name": "继续调律环", "rounds": 0, "status": "done",
        "reason": "正常结束", "final_affixes": [],
    })

    assert store.results[0].reason == "正常结束"


def test_smart_reset_opinion_does_not_outlive_the_reset(qtbot):
    """智能调律促成重置后装备继续调律，终态原因应是后续的真实结论。"""
    hub = TuningProgressHub()
    store = TuningResultStore(hub)
    hub.slot_entered.emit("ring", "环")
    _start(hub, "重置后继续环", "环")
    hub.smart_tuning_updated.emit({
        "enabled": True, "state": "final", "final_action": "reset",
        "opinion": "智能调律：无法提升，处理结果：重置装备。",
    })
    hub.equipment_reset.emit({
        "name": "重置后继续环", "before_affixes": [], "after_affixes": [],
        "resets_used": 1,
    })
    hub.equipment_finished.emit({
        "name": "重置后继续环", "rounds": 3, "status": "done",
        "reason": "词条已满，无行为规则命中 → 结束并保留装备",
        "final_affixes": [],
    })

    assert store.results[0].reason == "词条已满，无行为规则命中 → 结束并保留装备"


def test_progress_widget_shows_ordered_smart_plan_details(qtbot):
    hub = TuningProgressHub()
    widget = TuningProgressWidget(hub)
    qtbot.addWidget(widget)
    widget.reset_state()
    hub.smart_tuning_updated.emit({
        "enabled": True,
        "state": "evaluated",
        "message": "所有方案确定无法提升",
        "plans": [
            {
                "plan_name": "无名PVE", "rule_name": "会意", "playstyle": "纯唐",
                "status": "no_improvement", "plan_maximum_rate": 0.99,
                "baseline_rate": 0.95, "without_slot_rate": 0.81,
                "maximum_rate": 0.945, "reason": "穷尽后无提升",
            },
            {
                "plan_name": "火拳奶", "rule_name": "奶", "playstyle": "纯奶",
                "status": "incomplete",
                "reason": "备战方案不完整（7/8），智能调律不启用",
            },
        ],
    })

    assert widget._smart_group.isVisibleTo(widget)
    text = widget._smart_plans_label.text()
    assert text.index("无名PVE") < text.index("火拳奶")
    assert "方案极限：99.00%" in text
    assert "七件极限：81.00%" in text
    assert "候选极限：94.50%" in text
    assert "备战方案不完整" in text


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
