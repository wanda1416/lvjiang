"""流派配置详情共用滚动区与方案/属性卡片的布局边界。"""

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtWidgets import QApplication, QGroupBox, QLabel, QScrollArea

from lvjiang.apps.yysls.core.combat.combat_attrs import CombatAttributes
from lvjiang.apps.yysls.ui.game_settings.school_panel import SchoolPanel


def _panel(qtbot) -> SchoolPanel:
    panel = SchoolPanel(
        data={"schools": {"测试流派": {"attr": "鸣金"}}},
        on_changed=lambda: None,
    )
    qtbot.addWidget(panel)
    panel.resize(900, 700)
    panel.show()
    return panel


def test_school_details_scroll_together_without_squeezing_values(qtbot):
    panel = _panel(qtbot)
    card = panel._create_standard_attrs_widget(
        CombatAttributes(), "鸣金", editable=True,
    )
    panel._value_content_layout.addWidget(card)
    QApplication.processEvents()

    scroll_areas = panel.findChildren(QScrollArea)
    assert len(scroll_areas) == 1
    scroll = scroll_areas[0]
    assert scroll.verticalScrollBar().maximum() > 0
    assert scroll.horizontalScrollBar().maximum() == 0
    assert card.height() >= card.sizeHint().height()
    assert all(edit.maximumWidth() <= 112 for edit in panel._ps_edits.values())

    labels = {label.text() for label in card.findChildren(QLabel)}
    assert "属攻伤害加成" in labels
    assert "属攻伤害减免" in labels
    assert "属攻伤害加成（鸣金）" not in labels

    panel._clear_ps_editor()
    QApplication.processEvents()
    assert scroll.verticalScrollBar().maximum() == 0


def test_scheme_list_aligns_with_base_list_and_import_stays_below(qtbot):
    panel = _panel(qtbot)
    QApplication.processEvents()
    scheme_top = panel._scheme_list.mapTo(panel, panel._scheme_list.rect().topLeft()).y()
    base_top = panel._ps_list.mapTo(panel, panel._ps_list.rect().topLeft()).y()
    import_top = panel._btn_import_scheme.mapTo(
        panel, panel._btn_import_scheme.rect().topLeft()
    ).y()
    assert scheme_top == base_top
    assert import_top > scheme_top
    assert panel._scheme_group.geometry().height() == panel._base_attrs_group.geometry().height()


def test_scheme_adps_precedes_attack_attributes(qtbot, monkeypatch):
    panel = _panel(qtbot)
    panel._scheme_list.addItem("测试方案")
    monkeypatch.setattr(
        "lvjiang.apps.yysls.core.graduation.get_graduation_scheme_combat_attrs",
        lambda _school, _scheme: CombatAttributes(),
    )
    monkeypatch.setattr(
        "lvjiang.apps.yysls.core.graduation.get_graduation_scheme_metrics",
        lambda _school, _scheme: (100.0, 120.0),
    )
    panel._on_scheme_selected(0)
    assert panel._value_content_layout.itemAt(0).widget().objectName() == "schemeMetricsPanel"
    card = panel._value_content_layout.itemAt(1).widget()
    assert any(group.title() == "攻击属性" for group in card.findChildren(QGroupBox))
