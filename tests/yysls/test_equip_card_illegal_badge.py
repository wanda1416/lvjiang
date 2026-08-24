"""装备卡片的「!」状态异常徽标

被合法性判定器标记的装备（``_extra.illegal_equip`` 非空），
在卡片首行名称后显示红色「!」，点击弹出全部异常原因提示用户手工校正。
槽位卡片（_SlotCard）和背包卡片（_CompactEquipCard）行为一致。

断言用 isHidden() 而非 isVisible()：卡片没有被 show() 出来时，子控件的
isVisible() 恒为 False，区分不出「显式隐藏」与「父窗口未显示」。
"""

import pytest
from PyQt6.QtWidgets import QLabel

from lvjiang.apps.yysls.core.equip_parser.dingyin_parser import (
    DINGYIN_NOTICE_KEY,
    ZHIGE_DINGYIN_KEY,
)
from lvjiang.apps.yysls.core.equip_validator import ILLEGAL_KEY
from lvjiang.apps.yysls.ui.loadout.equip.cards import (
    _CompactEquipCard,
    _SlotCard,
)


def _equip(*, illegal: list[str] | None = None) -> dict:
    d = {
        "type": "剑", "name": "踏雪含光", "level": 110, "quality": "gold",
        "affix_1": {"name": "最大外功攻击", "value": 121.4},
        "_extra": {},
    }
    if illegal:
        d["_extra"][ILLEGAL_KEY] = illegal
    return d


@pytest.fixture(params=["slot", "compact"])
def card(request, qtbot):
    if request.param == "slot":
        w = _SlotCard("main_weapon", "主武器", "weapon")
        setter = w.set_equip
    else:
        w = _CompactEquipCard()
        setter = lambda d: w.set_equip(d, "主武器")  # noqa: E731
    qtbot.addWidget(w)
    w._setter = setter
    return w


class TestBadgeVisibility:
    def test_hidden_for_normal_equipment(self, card):
        card._setter(_equip())
        assert card.illegal_badge.isHidden()

    def test_shown_for_illegal_equipment(self, card):
        card._setter(_equip(illegal=["词条 2-5 不能重复：「劲」出现了 2 次"]))
        assert not card.illegal_badge.isHidden()
        assert card.illegal_badge.text() == "!"

    def test_hidden_again_after_reuse(self, card):
        """卡片会被复用渲染不同装备，旧标记必须清掉，不能糊在下一件上。"""
        card._setter(_equip(illegal=["异常"]))
        assert not card.illegal_badge.isHidden()
        card._setter(_equip())
        assert card.illegal_badge.isHidden()


class TestBadgeInteraction:
    def test_click_shows_all_reasons(self, qtbot, monkeypatch):
        import lvjiang.apps.yysls.ui.loadout.equip.cards as mod
        shown = []
        monkeypatch.setattr(
            mod.QMessageBox, "warning",
            lambda *a, **k: shown.append(a[2] if len(a) > 2 else ""))
        card = _SlotCard("main_weapon", "主武器", "weapon")
        qtbot.addWidget(card)
        card.set_equip(_equip(illegal=["原因甲", "原因乙"]))
        card.illegal_badge.mousePressEvent(None)
        assert len(shown) == 1
        assert "原因甲" in shown[0]
        assert "原因乙" in shown[0]

    def test_click_does_not_popup_when_clean(self, qtbot, monkeypatch):
        import lvjiang.apps.yysls.ui.loadout.equip.cards as mod
        shown = []
        monkeypatch.setattr(
            mod.QMessageBox, "warning", lambda *a, **k: shown.append(a))
        card = _SlotCard("main_weapon", "主武器", "weapon")
        qtbot.addWidget(card)
        card.set_equip(_equip())
        card.illegal_badge.mousePressEvent(None)
        assert shown == []


class TestSlotCardEmpty:
    def test_badge_cleared_on_empty(self, qtbot):
        card = _SlotCard("main_weapon", "主武器", "weapon")
        qtbot.addWidget(card)
        card.set_equip(_equip(illegal=["异常"]))
        card.set_empty()
        assert card.illegal_badge.isHidden()


class TestZhigeDingyinDisplay:
    def test_displays_text_without_illegal_badge(self, card):
        equip = _equip()
        equip["_extra"][ZHIGE_DINGYIN_KEY] = True
        card._setter(equip)

        texts = [label.text() for label in card.findChildren(QLabel)]
        assert "<止戈定音>" in texts
        assert card.illegal_badge.isHidden()

    def test_does_not_display_stored_dingyin_value(self, card):
        equip = _equip()
        equip["dingyin"] = {"name": "未知止戈效果", "value": 999}
        equip["_extra"][ZHIGE_DINGYIN_KEY] = True
        card._setter(equip)

        texts = [label.text() for label in card.findChildren(QLabel)]
        assert "<止戈定音>" in texts
        assert "999" not in texts

    def test_suspected_misread_keeps_text_and_exposes_notice(self, card):
        equip = _equip()
        equip["_extra"].update({
            ZHIGE_DINGYIN_KEY: True,
            DINGYIN_NOTICE_KEY: "可能是外功穿透，请核对",
        })
        card._setter(equip)

        labels = [
            label for label in card.findChildren(QLabel)
            if label.text() == "<止戈定音>"
        ]
        assert len(labels) == 1
        assert "外功穿透" in labels[0].toolTip()
