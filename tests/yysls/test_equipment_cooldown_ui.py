"""装备卡片与属性对话框的冷却提醒。"""

from datetime import datetime, timedelta, timezone

from lvjiang.apps.yysls.config import get_game_config
from lvjiang.apps.yysls.core.loadout import LoadoutRepository
from lvjiang.apps.yysls.ui.loadout.equip.cards import (
    _CompactEquipCard,
    _cooldown_has_expired,
    _equipment_property_rows,
    _EquipmentPropertiesDialog,
    _format_card_cooldown,
    _format_cooldown_remaining,
    _SlotCard,
)
from lvjiang.apps.yysls.ui.loadout.equip.cooldown_manager_dialog import (
    CooldownEquipmentDialog,
    _load_cooldown_entries,
)

NOW = datetime(2026, 9, 6, 10, 0, tzinfo=timezone.utc)


def test_equipment_lock_status_is_third_property_row():
    locked = _equipment_property_rows({"_fp": "fp", "lock_status": "locked"})
    unlocked = _equipment_property_rows({"_fp": "fp", "lock_status": "unlock"})
    missing = _equipment_property_rows({"_fp": "fp"})

    assert locked[2] == ("状态", "已锁定")
    assert unlocked[2] == ("状态", "未锁定")
    assert missing[2] == ("状态", "")


def test_equipment_properties_list_referencing_plans():
    referenced = _equipment_property_rows(
        {"_fp": "fp"}, ["无名PVE", "无名PVP"])
    unreferenced = _equipment_property_rows({"_fp": "fp"})

    assert referenced[3] == ("引用方案", "无名PVE、无名PVP")
    assert unreferenced[3] == ("引用方案", "无")


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


def test_card_cooldown_text_covers_kind_state_and_remaining():
    future = NOW + timedelta(days=1, hours=2, minutes=3)

    assert _format_card_cooldown({
        "cooldown_kind": "transmute",
        "cooldown_state": "cooling",
        "cooldown_expires_at": future.isoformat(),
    }, now=NOW) == "词条转律冷却中：1 天 2 小时 3 分钟"
    assert _format_card_cooldown({
        "cooldown_kind": "reset",
        "cooldown_state": "completed",
    }, now=NOW) == "重置调律冷却完成"
    assert _format_card_cooldown({}) == ""


def test_expired_card_cooldown_is_rendered_as_completed(qtbot):
    expired = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()

    compact = _CompactEquipCard()
    slot = _SlotCard("ring", "环", "ring")
    qtbot.addWidget(compact)
    qtbot.addWidget(slot)

    compact.set_equip({
        "_fp": "fp", "name": "装备", "level": 110,
        "cooldown_kind": "reset", "cooldown_state": "cooling",
        "cooldown_expires_at": expired,
    }, "环")
    slot.set_equip({
        "_fp": "fp", "name": "装备", "level": 110,
        "cooldown_kind": "transmute", "cooldown_state": "completed",
        "cooldown_expires_at": expired,
    })
    assert compact.cooldown_label.text() == "重置调律冷却完成"
    assert slot.cooldown_label.text() == "词条转律冷却完成"
    assert not compact.cooldown_label.isHidden()
    assert not slot.cooldown_label.isHidden()
    assert compact.affix_layout.itemAt(
        compact.affix_layout.count() - 1).widget() \
        is compact.cooldown_label
    assert slot.affix_layout.itemAt(slot.affix_layout.count() - 1).widget() \
        is slot.cooldown_label

    compact.update_cooldown(future)
    slot.update_cooldown("")
    assert compact.cooldown_label.text().startswith("重置调律冷却中：")
    assert not compact.cooldown_label.isHidden()
    assert slot.cooldown_label.isHidden()


def test_short_cards_clip_bottom_without_compressing_affix_rows(qtbot):
    future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    equip = {
        "_fp": "fp", "name": "装备", "level": 110,
        **{f"affix_{index}": {"name": f"词条{index}", "value": index}
           for index in range(1, 6)},
        "dingyin": {"name": "定音", "value": 1},
        "cooldown_kind": "reset", "cooldown_state": "cooling",
        "cooldown_expires_at": future,
    }
    compact = _CompactEquipCard({"card_min_height": 80})
    slot = _SlotCard(
        "ring", "环", "ring", {"card_min_height": 80})
    qtbot.addWidget(compact)
    qtbot.addWidget(slot)
    compact.set_equip(equip, "环")
    slot.set_equip(equip)
    compact.show()
    slot.show()

    for card in (compact, slot):
        assert card.height() == 80
        first_row = card.affix_layout.itemAt(0).layout()
        assert first_row is not None
        assert first_row.itemAt(0).widget().height() > 0
        cooldown_y = card.affix_container.y() + card.cooldown_label.y()
        assert cooldown_y >= card.height()


