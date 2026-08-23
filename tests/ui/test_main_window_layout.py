"""Main-window layout sizing and workflow-note tests."""

from types import SimpleNamespace

from PyQt6.QtWidgets import QComboBox, QFormLayout, QGroupBox

from lvjiang.ui.main_window import (
    _TOP_COMBO_CHARACTER_CAPACITY,
    MainWindow,
    _create_workflow_note_label,
    _set_combo_character_capacity,
)


def test_top_combo_capacity_fits_six_chinese_characters(qtbot):
    combo = QComboBox()
    qtbot.addWidget(combo)

    width = _set_combo_character_capacity(combo)
    text_width = combo.fontMetrics().horizontalAdvance(
        "汉" * _TOP_COMBO_CHARACTER_CAPACITY
    )

    assert width > text_width  # 还包含边框、内边距和下拉箭头
    assert combo.minimumWidth() == width
    assert combo.maximumWidth() == width


def test_workflow_note_is_wrapped_and_shown_above_parameters(qtbot):
    note_label = _create_workflow_note_label()
    param_panel = QGroupBox()
    qtbot.addWidget(note_label)
    qtbot.addWidget(param_panel)
    flow_cfg = {
        "id": "demo",
        "note": "第一行说明。\n第二行说明。",
        "parameters": [{
            "name": "rounds",
            "label": "执行轮数",
            "type": "number",
            "default": 12,
        }],
    }
    host = SimpleNamespace(
        _workflow_note_label=note_label,
        _param_panel=param_panel,
        _param_layout=QFormLayout(param_panel),
        _get_selected_flow_config=lambda: flow_cfg,
    )

    MainWindow._rebuild_param_panel(host)

    assert note_label.wordWrap() is True
    assert note_label.text() == "说明：第一行说明。\n第二行说明。"
    assert not note_label.isHidden()
    assert not param_panel.isHidden()


def test_workflow_note_is_hidden_when_metadata_is_empty(qtbot):
    note_label = _create_workflow_note_label()
    param_panel = QGroupBox()
    qtbot.addWidget(note_label)
    qtbot.addWidget(param_panel)
    host = SimpleNamespace(
        _workflow_note_label=note_label,
        _param_panel=param_panel,
        _param_layout=QFormLayout(param_panel),
        _get_selected_flow_config=lambda: {
            "id": "demo", "note": "", "parameters": []},
    )

    MainWindow._rebuild_param_panel(host)

    assert note_label.text() == ""
    assert note_label.isHidden()
    assert param_panel.isHidden()
