"""规则页的玩法引用：只列已引用、显式增删、绑定开关归规则。"""
from __future__ import annotations

import pytest

from lvjiang.apps.yysls.ui.tune_settings import rule_settings_page
from lvjiang.apps.yysls.ui.tune_settings.rule_settings_page import (
    RuleSettingsPage,
)


@pytest.fixture
def page(qtbot):
    data = {
        "key": "huiyi_general", "name": "会意通用",
        "playstyles": ["无名", "九剑"],
        "playstyle_switches": {"九剑": "keep_wanjia"},
    }
    p = RuleSettingsPage(data, on_changed=lambda: None)
    qtbot.addWidget(p)
    return p


def _names(page):
    t = page._playstyle_table
    return [t.item(i, 0).text() for i in range(t.rowCount())]


def test_only_referenced_playstyles_are_listed(page):
    """不把全部玩法铺出来让人勾——规则一多就分不清哪些是真正在用的。"""
    assert _names(page) == ["无名", "九剑"]


def test_switch_column_is_editable_and_belongs_to_the_rule(page):
    """开关控制的是非武器增伤这类判定口径，属于规则；同一玩法在不同规则下
    可以绑不同开关，甚至不绑。"""
    table = page._playstyle_table
    row = _names(page).index("九剑")
    combo = table.cellWidget(row, 5)
    assert combo is not None
    assert combo.currentText() == "keep_wanjia"

    # 定义列只读（改定义去玩法配置）
    from PyQt6.QtWidgets import QAbstractItemView

    assert table.editTriggers() == QAbstractItemView.EditTrigger.NoEditTriggers

    combo.setCurrentText("")
    assert page._data.get("playstyle_switches", {}) == {}


def test_adding_a_reference_offers_only_unreferenced_ones(page, monkeypatch):
    picked = {}

    def fake_get_item(_parent, _title, _label, items, *_a, **_k):
        picked["items"] = list(items)
        return items[0], True

    monkeypatch.setattr(rule_settings_page.QInputDialog, "getItem",
                        staticmethod(fake_get_item))
    page._on_add_playstyle_ref()

    assert "无名" not in picked["items"] and "九剑" not in picked["items"]
    assert picked["items"][0] in _names(page)
    assert len(_names(page)) == 3


def test_removing_a_reference_drops_its_switch_too(page):
    table = page._playstyle_table
    table.setCurrentCell(_names(page).index("九剑"), 0)

    page._on_del_playstyle_ref()

    assert _names(page) == ["无名"]
    assert "九剑" not in (page._data.get("playstyle_switches") or {})