def test_locked_badge_is_fixed_to_top_right_on_both_cards(qtbot):
    compact = _CompactEquipCard()
    slot = _SlotCard("ring", "环", "ring")
    qtbot.addWidget(compact)
    qtbot.addWidget(slot)
    compact.resize(320, compact.height())
    slot.resize(320, slot.height())

    locked = {"_fp": "fp", "name": "装备", "level": 110,
              "lock_status": "locked"}
    compact.set_equip(locked, "环")
    slot.set_equip(locked)

    for card in (compact, slot):
        assert not card.lock_badge.isHidden()
        assert card.lock_badge.size().width() == 18
        assert card.lock_badge.size().height() == 18
        assert card.lock_badge.x() == (
            card.width() - card.lock_badge.width()
            - card.lock_badge._RIGHT)
        assert card.lock_badge.y() == card.lock_badge._TOP

    unlocked = {**locked, "lock_status": "unlock"}
    compact.set_equip(unlocked, "环")
    slot.set_equip(unlocked)
    assert compact.lock_badge.isHidden()
    assert slot.lock_badge.isHidden()

    compact.set_equip({k: v for k, v in locked.items()
                       if k != "lock_status"}, "环")
    slot.set_empty()
    assert compact.lock_badge.isHidden()
    assert slot.lock_badge.isHidden()


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

    # 天数取配置：它是每次版本更新都可能改的游戏数值，钉死只会让改数据的
    # 提交无端变红，保护不了任何东西。要验的是「重置按配置天数重算」。
    days = get_game_config().get_equipment_cooldown_days()
    before = datetime.now(timezone.utc) + timedelta(days=days, seconds=-1)
    dialog._reset_cooldown_button.click()
    reset_value = datetime.fromisoformat(changes[-1])
    after = datetime.now(timezone.utc) + timedelta(days=days)
    assert before <= reset_value <= after
    assert dialog._remaining_value.text() == f"（{days} 天 0 小时 0 分钟）"

    dialog._clear_cooldown_button.click()
    assert changes[-1] == ""
    assert dialog._remaining_value.isHidden()
    assert not dialog._clear_cooldown_button.isEnabled()


def test_properties_dialog_can_clear_completed_cooldown_without_expiry(qtbot):
    """冷却完成态没有到期时间，但冷却管理器会列出它，必须能直接清除。"""
    changes: list[str] = []
    equip = {
        "_fp": "fp",
        "original_level": 110,
        "cooldown_kind": "transmute",
        "cooldown_state": "completed",
        "cooldown_expires_at": "",
    }
    dialog = _EquipmentPropertiesDialog(
        equip, cooldown_changed=lambda value: changes.append(value) or True)
    qtbot.addWidget(dialog)

    assert dialog._clear_cooldown_button.isEnabled()
    dialog._clear_cooldown_button.click()
    assert changes == [""]
    assert dialog._equip["cooldown_kind"] == ""
    assert dialog._equip["cooldown_state"] == ""
    assert not dialog._clear_cooldown_button.isEnabled()


def test_properties_dialog_carries_expired_cooldown_progress(qtbot):
    changes: list[str] = []
    previous = datetime.now(timezone.utc) - timedelta(days=1)
    dialog = _EquipmentPropertiesDialog(
        {
            "_fp": "fp",
            "original_level": 110,
            "cooldown_expires_at": previous.isoformat(),
        },
        cooldown_changed=lambda value: changes.append(value) or True,
    )
    qtbot.addWidget(dialog)

    dialog._reset_cooldown_button.click()

    reset_value = datetime.fromisoformat(changes[-1])
    assert abs(reset_value.timestamp() - (
        previous + timedelta(
            days=get_game_config().get_equipment_cooldown_days())
    ).timestamp()) < 1


def test_cooldown_manager_collects_all_users_and_sorts_ascending(tmp_path):
    alice = LoadoutRepository("alice", tmp_path)
    bob = LoadoutRepository("bob", tmp_path)
    later = {
        "_fp": "later",
        "type": "环",
        "level": 110,
        "cooldown_expires_at": "2026-09-10T10:00:00+00:00",
    }
    alice.assign_equipment(
        alice.load().active_plan_id, "ring", later)
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
    assert entries[1].referenced_plans == ("默认方案",)


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


def test_cooldown_manager_user_filter_defaults_to_all_and_filters_in_memory(qtbot, tmp_path):
    """顶部用户过滤：默认全部用户；可多选；全选时按钮显示「全部用户」。"""
    for name, count in (("alice", 2), ("bob", 3), ("carol", 1)):
        repo = LoadoutRepository(name, tmp_path)
        for index in range(count):
            repo.upsert_item({
                "_fp": f"{name}-{index}", "type": "环", "level": 110,
                "cooldown_expires_at": (NOW + timedelta(days=index + 1)).isoformat(),
            })

    dialog = CooldownEquipmentDialog(["alice", "bob", "carol"], {}, users_dir=tmp_path)
    qtbot.addWidget(dialog)

    assert dialog._user_filter.text() == "全部用户"
    assert dialog._user_filter.selected_keys() == ["alice", "bob", "carol"]
    assert len(dialog._tiles) == 6
    assert dialog._count_label.text() == "6 / 6 件"

    dialog._user_filter.set_selected(["bob", "carol"])
    assert len(dialog._tiles) == 4
    assert {tile.entry.username for tile in dialog._tiles} == {"bob", "carol"}
    assert dialog._user_filter.text() == "bob、carol"
    assert not dialog._user_filter._all_action.isChecked()

    # 勾回「全部」→ 全选，文字恢复
    dialog._user_filter._all_action.setChecked(True)
    assert dialog._user_filter.selected_keys() == ["alice", "bob", "carol"]
    assert dialog._user_filter.text() == "全部用户"
    assert len(dialog._tiles) == 6

    # 一个都不选：空态提示区分"没有冷却装备"与"所选用户没有"
    dialog._user_filter.set_selected([])
    assert dialog._tiles == []
    assert dialog._user_filter.text() == "未选择用户"
