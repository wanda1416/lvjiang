"""候选评级的玩法多选器。

只能选一条规则时，能留下的装备极少——一件装备往往只对得上其中一两套
练法。这里验的是「本流派排在前面、能多选、全选/全不选覆盖整张表」。
"""
from __future__ import annotations

import pytest
from PyQt6.QtCore import Qt

from lvjiang.apps.yysls.ui.loadout.optimal_combo import (
    _MIN_RATING_CHOICES,
    _ClickableLineEdit,
    _PlaystylePickerDialog,
)

pytestmark = pytest.mark.usefixtures("qapp")

_OPTIONS = [
    ("huixin_small", "双切", "会心小-双切", True),
    ("huixin_small", "单切", "会心小-单切", True),
    ("jingzhun", "远程", "精准-远程", False),
]


def test_the_current_schools_playstyles_are_marked(qtbot=None) -> None:
    """几十条玩法混在一起，不标出来找自己那几条要翻半天。"""
    dialog = _PlaystylePickerDialog(_OPTIONS, set())

    labels = [dialog._list.item(i).text() for i in range(dialog._list.count())]
    assert "（本流派）" in labels[0]
    assert "（本流派）" not in labels[2]


def test_preselected_pairs_come_back_checked() -> None:
    dialog = _PlaystylePickerDialog(
        _OPTIONS, {("huixin_small", "单切")})

    assert dialog.values() == [("huixin_small", "单切")]


def test_select_all_and_none_cover_every_row() -> None:
    dialog = _PlaystylePickerDialog(_OPTIONS, set())

    dialog._set_all(True)
    assert len(dialog.values()) == 3
    dialog._set_all(False)
    assert dialog.values() == []


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

def test_combo_detail_tab_shows_the_current_equipment() -> None:
    """满承音/满等级只是算分假设，把假设值摆成装备详情会让人以为装备
    真是那样。"""
    from lvjiang.apps.yysls.ui.loadout.optimal_combo import OptimalComboDialog

    dlg = OptimalComboDialog.__new__(OptimalComboDialog)
    from PyQt6.QtWidgets import QLabel, QTabWidget

    from lvjiang.apps.yysls.ui.loadout.equip.cards import _SlotCard

    dlg._detail_cards = {
        "main_weapon": _SlotCard("main_weapon", "主武器", "weapon"),
        "head": _SlotCard("head", "冠胄", "head"),
    }
    dlg._detail_hint = QLabel()
    dlg._tab_widget = QTabWidget()
    for _ in range(3):
        dlg._tab_widget.addTab(QLabel(), "t")

    equip = {"name": "测试剑", "type": "剑", "level": 110, "quality": "gold",
             "dingyin": {"name": "无相穿透", "value": 1.0}}
    OptimalComboDialog._on_show_detail(dlg, {"main_weapon": equip})

    assert "测试剑" in dlg._detail_cards["main_weapon"].lbl_name.text()
    # 组合里没有的槽位显示空卡，而不是留着上一次的内容
    assert "冠胄" == dlg._detail_cards["head"].lbl_name.text()
    assert dlg._tab_widget.currentIndex() == 2
