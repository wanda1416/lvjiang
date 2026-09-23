"""装备展示新增品阶、调律进度和备战状态筛选。"""

from datetime import datetime, timezone
from types import SimpleNamespace

from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import (
    QComboBox,
    QGridLayout,
    QLabel,
    QMenu,
    QPushButton,
    QStyle,
    QStyleOptionComboBox,
    QWidget,
)

import lvjiang.apps.yysls.config as config_module
from lvjiang.apps.yysls.ui.loadout.equip.cards import (
    _CompactEquipCard,
    _SlotCard,
)
from lvjiang.apps.yysls.ui.loadout.equip.status_tab import (
    EquipStatusTab,
    _FilteredDeleteDialog,
    _fit_filter_combo,
)


def _combo(value: str) -> QComboBox:
    combo = QComboBox()
    combo.addItem(value, value)
    return combo


def _filter_stub(*, quality: str, affix: str, status: str):
    return SimpleNamespace(
        _quality_filter=_combo(quality),
        _status_filter=_combo(status),
        _get_level_threshold=lambda: 0,
        _get_affix_filter=lambda: affix,
    )


def _equip(*, quality: str | None = "gold", affix_count: int = 1) -> dict:
    equip = {"quality": quality, "level": 110}
    for index in range(1, affix_count + 1):
        equip[f"affix_{index}"] = {"name": f"词条{index}"}
    return equip


def test_filter_combo_fits_text_with_style_padding_and_arrow(qtbot):
    combo = QComboBox()
    combo.setStyleSheet("QComboBox { padding: 8px 16px; font-size: 18px; }")
    combo.addItems(["全部", "未满调律"])
    qtbot.addWidget(combo)
    width = _fit_filter_combo(combo)
    combo.resize(width, combo.sizeHint().height())
    option = QStyleOptionComboBox()
    combo.initStyleOption(option)
    field = combo.style().subControlRect(
        QStyle.ComplexControl.CC_ComboBox, option,
        QStyle.SubControl.SC_ComboBoxEditField, combo)

    assert field.width() >= combo.fontMetrics().horizontalAdvance("未满调律")
    assert combo.view().minimumWidth() >= width
    assert combo.maximumWidth() > width  # 父布局仍可按空间拉宽


def test_white_quality_means_any_non_gold_non_purple(qtbot):
    tab = _filter_stub(quality="other", affix="all", status="all")
    qtbot.addWidget(tab._quality_filter)
    qtbot.addWidget(tab._status_filter)

    assert EquipStatusTab._equip_passes_filter(tab, _equip(quality="blue"))
    assert EquipStatusTab._equip_passes_filter(tab, _equip(quality="green"))
    assert EquipStatusTab._equip_passes_filter(tab, _equip(quality=None))
    assert not EquipStatusTab._equip_passes_filter(tab, _equip(quality="gold"))
    assert not EquipStatusTab._equip_passes_filter(tab, _equip(quality="purple"))


def test_not_full_tuning_accepts_at_most_four_affixes(qtbot):
    tab = _filter_stub(
        quality="all", affix="not_full_tuning", status="all")
    qtbot.addWidget(tab._quality_filter)
    qtbot.addWidget(tab._status_filter)

    assert EquipStatusTab._equip_passes_filter(tab, _equip(affix_count=4))
    assert not EquipStatusTab._equip_passes_filter(tab, _equip(affix_count=5))


def test_loadout_status_uses_any_plan_reference(qtbot):
    referenced = _filter_stub(
        quality="all", affix="all", status="referenced")
    unreferenced = _filter_stub(
        quality="all", affix="all", status="unreferenced")
    for tab in (referenced, unreferenced):
        qtbot.addWidget(tab._quality_filter)
        qtbot.addWidget(tab._status_filter)

    equip = _equip()
    assert EquipStatusTab._equip_passes_filter(
        referenced, equip, is_referenced=True)
    assert not EquipStatusTab._equip_passes_filter(
        referenced, equip, is_referenced=False)
    assert EquipStatusTab._equip_passes_filter(
        unreferenced, equip, is_referenced=False)
    assert not EquipStatusTab._equip_passes_filter(
        unreferenced, equip, is_referenced=True)


