"""Alert-panel layout tests."""

from lvjiang.ui.alert_panel import AlertPanel


def test_close_button_is_left_of_alert_message(qtbot, monkeypatch):
    monkeypatch.setattr("lvjiang.ui.alert_panel.get_alerts", lambda: [])
    panel = AlertPanel()
    qtbot.addWidget(panel)

    content_row = panel.layout().itemAt(0).layout()

    assert content_row.itemAt(0).widget() is panel._close_btn
    assert content_row.itemAt(1).widget() is panel._message_label
