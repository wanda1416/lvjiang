"""推导表按用户看到的单位与精度展示和比较，不改变保存值。"""

from dataclasses import replace
from types import SimpleNamespace

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QLabel, QTableWidget

from lvjiang.apps.yysls.core.attr_model import AttrLoadout, StatEffect, resolve
from lvjiang.apps.yysls.ui.game_settings.attr_derive_panel import AttrDerivePanel


def test_derivation_formats_units_and_compares_at_display_precision(qtbot):
    result = resolve(
        [StatEffect(
            source_id="test", label="测试来源", kind="inner_way",
            stats={"min_outer": 100.14, "crit_rate": 0.02304},
            extra={"剑武学增伤": 0.02304},
        )],
        level=115, school_attr="鸣金", caps_lookup=lambda *_args: None,
    )
    reference = replace(result.panel_attrs)
    reference.min_outer = 100.10
    reference.crit_rate = 0.023
    reference.extra_attrs = {"剑武学增伤": 0.023}
    residual = {"min_outer": -0.04, "crit_rate": -0.00004, "extra:剑武学增伤": -0.00004}
    table = QTableWidget(0, 5)
    summary = QLabel()
    qtbot.addWidget(table)
    qtbot.addWidget(summary)
    host = SimpleNamespace(
        _table=table, _summary=summary,
        _resolve=lambda _loadout: result,
        _reference_attrs=lambda _loadout: reference,
        _residual=lambda *_args: residual,
    )
    host._rows = lambda *args: AttrDerivePanel._rows(host, *args)
    host._fill_table = lambda *args: AttrDerivePanel._fill_table(host, *args)
    loadout = AttrLoadout(level=115, school="鸣金·虹")
    AttrDerivePanel._recompute(host, loadout)
    rows = {name: index for index, (name, _, _) in enumerate(host._rows(result, reference))}

    for name, expected in (("min_outer", "100.1"), ("crit_rate", "2.30%"), ("剑武学增伤", "2.30%")):
        row = rows[name]
        assert table.item(row, 1).text() == expected
        assert table.item(row, 2).text() == "-"
        assert table.item(row, 3).text() == expected
        assert table.item(row, 3).foreground().color() != Qt.GlobalColor.red
        assert expected in table.item(row, 4).text()
    assert "与对照完全一致" in summary.text()
    assert "靠补足" not in summary.text()

    # 真正超过显示精度的差异仍显示、计数并标红（未补足）。
    reference.crit_rate = 0.0232
    residual.clear()
    AttrDerivePanel._recompute(host, loadout)
    row = rows["crit_rate"]
    assert table.item(row, 3).text() == "2.32%"
    assert table.item(row, 3).foreground().color() == Qt.GlobalColor.red
    assert "与对照有 1 项不一致" in summary.text()
    assert result.panel_attrs.crit_rate == 0.02304
