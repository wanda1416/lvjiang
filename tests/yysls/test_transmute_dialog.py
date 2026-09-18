"""转律建议页：按需计算、卡片黄字目标、应用与清除、假设复选框。"""
from __future__ import annotations

from PyQt6.QtWidgets import QLabel, QPushButton

from lvjiang.apps.yysls.core.graduation.affix_impact import AffixImpactReport
from lvjiang.apps.yysls.core.graduation.transmute_optimizer import (
    TransmuteMove,
    TransmutePlanResult,
    TransmuteSlotStatus,
)
from lvjiang.apps.yysls.ui.loadout.affix_impact_dialog import AffixImpactDialog


def _equipped() -> dict:
    return {
        "ring": {
            "_fp": "ring-fp", "type": "环", "name": "测试环", "level": 110,
            "quality": "gold",
            "affix_1": {"name": "最大外功攻击", "value": 100},
            "affix_2": {"name": "敏", "value": 50},
        },
        "pendant": {
            "_fp": "pendant-fp", "type": "佩", "name": "测试佩", "level": 100,
            "quality": "gold",
            "affix_1": {"name": "最大外功攻击", "value": 100},
            "affix_2": {"name": "劲", "value": 50},
        },
    }


def _result(*, trusted: bool = True) -> TransmutePlanResult:
    move = TransmuteMove(
        slot_key="ring", fp="ring-fp", affix_index=2,
        from_name="敏", from_value=50, to_name="会意率", to_value=7.0,
        marginal_gain=0.012, swap_gain=0.011,
    )
    return TransmutePlanResult(
        baseline_rate=0.80, saved_rate=None, final_rate=0.812,
        slots=(
            TransmuteSlotStatus("ring", "ring-fp", "测试环", True, "", move),
            TransmuteSlotStatus(
                "pendant", "pendant-fp", "测试佩", False, "no_retransfer"),
        ),
        evaluated=42, exhausted=True, trusted=trusted,
        equipped_fps=frozenset({"ring-fp", "pendant-fp"}),
        missing_slots=("main_weapon",),
    )


def test_opening_dialog_runs_nothing_until_asked(qtbot):
    calls = {"report": 0, "search": 0}

    def report_provider():
        calls["report"] += 1
        return AffixImpactReport(0.8, 110, (), ())

    def runner(_stop):
        calls["search"] += 1
        return _result()

    dialog = AffixImpactDialog(
        "鸣金·虹", "基础方案", equipped=_equipped(),
        report_provider=report_provider, transmute_runner=runner,
    )
    qtbot.addWidget(dialog)
    assert calls == {"report": 0, "search": 0}
    assert dialog._tabs.count() == 3
    assert [dialog._tabs.tabText(i) for i in range(3)] == [
        "转律建议", "培养建议", "词条收益率"]

    dialog._tabs.setCurrentIndex(2)
    dialog._tabs.setCurrentIndex(1)
    assert calls["report"] == 1
    assert calls["search"] == 0


def test_run_renders_yellow_target_and_enables_apply(qtbot):
    applied: list[TransmutePlanResult] = []

    dialog = AffixImpactDialog(
        "鸣金·虹", "基础方案", equipped=_equipped(),
        transmute_runner=lambda _stop: _result(),
        apply_handler=lambda result: applied.append(result) or True,
    )
    qtbot.addWidget(dialog)
    run = dialog.findChild(QPushButton, "transmuteRunButton")
    apply = dialog.findChild(QPushButton, "transmuteApplyButton")
    assert run is not None and apply is not None
    assert not apply.isEnabled()

    run.click()
    qtbot.waitUntil(lambda: apply.isEnabled(), timeout=5000)

    ring_card = dialog._transmute_cards["ring"]
    targets = ring_card.findChildren(QLabel, "transmuteTargetLabel")
    assert [label.text() for label in targets] == ["(会意率)"]
    assert "推荐" in ring_card.hypothesis_label.text()
    pendant_card = dialog._transmute_cards["pendant"]
    assert "不支持无限转律" in pendant_card.hypothesis_label.text()
    status = dialog.findChild(QLabel, "transmuteStatus")
    assert status is not None
    assert "建议顺序" in status.text() and "缺 1 件" in status.text()
    final = dialog.findChild(QLabel, "affixMetricValue_transmuteFinal")
    assert final is not None and final.text().startswith("81.20%")

    apply.click()
    assert len(applied) == 1
    assert "已写入" in status.text()
    assert not apply.isEnabled()


def test_untrusted_result_cannot_be_applied(qtbot):
    dialog = AffixImpactDialog(
        "鸣金·虹", "基础方案", equipped=_equipped(),
        transmute_runner=lambda _stop: _result(trusted=False),
        apply_handler=lambda result: True,
    )
    qtbot.addWidget(dialog)
    dialog.render_transmute_result(_result(trusted=False))
    apply = dialog.findChild(QPushButton, "transmuteApplyButton")
    status = dialog.findChild(QLabel, "transmuteStatus")
    assert apply is not None and not apply.isEnabled()
    assert status is not None and "不可信" in status.text()


def test_saved_target_shows_on_plan_cards_and_bag_cards(qtbot):
    from lvjiang.apps.yysls.ui.loadout.equip.cards import (
        _CompactEquipCard,
        _SlotCard,
    )

    equip = _equipped()["ring"]
    equip["affix_2"]["target_transmute_name"] = "会意率"
    equip["affix_2"]["target_transmute_value"] = 7.0

    slot_card = _SlotCard("ring", "环", "ring")
    qtbot.addWidget(slot_card)
    slot_card.set_equip(equip)
    assert [
        label.text()
        for label in slot_card.findChildren(QLabel, "transmuteTargetLabel")
    ] == ["(会意率)"]

    bag_card = _CompactEquipCard({})
    qtbot.addWidget(bag_card)
    bag_card.set_equip(equip, "环", "ring")
    assert [
        label.text()
        for label in bag_card.findChildren(QLabel, "transmuteTargetLabel")
    ] == ["(会意率)"]


def test_assumption_controls_include_simulate_transmute(qtbot):
    from lvjiang.apps.yysls.ui.loadout.loadout_panel import LoadoutPanel
    from tests.yysls.test_loadout_panel_layout import _Host

    panel = LoadoutPanel(_Host())
    qtbot.addWidget(panel)
    controls = [
        panel._assumption_layout.itemAt(index).widget() for index in range(4)
    ]
    assert [control.text() for control in controls] == [
        "满等级", "满承音", "满定音", "模拟转律",
    ]
    combat = panel._character._combat_attrs_tab
    combat._chk_simulate_transmute.setChecked(True)
    flags = combat.assumption_flags()
    assert flags["simulate_transmute"] is True
    assert "模拟转律" in combat.assumption_labels()
