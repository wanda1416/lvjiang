"""规则输入不因当前显示宽度不足而丢弃标签。"""

from PyQt6.QtCore import Qt

from lvjiang.ui.profile.settings_dialog import (
    _AmountTagInputWidget,
    _RuleTermTagInputWidget,
)


def test_rule_cell_accepts_input_beyond_visible_width(qapp, qtbot):
    for widget in (_RuleTermTagInputWidget([]), _AmountTagInputWidget([])):
        qtbot.addWidget(widget)
        widget.resize(120, 36)
        widget.show()
        qapp.processEvents()

        values = ["111", "222", "333", "444"]
        for value in values:
            widget._input.setText(value)
            qtbot.keyClick(widget._input, Qt.Key.Key_Return)
            qapp.processEvents()
            assert widget._input.text() == ""
        assert widget.tags() == values

        widget.resize(700, 36)
        qapp.processEvents()
        assert widget.tags() == values
        widget.set_tags(values)
        assert widget.tags() == values