def test_scan_time_filter_falls_back_to_updated_at_and_ignores_mocks(qtbot):
    scan_filter = QComboBox()
    scan_filter.addItem("超过 3 天", "3")
    qtbot.addWidget(scan_filter)
    tab = SimpleNamespace(_scan_time_filter=scan_filter)
    now = datetime(2026, 9, 23, tzinfo=timezone.utc)
    old = {"updated_at": "2026-09-19T23:59:59+00:00"}
    recent = {"updated_at": "2026-09-21T00:00:00+00:00"}

    assert EquipStatusTab._passes_scan_time_filter(
        tab, old, is_mock=False, now=now)
    assert not EquipStatusTab._passes_scan_time_filter(
        tab, recent, is_mock=False, now=now)
    assert EquipStatusTab._passes_scan_time_filter(
        tab, recent, is_mock=True, now=now)


def test_scan_time_filter_treats_records_without_any_time_as_expired(qtbot):
    scan_filter = QComboBox()
    scan_filter.addItem("超过 30 天", "30")
    qtbot.addWidget(scan_filter)
    tab = SimpleNamespace(_scan_time_filter=scan_filter)

    assert EquipStatusTab._passes_scan_time_filter(
        tab, {}, is_mock=False,
        now=datetime(2026, 9, 23, tzinfo=timezone.utc))


def test_filtered_collection_excludes_current_equipment_and_marks_references(
    qtbot, monkeypatch,
):
    source_filter = _combo("all")
    qtbot.addWidget(source_filter)
    monkeypatch.setattr(
        config_module,
        "get_game_config",
        lambda: SimpleNamespace(get_group_to_part=lambda: {"ring": "环"}),
    )
    tab = SimpleNamespace(
        _selected_slot=None,
        _inv=SimpleNamespace(
            active_plan_fps={"worn"},
            referenced_plan_fps={"worn", "standby"},
        ),
        _source_filter=source_filter,
        _bag_items={
            "ring": {
                "worn": {"_fp": "worn"},
                "standby": {"_fp": "standby"},
                "free": {"_fp": "free"},
            },
        },
        _mock_items={},
        _equip_passes_filter=lambda _equip, *, is_referenced: True,
        _passes_scan_time_filter=lambda _equip, *, is_mock: True,
    )

    cards = EquipStatusTab._collect_filtered_cards(tab)

    assert [(card[0]["_fp"], card[4]) for card in cards] == [
        ("standby", True),
        ("free", False),
    ]


def test_filtered_delete_never_includes_mock_equipment(qtbot):
    source_filter = QComboBox()
    source_filter.addItem("全部", "all")
    source_filter.addItem("模拟", "mock")
    qtbot.addWidget(source_filter)
    cards = [
        ({"_fp": "real"}, "环", "ring", False, False),
        ({"_fp": "mock_one"}, "环", "ring", True, False),
    ]
    tab = SimpleNamespace(
        _source_filter=source_filter,
        _collect_filtered_cards=lambda: (
            cards if source_filter.currentData() == "all" else [cards[1]]),
    )

    assert EquipStatusTab._filtered_delete_fingerprints(tab) == {"real"}

    source_filter.setCurrentIndex(source_filter.findData("mock"))
    assert EquipStatusTab._filtered_delete_fingerprints(tab) == set()


def test_filtered_delete_dialog_defaults_to_protecting_loadout_items(qtbot):
    parent = QPushButton()
    qtbot.addWidget(parent)
    base_size = parent.font().pointSizeF()
    dialog = _FilteredDeleteDialog(
        "类型：背包", {"active", "standby", "free"},
        {"active", "standby"}, parent,
        locked_fingerprints={"standby", "free"},
    )
    qtbot.addWidget(dialog)

    assert dialog.preserve_referenced
    assert dialog.preserve_locked
    assert dialog.effective_delete_count == 0
    assert not dialog._delete_button.isEnabled()
    assert not dialog._reference_warning.isVisible()
    if base_size > 0:
        assert dialog.font().pointSizeF() == base_size + 2

    dialog._preserve_checkbox.setChecked(False)
    assert dialog.effective_delete_count == 1
    assert "2" in dialog._reference_warning.text()

    dialog._preserve_locked_checkbox.setChecked(False)
    assert dialog.effective_delete_count == 3


