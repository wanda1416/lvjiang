"""调律装备总览窗口布局与筛选。"""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QSizePolicy

from lvjiang.apps.yysls.ui.tuning.progress_hub import TuningProgressHub
from lvjiang.apps.yysls.ui.tuning.result_dialog import TuningResultsDialog
from lvjiang.apps.yysls.ui.tuning.result_store import TuningResultStore


def _add_result(
        hub, slot, name, *, rounds=1, status="done",
        final_rating="excellent"):
    hub.slot_entered.emit(slot, slot)
    hub.equipment_started.emit({
        "name": name, "type": "枪" if "weapon" in slot else "腕甲",
        "level": 110, "quality": "gold",
        "affixes": [{"name": "劲", "value": 76}],
    })
    hub.equipment_finished.emit({
        "name": name, "rounds": rounds, "status": status,
        "final_rating": final_rating,
        "final_affixes": [{"name": "最大外功攻击", "value": 100, "cap_pct": 92}],
    })


def test_dialog_uses_sidebar_and_four_card_columns(qtbot):
    hub = TuningProgressHub()
    store = TuningResultStore(hub)
    for index in range(5):
        _add_result(hub, "wrist", f"腕甲{index}")
    dialog = TuningResultsDialog(store)
    qtbot.addWidget(dialog)

    assert len(dialog._slot_buttons) == 7
    assert set(dialog._slot_buttons) == {
        "weapon", "ring", "pendant", "head", "chest", "leg", "wrist"}
    assert len(dialog._cards) == 5
    dialog._column_count = 4
    dialog._rebuild_cards()
    assert dialog._grid.getItemPosition(dialog._grid.indexOf(dialog._cards[0])) == (0, 0, 1, 1)
    assert dialog._grid.getItemPosition(dialog._grid.indexOf(dialog._cards[3])) == (0, 3, 1, 1)
    assert dialog._grid.getItemPosition(dialog._grid.indexOf(dialog._cards[4])) == (1, 0, 1, 1)
    assert [card.id_label.text() for card in dialog._cards] == [
        "#0001", "#0002", "#0003", "#0004", "#0005"]


def test_weapon_navigation_combines_main_and_sub_weapon(qtbot):
    hub = TuningProgressHub()
    store = TuningResultStore(hub)
    _add_result(hub, "main_weapon", "长枪")
    _add_result(hub, "sub_weapon", "短剑")
    _add_result(hub, "ring", "玉环")
    dialog = TuningResultsDialog(store)
    qtbot.addWidget(dialog)

    dialog._slot_buttons["weapon"].click()

    assert dialog.selected_slot == "weapon"
    assert [card.result.name for card in dialog._cards] == ["长枪", "短剑"]
    assert dialog._slot_buttons["weapon"].isChecked()
    assert dialog._slot_buttons["weapon"].text().endswith("2")


def test_filters_and_search_preserve_processing_order(qtbot):
    hub = TuningProgressHub()
    store = TuningResultStore(hub)
    _add_result(hub, "ring", "目标环一", rounds=1)
    _add_result(hub, "chest", "其他胸甲", rounds=1)
    _add_result(hub, "ring", "目标环二", rounds=0, status="recycled")
    _add_result(hub, "ring", "目标环三", rounds=2)
    dialog = TuningResultsDialog(store)
    qtbot.addWidget(dialog)

    dialog._slot_buttons["ring"].click()
    assert [card.result.equipment_id for card in dialog._cards] == [1, 3, 4]
    dialog._filter_buttons["tuned"].click()
    assert [card.result.equipment_id for card in dialog._cards] == [1, 4]
    dialog._search.setText("环三")
    assert [card.result.equipment_id for card in dialog._cards] == [4]

    dialog._search.clear()
    dialog._filter_buttons["all"].click()
    assert [card.result.equipment_id for card in dialog._cards] == [1, 3, 4]


def test_rating_filter_is_orthogonal_to_result_filter(qtbot):
    hub = TuningProgressHub()
    store = TuningResultStore(hub)
    _add_result(hub, "ring", "优秀已调律", final_rating="excellent")
    _add_result(
        hub, "ring", "顶级已回收", rounds=0, status="recycled",
        final_rating="top")
    _add_result(
        hub, "ring", "优秀已回收", rounds=0, status="recycled",
        final_rating="excellent")
    _add_result(
        hub, "ring", "未评级已跳过", rounds=0, status="skipped",
        final_rating="")
    dialog = TuningResultsDialog(store)
    qtbot.addWidget(dialog)

    dialog._rating_filter_buttons["excellent"].click()
    assert [card.result.name for card in dialog._cards] == [
        "优秀已调律", "优秀已回收"]

    dialog._filter_buttons["recycled"].click()
    assert [card.result.name for card in dialog._cards] == ["优秀已回收"]
    assert dialog.result_filter == "recycled"
    assert dialog.rating_filter == "excellent"

    dialog._filter_buttons["all"].click()
    dialog._rating_filter_buttons["unrated"].click()
    assert [card.result.name for card in dialog._cards] == ["未评级已跳过"]


def test_tuned_top_excludes_equipment_that_was_already_top(qtbot):
    hub = TuningProgressHub()
    store = TuningResultStore(hub)
    _add_result(
        hub, "ring", "原有顶级", rounds=0, status="already_full",
        final_rating="top")
    _add_result(
        hub, "ring", "本轮调成顶级", rounds=2, status="done",
        final_rating="top")
    dialog = TuningResultsDialog(store)
    qtbot.addWidget(dialog)

    dialog._filter_buttons["tuned"].click()
    dialog._rating_filter_buttons["top"].click()

    assert [card.result.name for card in dialog._cards] == ["本轮调成顶级"]


