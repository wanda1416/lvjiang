"""候选评级的玩法多选器。

只能选一条规则时，能留下的装备极少——一件装备往往只对得上其中一两套
练法。这里验的是「三层相关性排序、多选、全选/全不选覆盖整张表」。
"""
from __future__ import annotations

import pytest
from PyQt6.QtCore import Qt

from lvjiang.apps.yysls.ui.loadout.optimal_combo import (
    _MIN_RATING_CHOICES,
    _ClickableLineEdit,
    _playstyle_match_scope,
    _PlaystylePickerDialog,
)

pytestmark = pytest.mark.usefixtures("qapp")

_OPTIONS = [
    ("heal_pure", "纯奶", "治疗纯奶-纯奶", "plan"),
    ("heal_fire", "火拳", "治疗火拳-火拳", "school"),
    ("huixin_yuyu", "飞天玉", "玉玉大王-飞天玉", "attr"),
    ("jingzhun", "远程", "精准-远程", ""),
]


def test_related_playstyles_are_marked_in_three_levels(qtbot=None) -> None:
    dialog = _PlaystylePickerDialog(_OPTIONS, set())

    labels = [dialog._list.item(i).text() for i in range(dialog._list.count())]
    assert "（本方案）" in labels[0]
    assert "（本流派）" in labels[1]
    assert "（本属性）" in labels[2]
    assert "（本" not in labels[3]


def test_preselected_pairs_come_back_checked() -> None:
    dialog = _PlaystylePickerDialog(
        _OPTIONS, {("heal_fire", "火拳")})

    assert dialog.values() == [("heal_fire", "火拳")]


def test_select_all_and_none_cover_every_row() -> None:
    dialog = _PlaystylePickerDialog(_OPTIONS, set())

    dialog._set_all(True)
    assert len(dialog.values()) == 4
    dialog._set_all(False)
    assert dialog.values() == []


def test_playstyle_scope_uses_registered_school_and_attr_not_weapon_guess() -> None:
    current = {
        "current_playstyle": "纯奶",
        "current_school": "牵丝·霖",
        "current_attr": "牵丝",
    }
    assert _playstyle_match_scope(
        "纯奶", {"school": "牵丝·霖", "attr": "牵丝"}, **current,
    ) == "plan"
    assert _playstyle_match_scope(
        "火拳", {"school": "牵丝·霖", "attr": "牵丝"}, **current,
    ) == "school"
    assert _playstyle_match_scope(
        "飞天玉", {"school": "牵丝·玉", "attr": "牵丝"}, **current,
    ) == "attr"
    assert _playstyle_match_scope(
        "翊翊", {"school": "牵丝·翊", "attr": "牵丝"}, **current,
    ) == "attr"
    assert _playstyle_match_scope(
        "纯唐", {"school": "裂石·钧", "attr": "裂石"}, **current,
    ) == ""


def test_junk_is_never_offered_as_a_requirement() -> None:
    """要求「至少是垃圾」等于没有要求。"""
    assert "垃圾" not in _MIN_RATING_CHOICES
    assert _MIN_RATING_CHOICES == ("顶级", "优秀", "一般")


def test_the_display_box_is_read_only_and_emits_on_click() -> None:
    """多选结果是一串「规则-玩法」，下拉框装不下也表达不了。"""
    from PyQt6.QtCore import QPoint
    from PyQt6.QtGui import QMouseEvent

    edit = _ClickableLineEdit()
    fired: list[bool] = []
    edit.clicked.connect(lambda: fired.append(True))

    assert edit.isReadOnly()
    edit.mousePressEvent(QMouseEvent(
        QMouseEvent.Type.MouseButtonPress, QPoint(1, 1).toPointF(),
        Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier))
    assert fired == [True]


# ── 候选行的装备详情 ──────────────────────────────────────