def test_filtered_delete_dialog_disables_delete_when_everything_is_protected(
    qtbot,
):
    dialog = _FilteredDeleteDialog(
        "类型：背包", {"standby"}, {"standby"})
    qtbot.addWidget(dialog)

    assert dialog.effective_delete_count == 0
    assert not dialog._delete_button.isEnabled()


def test_compact_card_batch_mode_selects_by_click_and_blocks_context(qtbot):
    card = _CompactEquipCard()
    card.set_equip(
        {"_fp": "mock_one", "type": "环", "name": "模拟环"},
        "环", is_mock=True,
    )
    qtbot.addWidget(card)
    selected: list[tuple[str, bool]] = []
    card.selection_changed.connect(
        lambda fp, checked: selected.append((fp, checked)))

    card.set_selection_mode(True)
    assert not card.selection_checkbox.isHidden()
    QTest.mouseClick(card, Qt.MouseButton.LeftButton)

    assert card.selection_checkbox.isChecked()
    assert selected == [("mock_one", True)]


def test_single_item_metadata_updates_cards_without_rebuilding_grid(qtbot):
    container = QWidget()
    grid = QGridLayout(container)
    compact_data = {"_fp": "same", "type": "环", "name": "背包环"}
    slot_data = {"_fp": "same", "type": "环", "name": "穿戴环"}
    compact = _CompactEquipCard()
    compact.set_equip(compact_data, "环", "ring")
    slot = _SlotCard("ring", "环", "ring")
    slot.set_equip(slot_data)
    grid.addWidget(compact, 0, 0)
    qtbot.addWidget(container)
    qtbot.addWidget(slot)
    tab = SimpleNamespace(
        _equipped={"ring": slot_data},
        _bag_items={"ring": {"same": compact_data}},
        _mock_items={},
        _slot_cards={"ring": slot},
        _grid=grid,
    )

    EquipStatusTab._update_item_metadata(
        tab, "same", "lock_status", "locked")

    assert slot_data["lock_status"] == "locked"
    assert compact_data["lock_status"] == "locked"
    assert not slot.lock_badge.isHidden()
    assert not compact.lock_badge.isHidden()
    assert grid.itemAt(0).widget() is compact


def test_slot_card_renders_the_plan_dingyin_instead_of_equipment_default(qtbot):
    """PVP 扫到止戈后，正在查看的 PVE 方案仍必须显示自己的普通定音。"""
    card = _SlotCard("ring", "环", "ring")
    qtbot.addWidget(card)
    card.set_equip({
        "_fp": "same",
        "type": "环",
        "name": "流星环",
        "level": 110,
        "dingyin": {"name": "外功穿透", "value": 14.2},
        "dingyin_zhige": {"name": "止戈定音"},
        "dingyin_type": "zhige",
    }, dingyin_kind="normal")

    texts = {label.text() for label in card.findChildren(QLabel)}
    assert any("外功穿透" in value for value in texts)
    assert not any("<止戈定音>" in value for value in texts)


def test_missing_plan_dingyin_defaults_to_normal():
    plan = SimpleNamespace(dingyin={})
    tab = SimpleNamespace(
        _inv=SimpleNamespace(
            state=SimpleNamespace(
                active_plan_id="pve", plans={"pve": plan})))

    assert EquipStatusTab._plan_dingyin_kind(tab, "ring") == "normal"


def test_slot_properties_switch_calls_the_plan_write_entry(monkeypatch):
    from lvjiang.apps.yysls.ui.loadout.equip import cards

    writes: list[tuple[str, str]] = []
    inventory = SimpleNamespace(
        set_plan_dingyin=lambda slot, kind: writes.append((slot, kind)))
    tab = SimpleNamespace(
        window=lambda: None,
        _require_inventory=lambda: inventory,
        _plan_dingyin_kind=lambda slot_key: "normal",
        _refresh_slots=lambda: None,
        _refresh_dingyin_cards=lambda fp: None,
    )
    captured: dict = {}
    monkeypatch.setattr(
        cards, "_show_equipment_properties",
        lambda parent, equip, **kwargs: captured.update(kwargs))
    equip = {
        "_fp": "ring-fp",
        "dingyin": {"name": "外功穿透", "value": 14.2},
        "dingyin_zhige": {"name": "止戈定音"},
    }

    EquipStatusTab._on_properties_requested(tab, equip, slot_key="ring")
    assert captured["dingyin_changed"]("zhige") is True
    assert writes == [("ring", "zhige")]


