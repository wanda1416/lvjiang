"""重置成功在结构化历史中形成两次独立调律事件。"""
from __future__ import annotations

from lvjiang.apps.yysls.tuning_history.models import (
    RESET_COMPLETED,
    RESET_COOLDOWN,
    RESET_EXHAUSTED_RECYCLED,
    RESULT_RECYCLED,
    RESULT_RESET,
)
from lvjiang.apps.yysls.tuning_history.projector import TuningResultProjector


def _projector_after_successful_reset():
    projector = TuningResultProjector(
        lambda: "2026-09-01T01:00:00+00:00", split_resets=True)
    projector.consume("slot_entered", "chest", "胸甲")
    projector.consume("equipment_started", {
        "name": "流星甲", "type": "胸甲", "level": 110,
        "quality": "gold", "affixes": [{"name": "会心率"}],
    })
    projector.consume("operation_updated", {
        "phase": "material", "message": "已进入调律页",
    })
    projector.consume("round_prepared", {
        "round_no": 1, "food_used": "金狗粮",
        "food_reason": "预期优秀，添加金狗粮", "will_tune": True,
    })
    projector.consume("tune_round_completed", {
        "round_no": 1, "food_used": "金狗粮",
        "food_reason": "预期优秀，添加金狗粮",
        "new_affix_data": {"name": "精准率"},
    })
    projector.consume("operation_updated", {
        "phase": "reset", "reason": "词条不合格，执行重置",
    })
    before = projector.consume("equipment_reset", {
        "name": "流星甲", "type": "胸甲", "level": 110,
        "quality": "gold",
        "before_affixes": [{"name": "会心率"}, {"name": "精准率"}],
        "after_affixes": [{"name": "会心率"}],
        "before_rating": "junk", "resets_used": 1,
        "tuning_mode": "normal",
    })
    # 工作流在边界事件后发送的完成通知属于前一条事件。
    projector.consume("operation_updated", {
        "phase": "reset", "reset_outcome": RESET_COMPLETED,
        "reason": "词条不合格，执行重置",
    })
    return projector, before


def test_successful_reset_splits_before_and_after_cooldown_results():
    projector, before = _projector_after_successful_reset()

    assert before is not None
    assert before.equipment_id == 1
    assert before.name == "流星甲（重置前）"
    assert before.result == RESULT_RESET
    assert before.reset_outcome == RESET_COMPLETED
    assert before.rounds == 1
    assert before.round_details[0]["food_used"] == "金狗粮"
    assert before.round_details[0]["food_reason"] == "预期优秀，添加金狗粮"
    assert before.round_details[0]["completed"] is True
    assert before.telemetry_stop_reason == "reset_completed"

    projector.consume("tune_round_completed", {
        "round_no": 2, "new_affix_data": {"name": "会意率"},
    })
    projector.consume("operation_updated", {
        "phase": "reset", "reset_outcome": RESET_COOLDOWN,
        "reason": "本件已重置过一次，冷却期内不再重置",
    })
    after = projector.consume("equipment_finished", {
        "name": "流星甲", "rounds": 2, "status": "done",
        "final_affixes": [{"name": "会心率"}, {"name": "会意率"}],
        "reason": "本件已重置过一次，冷却期内不再重置",
        "resets": 1,
    })

    assert after is not None
    assert after.equipment_id == 2
    assert after.name == "流星甲（重置后）"
    assert after.initial_affixes == ({"name": "会心率"},)
    assert after.rounds == 1  # 不重复计算重置前的一轮
    assert after.result == RESULT_RESET
    assert after.reset_outcome == RESET_COOLDOWN


def test_post_reset_exhaustion_recycle_is_a_recycle_terminal_result():
    projector, _before = _projector_after_successful_reset()
    projector.consume("operation_updated", {
        "phase": "reset", "reset_outcome": RESET_EXHAUSTED_RECYCLED,
        "reason": "重置次数已用尽转回收",
    })

    after = projector.consume("equipment_finished", {
        "name": "流星甲", "rounds": 1, "status": "recycled",
        "reason": "重置次数已用尽转回收", "resets": 1,
    })

    assert after is not None
    assert after.result == RESULT_RECYCLED
    assert after.reset_outcome == RESET_EXHAUSTED_RECYCLED


def test_stopped_food_decision_is_kept_without_counting_a_round():
    projector = TuningResultProjector()
    projector.consume("slot_entered", "ring", "环")
    projector.consume("equipment_started", {
        "name": "流星环", "type": "环", "level": 110,
        "quality": "gold", "affixes": [{"name": "会心率"}],
    })
    projector.consume("round_prepared", {
        "round_no": 1, "food_used": "",
        "food_reason": "彩狗粮库存不足，按规则停止",
        "material_stock": {"彩狗粮": 0}, "will_tune": False,
    })

    item = projector.consume("equipment_finished", {
        "name": "流星环", "rounds": 0, "status": "done",
        "reason": "彩狗粮库存不足，按规则停止",
    })

    assert item is not None
    assert item.rounds == 0
    assert item.round_details == ({
        "round_no": 1, "food_used": "",
        "food_reason": "彩狗粮库存不足，按规则停止",
        "material_stock": {"彩狗粮": 0}, "will_tune": False,
        "completed": False,
    },)
