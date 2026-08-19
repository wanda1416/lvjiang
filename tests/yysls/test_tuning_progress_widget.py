"""自动调律进度页的实时阶段信息。"""

from lvjiang.apps.yysls.ui.tuning.progress_hub import TuningProgressHub
from lvjiang.apps.yysls.ui.tuning.progress_widget import TuningProgressWidget


def test_operation_update_is_shown_immediately(qtbot):
    hub = TuningProgressHub()
    widget = TuningProgressWidget(hub)
    qtbot.addWidget(widget)

    hub.operation_updated.emit({
        "phase": "reset",
        "message": "准备重置，剩余次数 3，正在检查冷却状态",
        "reason": "规则2命中垃圾",
    })

    qtbot.waitUntil(lambda: "准备重置" in widget._operation_label.text())
    text = widget._operation_label.text()
    assert "重置调律" in text
    assert "规则2命中垃圾" in text


def test_round_completion_moves_to_end_processing(qtbot):
    hub = TuningProgressHub()
    widget = TuningProgressWidget(hub)
    qtbot.addWidget(widget)

    hub.tune_round_completed.emit({
        "round_no": 2,
        "current_affixes": [],
        "affix_count": 2,
    })

    qtbot.waitUntil(lambda: "第 2 轮完成" in widget._operation_label.text())
    assert "结束处理" in widget._operation_label.text()


def test_finished_equipment_moves_to_previous_column(qtbot):
    hub = TuningProgressHub()
    widget = TuningProgressWidget(hub)
    qtbot.addWidget(widget)

    hub.equipment_started.emit({
        "name": "测试腕甲", "type": "腕甲", "level": 110,
        "quality": "gold", "affixes": [{"name": "劲", "value": 76}],
        "expect_rating": "top", "target_affixes": [],
    })
    hub.operation_updated.emit({
        "phase": "reset", "message": "重置已提交，正在读取重置结果",
    })
    hub.equipment_finished.emit({
        "name": "测试腕甲", "final_rating": "excellent", "rounds": 4,
        "affix_count": 5,
        "final_affixes": [{"name": "最大外功攻击", "value": 100}],
        "status": "done",
    })

    qtbot.waitUntil(lambda: widget._previous_name_label.text() == "测试腕甲")
    assert "4 轮" in widget._previous_info_label.text()
    assert "重置已提交" in widget._previous_process.toPlainText()
    assert "最大外功攻击" in widget._previous_process.toPlainText()
    assert "等待下一件装备" in widget._equip_name_label.text()


def test_new_current_does_not_overwrite_previous(qtbot):
    hub = TuningProgressHub()
    widget = TuningProgressWidget(hub)
    qtbot.addWidget(widget)
    first = {
        "name": "第一件", "type": "腕甲", "level": 110, "quality": "gold",
        "affixes": [], "expect_rating": "excellent", "target_affixes": [],
    }
    hub.equipment_started.emit(first)
    hub.equipment_finished.emit({
        "name": "第一件", "final_rating": "excellent", "rounds": 1,
        "final_affixes": [], "status": "done",
    })
    hub.equipment_started.emit({**first, "name": "第二件"})

    qtbot.waitUntil(lambda: "第二件" in widget._equip_name_label.text())
    assert widget._previous_name_label.text() == "第一件"


def test_reset_archives_before_and_starts_after_as_new_equipment(qtbot):
    hub = TuningProgressHub()
    widget = TuningProgressWidget(hub)
    qtbot.addWidget(widget)
    hub.equipment_started.emit({
        "name": "待重置腕甲", "type": "腕甲", "level": 110,
        "quality": "gold", "affixes": [
            {"name": "劲", "value": 76}, {"name": "御", "value": 50}],
        "expect_rating": "junk", "target_affixes": ["最大外功攻击"],
    })
    hub.equipment_reset.emit({
        "name": "待重置腕甲", "type": "腕甲", "level": 110,
        "quality": "gold",
        "before_affixes": [
            {"name": "劲", "value": 76}, {"name": "御", "value": 50}],
        "after_affixes": [{"name": "劲", "value": 76}],
        "before_rating": "junk", "expect_rating": "", "resets_used": 1,
    })

    qtbot.waitUntil(lambda: "重置后" in widget._equip_name_label.text())
    assert "重置前" in widget._previous_group.title()
    assert "重置前" in widget._previous_name_label.text()
    assert "御" in widget._previous_process.toPlainText()
    assert "重置前的装备状态" in widget._previous_process.toPlainText()
    assert "垃圾" in widget._previous_info_label.text()
    assert "重置后" in widget._equip_name_label.text()
    assert "判定中" in widget._expect_label.text()
    assert "劲" in widget._affix_current_label.text()
    assert "御" not in widget._affix_current_label.text()


def test_parsed_equipment_is_visible_before_assessment(qtbot):
    hub = TuningProgressHub()
    widget = TuningProgressWidget(hub)
    qtbot.addWidget(widget)

    hub.equipment_started.emit({
        "name": "即时腕甲", "type": "腕甲", "level": 110,
        "quality": "gold", "affixes": [{"name": "劲", "value": 76}],
        "expect_rating": "", "target_affixes": [],
    })
    qtbot.waitUntil(lambda: "即时腕甲" in widget._equip_name_label.text())
    assert "判定中" in widget._expect_label.text()
    assert "等待评级" in widget._rule_ratings_label.text()

    hub.equipment_assessed.emit({
        "expect_rating": "excellent", "stage": "scan",
        "rule_ratings": {
            "heal": {"name": "纯奶", "rating": "excellent"},
            "attack": {"name": "会意", "rating": "junk"},
        },
    })
    qtbot.waitUntil(lambda: "纯奶" in widget._rule_ratings_label.text())
    assert "优秀" in widget._rule_ratings_label.text()
    assert "会意" in widget._rule_ratings_label.text()
    assert "垃圾" in widget._rule_ratings_label.text()


def test_round_plan_is_visible_before_tuning_completes(qtbot):
    hub = TuningProgressHub()
    widget = TuningProgressWidget(hub)
    qtbot.addWidget(widget)

    hub.round_prepared.emit({
        "round_no": 3, "food_used": "金狗粮",
        "food_reason": "预期优秀，使用金狗粮",
        "material_stock": {"大律准石": 81, "金狗粮": 4},
        "will_tune": True,
    })
    qtbot.waitUntil(lambda: "第 3 轮" in widget._operation_label.text())
    assert "金狗粮" in widget._last_result_label.text()
    assert "大律准石×81" in widget._material_label.text()
