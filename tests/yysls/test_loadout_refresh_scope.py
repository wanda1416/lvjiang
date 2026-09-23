"""备战方案页的刷新范围：谁的变更该刷新谁。

装备变更事件必须带用户名。批量任务里 B 用户每扫到一件装备就发一次事件，
不带用户名就无从过滤，正在看 A 用户的人会被 B 的扫描按住反复重建整页。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from PyQt6.QtCore import QObject, pyqtSignal

from lvjiang.apps.yysls.ui.events import EQUIPMENT_CHANGED, YyslsEventHub
from lvjiang.apps.yysls.ui.loadout.loadout_panel import LoadoutPanel

# ─── 事件载荷 ──────────────────────────────────────────────

class _EventHost(QObject):
    app_event = pyqtSignal(object)


def test_equipment_event_carries_the_username(qtbot):
    host = _EventHost()
    hub = YyslsEventHub(host)
    seen: list[str] = []
    hub.equipment_changed.connect(seen.append)

    hub.publish(EQUIPMENT_CHANGED, "甲")

    qtbot.waitUntil(lambda: seen == ["甲"], timeout=2000)


def test_debounce_keeps_each_user_separate(qtbot):
    """去抖窗口合并同一用户，但不能把两个用户合成一次。

    合成一次就得丢掉其中一个用户名，订阅方只能退回「全刷」。
    """
    hub = YyslsEventHub(_EventHost())
    seen: list[str] = []
    hub.equipment_changed.connect(seen.append)

    hub.publish(EQUIPMENT_CHANGED, "甲")
    hub.publish(EQUIPMENT_CHANGED, "甲")
    hub.publish(EQUIPMENT_CHANGED, "乙")

    qtbot.waitUntil(lambda: len(seen) == 2, timeout=2000)
    assert sorted(seen) == sorted(["甲", "乙"])


def test_missing_payload_becomes_an_unknown_source(qtbot):
    """没报用户名的发布方仍然要能把订阅方叫醒，空串即「来源未知」。"""
    hub = YyslsEventHub(_EventHost())
    seen: list[str] = []
    hub.equipment_changed.connect(seen.append)

    hub.publish(EQUIPMENT_CHANGED)

    qtbot.waitUntil(lambda: seen == [""], timeout=2000)


# ─── 订阅方过滤 ────────────────────────────────────────────

class _Timer:
    def __init__(self) -> None:
        self.started = False

    def isActive(self) -> bool:  # noqa: N802 - 对齐 QTimer
        return False

    def start(self, _ms: int) -> None:
        self.started = True


def _panel_stub(active: str = "甲"):
    return SimpleNamespace(
        _host=SimpleNamespace(active_user_name=lambda: active),
        _equipment_refresh_timer=_Timer(),
        isVisible=lambda: True,
    )


def test_another_users_scan_does_not_touch_the_current_view():
    """正在看甲，乙在跑扫描：甲这一页不该被重建。"""
    panel = _panel_stub("甲")

    LoadoutPanel._schedule_equipment_refresh(panel, "乙")

    assert panel._equipment_refresh_timer.started is False


@pytest.mark.parametrize("username", ["甲", ""])
def test_own_and_unknown_changes_still_refresh(username):
    """自己的变更照常刷新；来源未知时也刷，宁可多刷不可漏刷。"""
    panel = _panel_stub("甲")

    LoadoutPanel._schedule_equipment_refresh(panel, username)

    assert panel._equipment_refresh_timer.started is True
