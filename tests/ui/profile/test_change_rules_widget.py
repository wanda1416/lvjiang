"""数据模型变动规则编辑器的紧凑布局回归测试。"""

from types import SimpleNamespace

from PyQt6.QtWidgets import QDoubleSpinBox

import lvjiang.core.profile as profile_core
from lvjiang.core.profile.models import StepDef, SyncTargetDef
from lvjiang.ui.profile.settings_dialog import _ChangeRulesWidget, _SyncTargetsWidget


def test_change_rule_rows_use_compact_amount_editor(qtbot):
    widget = _ChangeRulesWidget([
        StepDef(value=-1, source="和鸣抽奖"),
        StepDef(value=-5, source="和鸣抽奖"),
    ])
    qtbot.addWidget(widget)
    widget.resize(800, 300)
    widget.show()
    qtbot.waitExposed(widget)

    amount_editor = widget._table.cellWidget(0, 2)
    assert amount_editor is not None
    assert amount_editor.height() == 36
    assert widget._table.rowHeight(0) <= 38


def test_sync_ratio_accepts_large_negative_value(qtbot, monkeypatch):
    monkeypatch.setattr(
        profile_core,
        "get_profile_config",
        lambda: SimpleNamespace(get_keys_by_model=lambda _model: []),
    )
    widget = _SyncTargetsWidget()
    qtbot.addWidget(widget)
    widget.add_row(SyncTargetDef(key="stock:target", ratio=-3000))

    ratio_spin = widget._table.cellWidget(0, 1)
    assert isinstance(ratio_spin, QDoubleSpinBox)
    assert ratio_spin.value() == -3000
    assert ratio_spin.minimum() == float("-inf")
    assert ratio_spin.maximum() == float("inf")