def test_equipment_details_open_on_click_not_on_hover() -> None:
    """这一页正是逐件比对词条的地方，等系统那 ~700ms 悬停延迟太慢。"""
    from PyQt6.QtWidgets import QLabel

    from lvjiang.apps.yysls.ui.loadout.optimal_combo import _CandidateRow

    row = _CandidateRow(
        {"name": "测试环", "type": "环", "quality": "gold", "level": 110,
         "affix_1": {"name": "最小外功攻击", "value": 100}},
        "一般",
    )

    # 名称上不再挂详情 tooltip，只留一句操作提示
    assert "最小外功攻击" not in row.label.toolTip()
    assert row._popup is None

    row.label.clicked.emit()

    assert row._popup is not None
    detail = " ".join(w.text() for w in row._popup.findChildren(QLabel))
    assert "最小外功攻击" in detail


# ── 组合详情页 ────────────────────────────────────────────

def _detail_dialog(current_equipped: dict | None = None):
    """只搭组合详情页需要的那几样，不走完整构造（那要读 session）。"""
    from PyQt6.QtWidgets import QCheckBox, QLabel, QTabWidget

    from lvjiang.apps.yysls.ui.loadout.optimal_combo import (
        _SLOT_ORDER,
        OptimalComboPage,
        _SlotDetailPanel,
    )

    dlg = OptimalComboPage.__new__(OptimalComboPage)
    dlg._detail_panels = {
        key: _SlotDetailPanel(key, name, ft)
        for key, name, ft in _SLOT_ORDER if key in ("main_weapon", "head", "ring")
    }
    dlg._detail_cards = {k: p.card for k, p in dlg._detail_panels.items()}
    dlg._slot_labels = {key: name for key, name, _ft in _SLOT_ORDER}
    dlg._current_equipped = dict(current_equipped or {})
    dlg._detail_hint = QLabel()
    # 假设视图开关：不勾选即默认的「显示原始装备」，正是这两条用例要验的
    dlg._detail_hypothesis_toggle = QCheckBox()
    dlg._tab_widget = QTabWidget()
    for _ in range(3):
        dlg._tab_widget.addTab(QLabel(), "t")
    return dlg


def test_combo_detail_tab_shows_the_current_equipment() -> None:
    """满承音/满等级只是算分假设，把假设值摆成装备详情会让人以为装备
    真是那样；假设文字放在卡片上方的状态带里，不再挤进卡片。"""
    from lvjiang.apps.yysls.ui.loadout.optimal_combo import OptimalComboPage

    dlg = _detail_dialog()
    equip = {"name": "测试剑", "type": "剑", "level": 110, "quality": "gold",
             "dingyin": {"name": "无相穿透", "value": 1.0}}
    OptimalComboPage._on_show_detail(dlg, {
        "equipped": {"main_weapon": equip},
        "assumptions": {"main_weapon": ["同等级承音假设"]},
        "gongjue": "会意",
    })

    card = dlg._detail_cards["main_weapon"]
    assert "测试剑" in card.lbl_name.text()
    assert card._equip_data is equip
    assert not card.hypothesis_label.isVisibleTo(card)
    assert dlg._detail_panels["main_weapon"].assumptions == ["同等级承音假设"]
    assert "会意" in dlg._detail_hint.text()
    # 组合里没有的槽位显示空卡，而不是留着上一次的内容
    assert "冠胄" == dlg._detail_cards["head"].lbl_name.text()
    assert dlg._detail_panels["head"].change == "none"
    assert dlg._tab_widget.currentIndex() == 2


