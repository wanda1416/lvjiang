"""转律建议页：按需计算、卡片黄字目标、应用与清除、假设复选框。"""
from __future__ import annotations

from PyQt6.QtWidgets import QLabel, QPushButton, QScrollArea

from lvjiang.apps.yysls.core.graduation.affix_impact import AffixImpactReport
from lvjiang.apps.yysls.core.graduation.assumptions import Assumptions
from lvjiang.apps.yysls.core.graduation.transmute_optimizer import (
    TransmuteMove,
    TransmutePlanResult,
    TransmuteSlotStatus,
)
from lvjiang.apps.yysls.ui.loadout.affix_analysis_pages import AffixAnalysisPages


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


def test_opening_pages_runs_nothing_until_asked(qtbot):
    calls = {"report": 0, "search": 0}

    def report_provider():
        calls["report"] += 1
        return AffixImpactReport(0.8, 110, (), ())

    def runner(_stop, _assumptions):
        calls["search"] += 1
        return _result()

    pages = AffixAnalysisPages(
        "鸣金·虹", "基础方案", equipped=_equipped(),
        report_provider=report_provider, transmute_runner=runner,
    )
    qtbot.addWidget(pages)
    assert calls == {"report": 0, "search": 0}
    assert [title for title, _page in pages.pages()] == [
        "转律建议", "培养建议", "词条收益率"]

    pages.ensure_built(pages.yield_page)
    pages.ensure_built(pages.suggestion_page)
    assert calls["report"] == 1
    assert calls["search"] == 0


def test_transmute_actions_stay_above_dynamic_content(qtbot):
    """三个操作按钮属于顶部操作栏，不能被动态状态和卡片区推到底部。"""
    pages = AffixAnalysisPages(
        "鸣金·虹", "基础方案", equipped=_equipped(),
        transmute_runner=lambda _stop, _assumptions: _result(),
    )
    qtbot.addWidget(pages)
    layout = pages.transmute_page.layout()
    assert layout is not None
    assert layout.itemAt(0).widget() is pages._transmute_actions
    scroll = pages.transmute_page.findChild(QScrollArea)
    assert scroll is not None
    assert layout.indexOf(pages._transmute_actions) < layout.indexOf(scroll)


def test_run_renders_yellow_target_and_enables_apply(qtbot):
    applied: list[TransmutePlanResult] = []
    seen_assumptions: list[Assumptions] = []

    def runner(_stop, assumptions):
        seen_assumptions.append(assumptions)
        return _result()

    pages = AffixAnalysisPages(
        "鸣金·虹", "基础方案", equipped=_equipped(),
        transmute_runner=runner,
        apply_handler=lambda result: applied.append(result) or True,
        assumptions_provider=lambda: Assumptions(
            full_chengyin=True, simulate_transmute=True),
    )
    qtbot.addWidget(pages)
    run = pages.findChild(QPushButton, "transmuteRunButton")
    apply = pages.findChild(QPushButton, "transmuteApplyButton")
    assert run is not None and apply is not None
    assert not apply.isEnabled()

    run.click()
    qtbot.waitUntil(lambda: apply.isEnabled(), timeout=5000)

    # 假设在点击时定格；转律建议本身不使用“模拟转律”
    assert seen_assumptions == [Assumptions(full_chengyin=True)]
    assert "满承音" in pages._transmute_note.text()
    ring_card = pages._transmute_cards["ring"]
    targets = ring_card.findChildren(QLabel, "transmuteTargetLabel")
    assert [label.text() for label in targets] == ["(会意率)"]
    assert "推荐" in ring_card.hypothesis_label.text()
    pendant_card = pages._transmute_cards["pendant"]
    assert "不支持无限转律" in pendant_card.hypothesis_label.text()
    status = pages.findChild(QLabel, "transmuteStatus")
    assert status is not None
    assert "建议顺序" in status.text() and "缺 1 件" in status.text()
    final = pages.findChild(QLabel, "affixMetricValue_transmuteFinal")
    assert final is not None and final.text().startswith("81.20%")

    apply.click()
    assert len(applied) == 1
    assert "已写入" in status.text()
    assert not apply.isEnabled()


def test_untrusted_result_cannot_be_applied(qtbot):
    pages = AffixAnalysisPages(
        "鸣金·虹", "基础方案", equipped=_equipped(),
        transmute_runner=lambda _stop, _a: _result(trusted=False),
        apply_handler=lambda result: True,
    )
    qtbot.addWidget(pages)
    pages.render_transmute_result(_result(trusted=False))
    apply = pages.findChild(QPushButton, "transmuteApplyButton")
    status = pages.findChild(QLabel, "transmuteStatus")
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
    assert combat.assumptions().simulate_transmute is True
    assert "模拟转律" in combat.assumption_labels()