def test_bag_properties_switch_never_changes_plan_even_when_item_is_equipped(
    monkeypatch,
):
    """入口上下文是唯一语义：背包卡片不按指纹反查当前方案。"""
    from lvjiang.apps.yysls.ui.loadout.equip import cards

    plan_writes: list[tuple[str, str]] = []
    item_writes: list[tuple[str, str]] = []
    patched: list[tuple[str, str, str]] = []
    inventory = SimpleNamespace(
        set_plan_dingyin=lambda slot, kind: plan_writes.append((slot, kind)),
        set_item_dingyin_type=lambda fp, kind: item_writes.append((fp, kind)),
    )
    tab = SimpleNamespace(
        window=lambda: None,
        _require_inventory=lambda: inventory,
        _plan_dingyin_kind=lambda slot_key: "normal",
        _refresh_slots=lambda: None,
        _refresh_dingyin_cards=lambda fp: None,
        _update_item_metadata=lambda fp, key, value: patched.append(
            (fp, key, value)),
        # 即使同指纹正在装备槽，背包入口也不能写方案。
        _equipped={"ring": {"_fp": "same-fp"}},
    )
    captured: dict = {}
    monkeypatch.setattr(
        cards, "_show_equipment_properties",
        lambda parent, equip, **kwargs: captured.update(kwargs))
    equip = {
        "_fp": "same-fp",
        "dingyin": {"name": "外功穿透", "value": 14.2},
        "dingyin_zhige": {"name": "止戈定音"},
    }

    EquipStatusTab._on_properties_requested(tab, equip)
    assert captured["dingyin_changed"]("zhige") is True
    assert plan_writes == []
    assert item_writes == [("same-fp", "zhige")]
    assert patched == [("same-fp", "dingyin_type", "zhige")]


def test_lock_request_only_updates_one_item_without_full_sync():
    persisted: list[tuple[str, bool]] = []
    patched: list[tuple[str, str, str]] = []
    inventory = SimpleNamespace(
        set_item_lock_status=lambda fp, locked: persisted.append((fp, locked)))
    tab = SimpleNamespace(
        _require_inventory=lambda: inventory,
        _update_item_metadata=lambda fp, key, value: patched.append(
            (fp, key, value)),
        _sync_inv=lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("单字段修改不应全量同步")),
    )

    EquipStatusTab._on_lock_requested(
        tab, {"_fp": "ring-fp", "name": "环"}, False)

    assert persisted == [("ring-fp", False)]
    assert patched == [("ring-fp", "lock_status", "unlock")]


def test_cooldown_change_syncs_kind_and_state_in_memory(monkeypatch):
    """清除冷却要连 kind/state 一起同步到内存副本，否则卡片继续显示「冷却完成」。"""
    from lvjiang.apps.yysls.ui.loadout.equip import cards

    persisted: list[tuple[str, str]] = []
    patched: list[tuple[str, str, str]] = []
    inventory = SimpleNamespace(
        set_item_cooldown=lambda fp, value: persisted.append((fp, value)))
    tab = SimpleNamespace(
        window=lambda: None,
        _require_inventory=lambda: inventory,
        _update_item_metadata=lambda fp, key, value: patched.append(
            (fp, key, value)),
        # 背包里的装备：不在任何方案槽位上，切换定音写装备自身的展示状态。
        _slot_of_equipped=lambda equip_data: "",
        _plan_dingyin_kind=lambda slot_key: "",
    )
    captured: dict = {}
    monkeypatch.setattr(
        cards, "_show_equipment_properties",
        lambda parent, equip, cooldown_changed=None, **kwargs: captured.update(
            callback=cooldown_changed, **kwargs))
    equip = {"_fp": "ring-fp", "cooldown_kind": "reset",
             "cooldown_state": "completed", "cooldown_expires_at": ""}

    EquipStatusTab._on_properties_requested(tab, equip)
    assert captured["callback"]("") is True
    assert persisted == [("ring-fp", "")]
    assert patched == [
        ("ring-fp", "cooldown_expires_at", ""),
        ("ring-fp", "cooldown_kind", ""),
        ("ring-fp", "cooldown_state", ""),
    ]

    patched.clear()
    assert captured["callback"]("2026-09-10T00:00:00+00:00") is True
    assert patched == [
        ("ring-fp", "cooldown_expires_at", "2026-09-10T00:00:00+00:00"),
        ("ring-fp", "cooldown_kind", "reset"),
        ("ring-fp", "cooldown_state", "cooling"),
    ]


