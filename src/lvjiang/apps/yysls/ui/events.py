"""yysls 事件的类型化 Qt 适配器。"""

from __future__ import annotations

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from lvjiang.ui.app_events import AppEvent

APP_ID = "yysls"
EQUIPMENT_CHANGED = "equipment.changed"
OPEN_PLAY_STYLE_FORM = "play_style.open_form"
GRADUATION_UPDATED = "graduation.updated"


class YyslsEventHub(QObject):
    equipment_changed = pyqtSignal()
    open_play_style_form = pyqtSignal(dict)
    graduation_updated = pyqtSignal(object)

    def __init__(self, host) -> None:
        super().__init__(host)
        self._host = host
        self._equipment_timer = QTimer(self)
        self._equipment_timer.setSingleShot(True)
        self._equipment_timer.setInterval(150)
        self._equipment_timer.timeout.connect(self.equipment_changed)
        host.app_event.connect(self._route)

    def publish(self, topic: str, payload=None) -> None:
        self._host.app_event.emit(AppEvent(APP_ID, topic, payload))

    def _route(self, event: object) -> None:
        if not isinstance(event, AppEvent) or event.app_id != APP_ID:
            return
        if event.topic == EQUIPMENT_CHANGED:
            self._equipment_timer.start()
        elif event.topic == OPEN_PLAY_STYLE_FORM:
            self.open_play_style_form.emit(
                event.payload if isinstance(event.payload, dict) else {}
            )
        elif event.topic == GRADUATION_UPDATED:
            self.graduation_updated.emit(event.payload)


def get_event_hub(host) -> YyslsEventHub:
    hub = getattr(host, "_yysls_event_hub", None)
    if hub is None:
        hub = YyslsEventHub(host)
        host._yysls_event_hub = hub
    return hub
