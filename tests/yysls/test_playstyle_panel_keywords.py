"""玩法配置面板里匹配关键字的编辑边界。

关键字是配置数据，面板是编辑器。切换正在编辑的玩法只该换编辑上下文——把上一个
玩法的关键字顺手写到新玩法上，或者仅仅因为程序化回填就触发一次保存，都是
「编辑态污染持久化配置」这一类问题。
"""

import pytest

from lvjiang.apps.yysls.ui.game_settings import playstyle_panel
from lvjiang.apps.yysls.ui.game_settings.playstyle_panel import PlaystylePanel


def _data() -> dict:
    arts = ["明川药典", "千香引魂蛊"]
    return {
        "schools": {},
        "martial_arts": [
            {"name": "明川药典", "weapon": "扇"},
            {"name": "千香引魂蛊", "weapon": "伞"},
        ],
        "playstyles": [
            {"name": "火拳", "school": "", "arts": list(arts),
             "match_keywords": ["输出"]},
            {"name": "纯奶", "school": "", "arts": list(arts),
             "match_keywords": ["奶", "治疗"]},
        ],
    }


def _entry(data: dict, name: str) -> dict:
    return next(e for e in data["playstyles"] if e["name"] == name)


def _select(widget: PlaystylePanel, name: str) -> None:
    for row in range(widget._list.count()):
        item = widget._list.item(row)
        if item is not None and item.text() == name:
            widget._list.setCurrentRow(row)
            return
    raise AssertionError(f"列表里没有玩法 {name!r}")


@pytest.fixture
def panel(qtbot):
    data = _data()
    saves: list[int] = []
    widget = PlaystylePanel(data=data, on_changed=lambda: saves.append(1))
    qtbot.addWidget(widget)
    return widget, data, saves


def test_switching_playstyle_loads_its_own_keywords(panel):
    widget, _data_, _saves = panel

    _select(widget, "纯奶")
    assert widget._keywords.tags() == ["奶", "治疗"]

    _select(widget, "火拳")
    assert widget._keywords.tags() == ["输出"]


def test_switching_playstyle_does_not_rewrite_the_previous_one(panel):
    """切换编辑对象不能把上一个玩法的关键字带过去，也不该因回填触发保存。"""
    widget, data, saves = panel
    saves.clear()

    _select(widget, "纯奶")
    _select(widget, "火拳")
    _select(widget, "纯奶")

    assert _entry(data, "火拳")["match_keywords"] == ["输出"]
    assert _entry(data, "纯奶")["match_keywords"] == ["奶", "治疗"]
    assert saves == [], "纯展示的回填不得触发配置保存"


def test_adding_a_keyword_saves_only_the_edited_playstyle(panel):
    widget, data, saves = panel
    _select(widget, "火拳")
    saves.clear()

    widget._keywords.add_tag("爆发")

    assert _entry(data, "火拳")["match_keywords"] == ["输出", "爆发"]
    assert _entry(data, "纯奶")["match_keywords"] == ["奶", "治疗"]
    assert saves, "用户实际编辑后必须落盘"


@pytest.mark.parametrize("control, field, empty_label", [
    ("_combo_damage_a", "main_damage", "不需要增伤"),
    ("_combo_damage_b", "sub_damage", "不需要增伤"),
    ("_combo_output", "output_dingyin", "无特定定音"),
    ("_combo_defense", "defense_dingyin", "无特定定音"),
])
def test_optional_affixes_save_values_not_display_labels(
    qtbot, monkeypatch, control, field, empty_label,
):
    # 使用不同语言的展示文本，保证占位文案不会成为配置里的词条名称。
    monkeypatch.setattr(playstyle_panel, "tr", lambda text: f"translated:{text}")
    data = _data()
    data["playstyles"][0][field] = ""
    saves = []
    widget = PlaystylePanel(data=data, on_changed=lambda: saves.append(1))
    qtbot.addWidget(widget)
    combo = getattr(widget, control)

    assert combo.currentText() == f"translated:{empty_label}"
    assert combo.currentData() == ""
    assert not saves

    # 真实选项能保存并在切换玩法后恢复。
    assert combo.count() > 1
    combo.setCurrentIndex(1)
    chosen = combo.currentData()
    assert _entry(data, "火拳")[field] == chosen
    saves.clear()
    _select(widget, "纯奶")
    _select(widget, "火拳")
    assert combo.currentData() == chosen
    assert not saves

    # 清除要求后仍写入原有空串，其他字段编辑也不得把提示文案写进去。
    combo.setCurrentIndex(0)
    widget._keywords.add_tag("测试匹配")
    assert _entry(data, "火拳")[field] == ""
    _select(widget, "纯奶")
    _select(widget, "火拳")
    assert combo.currentText() == f"translated:{empty_label}"
    assert combo.currentData() == ""
