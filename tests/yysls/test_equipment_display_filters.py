"""装备展示新增品阶、调律进度和备战状态筛选。"""

from types import SimpleNamespace

from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import (
    QComboBox,
    QGridLayout,
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
    )
    captured: dict = {}
    monkeypatch.setattr(
        cards, "_show_equipment_properties",
        lambda parent, equip, cooldown_changed=None: captured.update(
            callback=cooldown_changed))
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