def test_combo_detail_marks_slots_that_differ_from_the_loadout() -> None:
    """不熟悉自己穿戴的人只看八张卡片不知道该换哪几件：
    要换的部位给强调色边框和「需更换 / 当前：xxx」，一致的部位给弱化的「已穿戴」。"""
    from lvjiang.apps.yysls.ui.loadout.optimal_combo import OptimalComboPage

    worn_sword = {"name": "旧剑", "type": "剑", "level": 100, "quality": "gold",
                  "_fp": "old-sword"}
    worn_head = {"name": "旧冠", "type": "冠胄", "level": 110, "quality": "gold",
                 "_fp": "old-head"}
    new_sword = {"name": "新剑", "type": "剑", "level": 110, "quality": "gold",
                 "_fp": "new-sword"}
    same_head = dict(worn_head)  # 同一条仓储记录的另一份 dict：按 fp 判等
    new_ring = {"name": "新环", "type": "环", "level": 110, "quality": "gold",
                "_fp": "new-ring"}

    dlg = _detail_dialog({"main_weapon": worn_sword, "head": worn_head})
    OptimalComboPage._on_show_detail(dlg, {
        "equipped": {"main_weapon": new_sword, "head": same_head, "ring": new_ring},
        "assumptions": {},
        "gongjue": "会意",
    })

    sword = dlg._detail_panels["main_weapon"]
    assert sword.change == "swap"
    assert sword.card._attention
    assert "旧剑" in sword.current_label.text()
    head = dlg._detail_panels["head"]
    assert head.change == "same"
    assert not head.card._attention
    assert not head.current_label.isVisibleTo(head)
    ring = dlg._detail_panels["ring"]
    assert ring.change == "new"
    assert ring.card._attention
    hint = dlg._detail_hint.text()
    assert "需更换 2 件" in hint and "主武器" in hint and "环" in hint
    assert "冠胄" not in hint.split("需更换")[1].split("·")[0]

    # 再看一套与备战方案一致的组合：边框与提示都要回收
    OptimalComboPage._on_show_detail(dlg, {
        "equipped": {"main_weapon": dict(worn_sword), "head": dict(worn_head)},
        "assumptions": {},
        "gongjue": "会意",
    })
    assert dlg._detail_panels["main_weapon"].change == "same"
    assert not dlg._detail_panels["main_weapon"].card._attention
    assert dlg._detail_panels["ring"].change == "none"
    assert "无需更换" in dlg._detail_hint.text()


def test_result_card_summarises_changes_and_hoists_assumptions() -> None:
    """结果列表里每条组合先说要动几件、动哪几件；计算假设在首行。"""
    from lvjiang.apps.yysls.ui.loadout.optimal_combo import _SLOT_ORDER, _ResultCard

    labels = {key: name for key, name, _ft in _SLOT_ORDER}
    worn = {"main_weapon": {"name": "旧剑", "type": "剑", "level": 100, "_fp": "a"},
            "head": {"name": "旧冠", "type": "冠胄", "level": 110, "_fp": "b"}}
    card = _ResultCard(1, {
        "rate": 0.5, "dps": 1000, "gongjue": "会意",
        "equipped": {
            "main_weapon": {"name": "新剑", "type": "剑", "level": 110, "_fp": "c"},
            "head": {"name": "旧冠", "type": "冠胄", "level": 110, "_fp": "b"},
        },
        "assumptions": {"main_weapon": ["满承音"], "head": ["满承音", "满等级"]},
    }, labels, worn)

    assert card.changed_slots == ["main_weapon"]
    assert "需更换 1 件" in card.change_summary.text()
    assert card.slot_chips["main_weapon"].text().startswith("⇄")
    assert "旧剑" in card.slot_chips["main_weapon"].toolTip()
    assert not card.slot_chips["head"].text().startswith("⇄")
    # 同一条假设在多个部位出现只显示一次
    assert [p.text() for p in card.assumption_pills] == ["满承音", "满等级"]

    unchanged = _ResultCard(1, {
        "rate": 0.5, "dps": 1000, "gongjue": "会意",
        "equipped": {"head": {"name": "旧冠", "type": "冠胄", "level": 110, "_fp": "b"}},
        "assumptions": {},
    }, labels, worn)
    assert unchanged.changed_slots == []
    assert "一致" in unchanged.change_summary.text()
    assert unchanged.assumption_pills == []