def test_store_reset_restores_both_filter_dimensions(qtbot):
    hub = TuningProgressHub()
    store = TuningResultStore(hub)
    _add_result(hub, "ring", "顶级环", final_rating="top")
    dialog = TuningResultsDialog(store)
    qtbot.addWidget(dialog)
    dialog._filter_buttons["tuned"].click()
    dialog._rating_filter_buttons["top"].click()

    store.clear()

    assert dialog.result_filter == "all"
    assert dialog.rating_filter == "all"
    assert dialog._filter_buttons["all"].isChecked()
    assert dialog._rating_filter_buttons["all"].isChecked()


def test_skipped_filter_and_badge(qtbot):
    hub = TuningProgressHub()
    store = TuningResultStore(hub)
    _add_result(hub, "ring", "跳过环", rounds=0, status="skipped")
    _add_result(hub, "ring", "调律环", rounds=1)
    dialog = TuningResultsDialog(store)
    qtbot.addWidget(dialog)

    dialog._filter_buttons["skipped"].click()

    assert [card.result.equipment_id for card in dialog._cards] == [1]
    assert dialog._cards[0].status_label.text() == "已跳过"
    assert dialog._stat_labels["skipped"].text() == "跳过 1"


def test_dialog_updates_live_and_card_shows_final_decision(qtbot):
    hub = TuningProgressHub()
    store = TuningResultStore(hub)
    dialog = TuningResultsDialog(store)
    qtbot.addWidget(dialog)

    _add_result(hub, "wrist", "实时腕甲", rounds=3, status="recycled")

    assert len(dialog._cards) == 1
    card = dialog._cards[0]
    assert card.result.name == "实时腕甲"
    assert card.status_label.text() == "调律后回收"
    assert card.id_label.text() == "#0001"
    assert "最大外功攻击" in card.affix_labels[0].text()
    assert "3 轮" in card.info_label.text()


def test_processing_reason_wraps_without_widening_card(qtbot):
    hub = TuningProgressHub()
    store = TuningResultStore(hub)
    _add_result(hub, "wrist", "长意见腕甲")
    dialog = TuningResultsDialog(store)
    qtbot.addWidget(dialog)
    card = dialog._cards[0]

    card.reason_label.setText("这是一条很长的处理意见" * 20)

    assert card.reason_label.wordWrap()
    assert card.reason_label.maximumWidth() == 420
    assert card.reason_label.sizePolicy().horizontalPolicy() == (
        QSizePolicy.Policy.Ignored)


def test_card_explains_reset_outcome(qtbot):
    hub = TuningProgressHub()
    store = TuningResultStore(hub)
    dialog = TuningResultsDialog(store)
    qtbot.addWidget(dialog)
    hub.slot_entered.emit("wrist", "腕甲")
    hub.equipment_started.emit({
        "name": "冷却腕甲", "type": "腕甲", "level": 110,
        "quality": "gold", "affixes": [],
    })
    hub.operation_updated.emit({
        "phase": "reset", "message": "冷却期中",
        "reason": "冷却期中", "reset_outcome": "cooldown",
    })
    hub.equipment_finished.emit({
        "name": "冷却腕甲", "rounds": 0, "status": "done",
        "reason": "冷却期中", "final_affixes": [],
    })

    assert dialog._cards[0].reset_label is not None
    assert dialog._cards[0].status_label.text() == "冷却期等待"
    assert dialog._cards[0].reset_label.text() == "重置结果：冷却期中"


def test_clicking_card_opens_detail_drawer(qtbot):
    hub = TuningProgressHub()
    store = TuningResultStore(hub)
    _add_result(hub, "wrist", "详情腕甲")
    dialog = TuningResultsDialog(store)
    qtbot.addWidget(dialog)
    dialog.show()

    qtbot.mouseClick(dialog._cards[0], Qt.MouseButton.LeftButton)

    assert not dialog._detail_panel.isHidden()
    assert dialog._detail_title.text().startswith("#0001")
    assert "详情腕甲" in dialog._detail_title.text()


def test_detail_drawer_shows_each_round_food_decision(qtbot):
    hub = TuningProgressHub()
    store = TuningResultStore(hub)
    hub.slot_entered.emit("wrist", "腕甲")
    hub.equipment_started.emit({
        "name": "狗粮腕甲", "type": "腕甲", "level": 110,
        "quality": "gold", "affixes": [{"name": "劲", "value": 76}],
    })
    hub.tune_round_completed.emit({
        "round_no": 1, "food_used": "金狗粮",
        "food_reason": "预期优秀，添加金狗粮",
        "new_affix_data": {"name": "会心率", "value": 10, "unit": "%"},
    })
    hub.equipment_finished.emit({
        "name": "狗粮腕甲", "rounds": 1, "status": "done",
        "final_affixes": [{"name": "会心率", "value": 10, "unit": "%"}],
    })
    dialog = TuningResultsDialog(store)
    qtbot.addWidget(dialog)

    dialog._show_detail(store.results[0])

    text = dialog._detail_rounds.toPlainText()
    assert "第 1 轮：金狗粮 → 会心率 10%" in text
    assert "预期优秀，添加金狗粮" in text