def test_source_actions_offer_copy_only_for_mock_type(qtbot):
    source = QComboBox()
    source.addItem("全部", "all")
    source.addItem("模拟", "mock")
    delete_button = QPushButton()
    copy_button = QPushButton()
    for widget in (source, delete_button, copy_button):
        qtbot.addWidget(widget)
    tab = SimpleNamespace(
        _source_filter=source,
        _btn_delete_filtered=delete_button,
        _btn_batch_copy=copy_button,
    )

    EquipStatusTab._update_source_actions(tab)
    assert not delete_button.isHidden()
    assert copy_button.isHidden()

    source.setCurrentIndex(source.findData("mock"))
    EquipStatusTab._update_source_actions(tab)
    assert delete_button.isHidden()
    assert not copy_button.isHidden()


def test_properties_dialog_separates_the_two_disabled_reasons(qtbot):
    """禁用要给对的原因：入口不支持切换 ≠ 装备只有一种定音。

    合成一句会对着两种定音都有的装备说假话，用户会以为是数据问题去重扫。
    """
    from lvjiang.apps.yysls.core.equip_parser.dingyin_parser import (
        ZHIGE_DINGYIN_NAME,
    )
    from lvjiang.apps.yysls.ui.loadout.equip.cards import (
        _EquipmentPropertiesDialog,
    )

    both = {"_fp": "f", "type": "环",
            "dingyin": {"name": "外功穿透", "value": 14.2},
            "dingyin_zhige": {"name": ZHIGE_DINGYIN_NAME}}
    only_one = {"_fp": "f", "type": "环",
                "dingyin": {"name": "外功穿透", "value": 14.2}}

    unsupported = _EquipmentPropertiesDialog(both)
    qtbot.addWidget(unsupported)
    assert not unsupported._switch_dingyin_button.isEnabled()
    assert "入口" in unsupported._switch_dingyin_button.toolTip()

    single = _EquipmentPropertiesDialog(only_one, dingyin_changed=lambda _k: True)
    qtbot.addWidget(single)
    assert not single._switch_dingyin_button.isEnabled()
    assert "一种定音" in single._switch_dingyin_button.toolTip()

    usable = _EquipmentPropertiesDialog(both, dingyin_changed=lambda _k: True)
    qtbot.addWidget(usable)
    assert usable._switch_dingyin_button.isEnabled()
    assert usable._switch_dingyin_button.text() == "切换止戈定音"
    usable._switch_dingyin_button.click()
    assert usable._switch_dingyin_button.text() == "切换普通定音"


def test_dingyin_context_action_only_exists_for_two_slots_and_names_target(
    qtbot,
):
    from lvjiang.apps.yysls.ui.loadout.equip.cards import (
        _add_dingyin_switch_action,
    )

    menu = QMenu()
    qtbot.addWidget(menu)
    selected: list[str] = []
    both = {
        "dingyin": {"name": "外功穿透", "value": 14.2},
        "dingyin_zhige": {"name": "止戈定音"},
    }

    action = _add_dingyin_switch_action(
        menu, both, "normal", selected.append)
    assert action is not None
    assert action.text() == "切换止戈定音"
    action.trigger()
    assert selected == ["zhige"]

    single = QMenu()
    qtbot.addWidget(single)
    assert _add_dingyin_switch_action(
        single, {"dingyin": both["dingyin"]}, "normal", selected.append,
    ) is None
    assert single.actions() == []
