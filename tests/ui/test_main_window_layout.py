"""Main-window layout sizing and workflow-note tests."""

from types import SimpleNamespace

from PyQt6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QSpinBox,
    QWidget,
)

from lvjiang.ui.main.window import (
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


def test_top_combo_keeps_compact_control_but_expands_popup(qtbot):
    combo = QComboBox()
    qtbot.addWidget(combo)
    width = _set_combo_character_capacity(combo)

    combo.addItem("这是一个明显超过六个汉字的配置名")

    assert combo.width() == width
    assert combo.view().minimumWidth() > width


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


def test_checkgroup_uses_full_width_and_actual_wrapped_height(qtbot):
    note_label = _create_workflow_note_label()
    param_panel = QGroupBox()
    param_panel.setFixedWidth(400)
    qtbot.addWidget(note_label)
    qtbot.addWidget(param_panel)
    param_layout = QFormLayout(param_panel)
    flow_cfg = {
        "id": "demo",
        "parameters": [
            {
                "name": "choices",
                "label": "八个选项",
                "type": "checkgroup",
                "options": [f"选项 {index}" for index in range(8)],
            },
            {
                "name": "rounds",
                "label": "下一参数",
                "type": "number",
                "default": 1,
            },
        ],
    }
    host = SimpleNamespace(
        _workflow_note_label=note_label,
        _param_panel=param_panel,
        _param_layout=param_layout,
        _get_selected_flow_config=lambda: flow_cfg,
    )

    MainWindow._rebuild_param_panel(host)
    param_panel.show()
    qtbot.wait(10)

    label_item = param_layout.itemAt(0, QFormLayout.ItemRole.SpanningRole)
    group_item = param_layout.itemAt(1, QFormLayout.ItemRole.SpanningRole)
    assert isinstance(label_item.widget(), QLabel)
    assert label_item.widget().text() == "八个选项:"

    container = group_item.widget()
    assert isinstance(container, QWidget)
    assert container.objectName() == "choices"
    assert container.height() == container.heightForWidth(container.width())

    next_widget = param_panel.findChild(QSpinBox, "rounds")
    assert next_widget is not None
    assert container.x() < next_widget.x()
    assert container.width() > next_widget.width()
    actual_gap = next_widget.y() - (container.y() + container.height())
    assert actual_gap <= max(0, param_layout.verticalSpacing()) + 1
