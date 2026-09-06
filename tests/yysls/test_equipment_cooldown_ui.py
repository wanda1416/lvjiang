"""装备卡片与属性对话框的冷却提醒。"""

from datetime import datetime, timedelta, timezone

from lvjiang.apps.yysls.core.loadout import LoadoutRepository
from lvjiang.apps.yysls.ui.loadout.equip.cards import (
    _CompactEquipCard,
    _cooldown_has_expired,
    _EquipmentPropertiesDialog,
    _format_cooldown_remaining,
    _SlotCard,
)
from lvjiang.apps.yysls.ui.loadout.equip.cooldown_manager_dialog import (
    CooldownEquipmentDialog,
    _load_cooldown_entries,
)

NOW = datetime(2026, 9, 6, 10, 0, tzinfo=timezone.utc)


def test_cooldown_remaining_rounds_up_partial_minute():
    expires = NOW + timedelta(days=2, hours=3, minutes=4, seconds=1)

    assert _format_cooldown_remaining(
        expires.isoformat(), now=NOW) == "2 天 3 小时 5 分钟"
    assert not _cooldown_has_expired(expires.isoformat(), now=NOW)


def test_expired_cooldown_stops_at_zero():
    expires = NOW - timedelta(seconds=1)

    assert _format_cooldown_remaining(
        expires.isoformat(), now=NOW) == "0 天 0 小时 0 分钟"
    assert _cooldown_has_expired(expires.isoformat(), now=NOW)
    assert _format_cooldown_remaining("损坏时间", now=NOW) == ""
    assert not _cooldown_has_expired("损坏时间", now=NOW)


def test_expired_badge_is_shown_on_both_equipment_cards(qtbot):
    expired = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()

    compact = _CompactEquipCard()
    slot = _SlotCard("ring", "环", "ring")
    qtbot.addWidget(compact)
    qtbot.addWidget(slot)

    compact.set_equip({
        "_fp": "fp", "name": "装备", "level": 110,
        "cooldown_expires_at": expired,
    }, "环")
    slot.set_equip({
        "_fp": "fp", "name": "装备", "level": 110,
        "cooldown_expires_at": expired,
    })
    assert not compact.cooldown_badge.isHidden()
    assert not slot.cooldown_badge.isHidden()

    compact.cooldown_badge.set_cooldown(future)
    slot.cooldown_badge.set_cooldown("")
    assert compact.cooldown_badge.isHidden()
    assert slot.cooldown_badge.isHidden()


def test_properties_dialog_spaces_fields_and_updates_cooldown(qtbot):
    changes: list[str] = []
    initial = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    dialog = _EquipmentPropertiesDialog(
        {
            "_fp": "fp",
            "original_level": 110,
            "cooldown_expires_at": initial,
        },
        cooldown_changed=lambda value: changes.append(value) or True,
    )
    qtbot.addWidget(dialog)

    form = dialog.layout().itemAt(0).layout()
    assert form.verticalSpacing() >= 12
    assert not dialog._remaining_value.isHidden()
    assert dialog._remaining_value.text().startswith("（")

    before = datetime.now(timezone.utc) + timedelta(days=5, seconds=-1)
    dialog._reset_cooldown_button.click()
    reset_value = datetime.fromisoformat(changes[-1])
    after = datetime.now(timezone.utc) + timedelta(days=5)
    assert before <= reset_value <= after
    assert dialog._remaining_value.text() == "（5 天 0 小时 0 分钟）"

    dialog._clear_cooldown_button.click()
    assert changes[-1] == ""
    assert dialog._remaining_value.isHidden()
    assert not dialog._clear_cooldown_button.isEnabled()


def test_cooldown_manager_collects_all_users_and_sorts_ascending(tmp_path):
    alice = LoadoutRepository("alice", tmp_path)
    bob = LoadoutRepository("bob", tmp_path)
    alice.upsert_item({
        "_fp": "later",
        "type": "环",
        "level": 110,
        "cooldown_expires_at": "2026-09-10T10:00:00+00:00",
    })
    alice.upsert_item({
        "_fp": "without-cooldown",
        "type": "佩",
        "level": 110,
        "cooldown_expires_at": "",
    })
    bob.upsert_item({
        "_fp": "earlier",
        "type": "腕甲",
        "level": 110,
        "cooldown_expires_at": "2026-09-08T10:00:00+00:00",
    })

    entries = _load_cooldown_entries(["alice", "bob"], tmp_path)

    assert [(entry.username, entry.equip["_fp"]) for entry in entries] == [
        ("bob", "earlier"),
        ("alice", "later"),
    ]


def test_cooldown_manager_uses_six_columns_and_shared_cards(qtbot, tmp_path):
    repo = LoadoutRepository("alice", tmp_path)
    for index in range(7):
        repo.upsert_item({
            "_fp": f"fp-{index}",
            "type": "环",
            "level": 110,
            "cooldown_expires_at": (
                NOW + timedelta(days=index + 1)).isoformat(),
        })

    dialog = CooldownEquipmentDialog(
        ["alice"], {}, users_dir=tmp_path)
    qtbot.addWidget(dialog)
    grid = dialog._scroll.widget().layout()

    assert len(dialog._tiles) == 7
    assert grid.itemAtPosition(0, 5).widget() is dialog._tiles[5]
    assert grid.itemAtPosition(1, 0).widget() is dialog._tiles[6]
    assert all(tile.card._context_mode == "properties"
               for tile in dialog._tiles)
