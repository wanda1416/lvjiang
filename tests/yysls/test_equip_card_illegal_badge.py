"""装备卡片的「!」状态异常徽标

被合法性判定器标记的装备（``_extra.illegal_equip`` 非空），
在卡片首行名称后显示红色「!」，点击弹出全部异常原因提示用户手工校正。
槽位卡片（_SlotCard）和背包卡片（_CompactEquipCard）行为一致。

断言用 isHidden() 而非 isVisible()：卡片没有被 show() 出来时，子控件的
isVisible() 恒为 False，区分不出「显式隐藏」与「父窗口未显示」。
"""

import pytest
from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtWidgets import QLabel, QWidget

from lvjiang.apps.yysls.core.equip_parser.dingyin_parser import (
    DINGYIN_NOTICE_KEY,
    ZHIGE_DINGYIN_KEY,
)
from lvjiang.apps.yysls.core.equip_validator import ILLEGAL_KEY
from lvjiang.apps.yysls.ui.loadout.equip.cards import (
    _CompactEquipCard,
    _equipment_properties_text,
    _EquipmentPropertiesDialog,
    _show_equipment_properties,
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


def _capture_warnings(monkeypatch):
    """拦截 QMessageBox.warning，返回收集到的 (parent, 正文) 列表"""
    import lvjiang.apps.yysls.ui.loadout.equip.cards as mod
    shown: list[tuple] = []
    monkeypatch.setattr(
        mod.QMessageBox, "warning",
        lambda *a, **k: shown.append((a[0], a[2] if len(a) > 2 else "")))
    return shown


class TestBadgeInteraction:
    """点「!」弹原因。

    弹窗是排队到事件循环里弹的（见 _IllegalBadge.mousePressEvent 的注释），
    所以断言前必须让事件循环转一圈。
    """

    def test_click_shows_all_reasons(self, qtbot, monkeypatch):
        shown = _capture_warnings(monkeypatch)
        card = _SlotCard("main_weapon", "主武器", "weapon")
        qtbot.addWidget(card)
        card.set_equip(_equip(illegal=["原因甲", "原因乙"]))
        card.illegal_badge.mousePressEvent(None)
        assert shown == []          # 事件处理里绝不能同步弹出
        qtbot.waitUntil(lambda: len(shown) == 1, timeout=1000)
        assert "原因甲" in shown[0][1]
        assert "原因乙" in shown[0][1]

    def test_click_does_not_popup_when_clean(self, qtbot, monkeypatch):
        shown = _capture_warnings(monkeypatch)
        card = _SlotCard("main_weapon", "主武器", "weapon")
        qtbot.addWidget(card)
        card.set_equip(_equip())
        card.illegal_badge.mousePressEvent(None)
        qtbot.wait(50)
        assert shown == []

    def test_survives_card_deleted_before_dialog_opens(self, qtbot, monkeypatch):
        """扫描期间卡片被重建销毁：排队中的弹窗不得触碰已释放对象

        真实崩溃路径（Windows 0xc0000374）：模态框跑嵌套事件循环 →
        equipment_changed 在循环里重建网格 deleteLater 掉本卡片 →
        模态框的 parent 与调用栈上的 self 双双失效。改成排队后，
        弹窗只依赖点击瞬间取到的窗口与原因副本。
        """
        from PyQt6 import sip
        shown = _capture_warnings(monkeypatch)
        holder = QWidget()          # 充当顶层窗口，卡片销毁后它仍在
        qtbot.addWidget(holder)
        card = _SlotCard("main_weapon", "主武器", "weapon", parent=holder)
        card.set_equip(_equip(illegal=["原因甲"]))
        badge = card.illegal_badge

        card.illegal_badge.mousePressEvent(None)
        sip.delete(card)            # 模拟 _rebuild_grid 的销毁
        assert sip.isdeleted(badge)

        qtbot.waitUntil(lambda: len(shown) == 1, timeout=1000)
        assert shown[0][0] is holder   # parent 用的是存活的顶层窗口
        assert "原因甲" in shown[0][1]

    def test_dropped_parent_when_window_also_gone(self, qtbot, monkeypatch):
        """连顶层窗口都没了：降级为无父弹出，绝不把已释放对象当 parent"""
        from PyQt6 import sip

        from lvjiang.apps.yysls.ui.loadout.equip.cards import (
            _show_illegal_reasons,
        )
        shown = _capture_warnings(monkeypatch)
        dead = QWidget()
        qtbot.addWidget(dead)
        sip.delete(dead)
        _show_illegal_reasons(dead, ["原因甲"])
        assert len(shown) == 1
        assert shown[0][0] is None


class TestContextMenuNonBlocking:
    """背包卡片右键菜单必须用 popup() 而非 exec()

    exec() 会跑嵌套事件循环，扫描期间 equipment_changed 会在循环里重建网格
    deleteLater 掉本卡片，exec() 返回后对 self 的访问全是已释放对象。
    """

    def test_popup_used_and_returns_immediately(self, qtbot, monkeypatch):
        import lvjiang.apps.yysls.ui.loadout.equip.cards as mod

        def _banned_exec(*a, **k):
            raise AssertionError("右键菜单不得用 exec()：会跑嵌套事件循环")

        monkeypatch.setattr(mod.QMenu, "exec", _banned_exec)
        popped: list = []
        monkeypatch.setattr(mod.QMenu, "popup",
                            lambda self, pos: popped.append(pos))
        card = _CompactEquipCard()
        qtbot.addWidget(card)
        card.set_equip(_equip(), "主武器")
        card._show_context_menu(QPoint(1, 2))
        assert len(popped) == 1

    def test_actions_emit_captured_data(self, qtbot, monkeypatch):
        """菜单动作按点击瞬间捕获的数据发信号，不回读可能已被复用的字段"""
        import lvjiang.apps.yysls.ui.loadout.equip.cards as mod
        menus: list = []
        monkeypatch.setattr(mod.QMenu, "popup",
                            lambda self, pos: menus.append(self))
        card = _CompactEquipCard()
        qtbot.addWidget(card)
        equip = _equip()
        card.set_equip(equip, "主武器", group_key="g1")
        card._show_context_menu(QPoint(0, 0))

        got: list = []
        card.delete_requested.connect(lambda d, g: got.append((d, g)))
        # 菜单弹出后卡片被复用渲染了别的装备
        card.set_equip(_equip(illegal=["异常"]), "护腕", group_key="g2")

        actions = {a.text(): a for a in menus[0].actions()}
        actions["删除"].trigger()
        assert got == [(equip, "g1")]

    def test_every_equipment_menu_exposes_properties(self, qtbot, monkeypatch):
        import lvjiang.apps.yysls.ui.loadout.equip.cards as mod
        menus: list = []
        monkeypatch.setattr(mod.QMenu, "popup",
                            lambda self, pos: menus.append(self))
        card = _CompactEquipCard()
        qtbot.addWidget(card)
        card.set_equip(_equip(), "主武器")
        card._show_context_menu(QPoint(0, 0))

        assert "属性" in {action.text() for action in menus[0].actions()}

    def test_equipped_slot_menu_opens_the_same_properties(
        self, qtbot, monkeypatch,
    ):
        import lvjiang.apps.yysls.ui.loadout.equip.cards as mod

        monkeypatch.setattr(
            mod.QMenu, "exec",
            lambda menu, _pos: next(
                action for action in menu.actions() if action.text() == "属性"),
        )
        shown: list[dict] = []
        monkeypatch.setattr(
            mod, "_show_equipment_properties",
            lambda _parent, equip: shown.append(equip),
        )
        card = _SlotCard("main_weapon", "主武器", "weapon")
        qtbot.addWidget(card)
        equip = _equip()
        card.set_equip(equip)

        event = type("MenuEvent", (), {
            "globalPos": lambda self: QPoint(0, 0),
            "accept": lambda self: None,
        })()
        card.contextMenuEvent(event)

        assert shown == [equip]


def test_equipment_properties_show_source_fp_and_empty_missing_times():
    equip = _equip()
    equip["_fp"] = "abc123"
    equip["original_level"] = 105
    assert _equipment_properties_text(equip).splitlines() == [
        "来源：扫描",
        "指纹：abc123",
        "原始等级：105",
        "冷却时间：",
        "创建时间：",
        "更新时间：",
    ]


def test_equipment_properties_show_mock_and_formatted_times():
    equip = _equip()
    equip.update({
        "_fp": "mock_abc123",
        "original_level": 110,
        "cooldown_expires_at": "2026-09-03T05:06:07",
        "created_at": "2026-09-01T01:02:03",
        "updated_at": "2026-09-02T04:05:06",
    })
    equip["_extra"]["is_mock"] = True
    assert _equipment_properties_text(equip).splitlines() == [
        "来源：模拟",
        "指纹：mock_abc123",
        "原始等级：110",
        "冷却时间：2026-09-03 05:06:07",
        "创建时间：2026-09-01 01:02:03",
        "更新时间：2026-09-02 04:05:06",
    ]


def test_equipment_properties_use_plain_selectable_dialog(qtbot, monkeypatch):
    shown: list[_EquipmentPropertiesDialog] = []
    monkeypatch.setattr(
        _EquipmentPropertiesDialog, "exec", lambda self: shown.append(self))

    equip = _equip()
    equip["_fp"] = "abc123"
    _show_equipment_properties(None, equip)

    assert len(shown) == 1
    dialog = shown[0]
    qtbot.addWidget(dialog)
    assert dialog.windowTitle() == "装备属性"
    text = dialog.findChild(QLabel, "equipmentPropertiesText")
    assert text is not None
    assert "指纹：abc123" in text.text()
    assert "原始等级：0" in text.text()
    assert text.textInteractionFlags() & Qt.TextInteractionFlag.TextSelectableByMouse


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
