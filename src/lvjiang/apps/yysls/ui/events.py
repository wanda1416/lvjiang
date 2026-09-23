"""yysls 事件的类型化 Qt 适配器。"""

from __future__ import annotations

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from lvjiang.ui.app_events import AppEvent

from ..core.events import (
    APP_ID,
    EQUIPMENT_CHANGED,
    GRADUATION_UPDATED,
    OPEN_PLAY_STYLE_FORM,
)

__all__ = [
    "APP_ID", "EQUIPMENT_CHANGED", "GRADUATION_UPDATED", "OPEN_PLAY_STYLE_FORM",
    "YyslsEventHub", "get_event_hub",
]


class YyslsEventHub(QObject):
    #: 参数是发生变更的用户名。批量任务里别的用户正在扫描装备，每写一件就
    #: 发一次事件；订阅方据此只响应自己正在看的用户，不被旁人的扫描拖着
    #: 重建整页。空串表示来源未知，订阅方不应据此过滤。
    equipment_changed = pyqtSignal(str)
    open_play_style_form = pyqtSignal(dict)
    graduation_updated = pyqtSignal(object)

    def __init__(self, host) -> None:
        super().__init__(host)
        self._host = host
        #: 去抖窗口内攒下的用户名；同一用户只发一次，不同用户各发一次。
        self._pending_equipment_users: set[str] = set()
        self._equipment_timer = QTimer(self)
        self._equipment_timer.setSingleShot(True)
        self._equipment_timer.setInterval(150)
        self._equipment_timer.timeout.connect(self._flush_equipment_changed)
        host.app_event.connect(self._route)

    def _flush_equipment_changed(self) -> None:
        pending, self._pending_equipment_users = (
            self._pending_equipment_users, set())
        for username in sorted(pending):
            self.equipment_changed.emit(username)

    def publish(self, topic: str, payload=None) -> None:
        self._host.app_event.emit(AppEvent(APP_ID, topic, payload))

    def _route(self, event: object) -> None:
        if not isinstance(event, AppEvent) or event.app_id != APP_ID:
            return
        if event.topic == EQUIPMENT_CHANGED:
            self._pending_equipment_users.add(
                event.payload if isinstance(event.payload, str) else "")
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
