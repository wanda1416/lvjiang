"""备战方案页的刷新范围：谁的变更、刷几次。

两条契约，都是实测出来的卡顿来源：

1. 装备变更事件必须带用户名。批量任务里 B 用户每扫到一件装备就发一次事件，
   不带用户名就无从过滤，正在看 A 用户的人会被 B 的扫描按住反复重建整页。
2. 切换用户只重载一次。装备页和外层面板曾经各自订阅 user_changed、各读一份
   ``EquipmentInventory``、各重建一次网格；筛选还要再重建第三次。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWidgets import QComboBox

import lvjiang.apps.yysls.core.combat.equipment as equipment_module
import lvjiang.constants as constants_module
from lvjiang.apps.yysls.core.loadout import LoadoutRepository
from lvjiang.apps.yysls.ui.events import EQUIPMENT_CHANGED, YyslsEventHub
from lvjiang.apps.yysls.ui.loadout.equip.status_tab import EquipStatusTab
from lvjiang.apps.yysls.ui.loadout.loadout_panel import LoadoutPanel
from lvjiang.core.user_config import User, save_user_metadata

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


# ─── 切换用户只重载一次 ────────────────────────────────────


class _Users:
    @staticmethod
    def list_users() -> list[str]:
        return ["甲", "乙"]


class _Host(QObject):
    user_changed = pyqtSignal(str)
    app_event = pyqtSignal(object)

    def __init__(self, name: str = "") -> None:
        super().__init__()
        self.user_manager = _Users()
        self.user_combo = QComboBox()
        self.name = name

    def active_user_name(self) -> str:
        return self.name

    @staticmethod
    def navigate_user(_delta: int) -> None:
        return None


def test_user_switch_loads_one_inventory_and_rebuilds_once(
        qtbot, tmp_path, monkeypatch):
    """换用户：一份 EquipmentInventory、一次网格重建。

    面板与装备页曾经各订阅一次 user_changed，各读一份库存；装备页还要在
    换上新筛选后再重建一次网格。三次重建里只有一次有意义。
    """
    monkeypatch.setattr(constants_module, "USERS_DIR", tmp_path)
    loaded: list[str] = []
    real = equipment_module.EquipmentInventory

    class _Counting(real):  # type: ignore[valid-type,misc]
        def __init__(self, user_name: str) -> None:
            loaded.append(user_name)
            super().__init__(user_name)

    monkeypatch.setattr(equipment_module, "EquipmentInventory", _Counting)

    host = _Host("甲")
    panel = LoadoutPanel(host)
    qtbot.addWidget(panel)
    assert loaded == ["甲"]

    rebuilds: list[str] = []
    panel._equipment._rebuild_grid = lambda: rebuilds.append("grid")
    host.name = "乙"
    host.user_changed.emit("乙")

    assert loaded == ["甲", "乙"]
    assert rebuilds == ["grid"]


def test_saved_filters_are_applied_on_startup(qtbot, tmp_path, monkeypatch):
    """启动时就要装上该用户存下来的筛选，而不是切一次用户才生效。

    构造期那次 _load_filter_settings 还没有 _inv，读不到任何用户的存档，
    摆出来的只能是默认值——真正属于这个用户的筛选要等库存注入后才读得到。
    """
    monkeypatch.setattr(constants_module, "USERS_DIR", tmp_path)
    save_user_metadata(User(name="甲"), tmp_path)
    LoadoutRepository("甲", tmp_path).set_ui_state(
        "equip_filter", {"sort": "level_desc"})

    panel = LoadoutPanel(_Host("甲"))
    qtbot.addWidget(panel)

    assert panel._equipment._sort_filter.currentData() == "level_desc"


# ─── 筛选读取的时点 ────────────────────────────────────────


def _equip_stub():
    fake = SimpleNamespace(
        _inv="甲的库存",
        _user_filters_stale=False,
        _reload_display_params=lambda: None,
        _update_status_row=lambda: None,
        seen=[],
    )
    fake._exit_batch_copy_mode = lambda *, rebuild: None
    fake._apply_pending_filters = (
        lambda: EquipStatusTab._apply_pending_filters(fake))
    fake._load_filter_settings = lambda: fake.seen.append(("筛选", fake._inv))
    fake._sync_inv = lambda: fake.seen.append(("网格", fake._inv))
    return fake


def test_filters_are_read_after_the_new_inventory_and_before_the_grid():
    """筛选存在各用户自己的仓储里，早读一步拿到的是上一个用户的筛选。

    晚读一步则要用旧筛选先建一次网格再重建——这正是原来的第三次重建。
    """
    fake = _equip_stub()

    EquipStatusTab.prepare_for_user_change(fake)
    EquipStatusTab.refresh_from(fake, "乙的库存")

    assert fake.seen == [("筛选", "乙的库存"), ("网格", "乙的库存")]


def test_a_plain_refresh_does_not_reset_the_filter_bar():
    """扫描写入触发的刷新不是换用户，不该把用户调好的筛选条重置回存档值。"""
    fake = _equip_stub()

    EquipStatusTab.refresh_from(fake, "甲的库存")

    assert fake.seen == [("网格", "甲的库存")]
