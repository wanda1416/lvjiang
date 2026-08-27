"""yysls 事件适配器：命名空间过滤与装备刷新防抖。"""

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QWidget

from lvjiang.apps.yysls.ui.events import (
    EQUIPMENT_CHANGED,
    OPEN_PLAY_STYLE_FORM,
    get_event_hub,
)
from lvjiang.ui.app_events import AppEvent


class _Host(QWidget):
    app_event = pyqtSignal(object)


def test_event_hub_filters_other_apps_and_routes_payload(qtbot):
    host = _Host()
    qtbot.addWidget(host)
    hub = get_event_hub(host)
    seen: list[dict] = []
    hub.open_play_style_form.connect(seen.append)
    host.app_event.emit(AppEvent("other", OPEN_PLAY_STYLE_FORM, {"x": 1}))
    hub.publish(OPEN_PLAY_STYLE_FORM, {"x": 2})
    assert seen == [{"x": 2}]


def test_equipment_events_are_debounced_in_plugin_hub(qtbot):
    host = _Host()
    qtbot.addWidget(host)
    hub = get_event_hub(host)
    seen: list[bool] = []
    hub.equipment_changed.connect(lambda: seen.append(True))
    with qtbot.waitSignal(hub.equipment_changed, timeout=1000):
        hub.publish(EQUIPMENT_CHANGED)
        hub.publish(EQUIPMENT_CHANGED)
        hub.publish(EQUIPMENT_CHANGED)
    assert seen == [True]
