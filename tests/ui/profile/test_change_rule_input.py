"""规则单元格容量不足时保留输入，扩宽后可继续录入。"""

from PyQt6.QtWidgets import QToolTip

from lvjiang.ui.profile.settings_dialog import (
    _AmountTagInputWidget,
    _RuleTermTagInputWidget,
)


def test_rule_cell_rejects_overflow_without_losing_input(qapp, qtbot, monkeypatch):
    messages = []
    monkeypatch.setattr(QToolTip, "showText", lambda position, text, widget: messages.append(text))
    for widget in (_RuleTermTagInputWidget([]), _AmountTagInputWidget([])):
        qtbot.addWidget(widget)
        widget.resize(250, 36)
        widget.show()
        qapp.processEvents()
        changes = []
        widget.tags_changed.connect(lambda captured=changes: captured.append(True))

        widget._input.setText("111")
        widget._commit_input()
        qapp.processEvents()
        assert widget.tags() == ["111"]
        assert changes == [True]

        widget._input.setText("222")
        widget._commit_input()
        qapp.processEvents()
        assert widget.tags() == ["111"]
        assert widget._input.text() == "222"
        assert changes == [True]
        assert widget._content.width() <= widget.width()
        assert messages

        widget.resize(700, 36)
        qapp.processEvents()
        widget._commit_input()
        assert widget.tags() == ["111", "222"]
        assert widget._input.text() == ""
        assert changes == [True, True]

        # 从配置回填不受当前可见宽度限制，不能丢弃已保存的值。
        widget.resize(250, 36)
        widget.set_tags(["111", "222", "333", "444"])
        qapp.processEvents()
        assert widget.tags() == ["111", "222", "333", "444"]
        assert changes == [True, True]
        assert widget._content.width() <= widget.width()
