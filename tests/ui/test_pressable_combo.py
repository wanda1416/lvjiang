import pytest
from PyQt6.QtCore import Qt

from lvjiang.core.key_names import KNOWN_PRESS_NAMES
from lvjiang.ui.pressable_combo import PRESSABLE_GROUPS, PressableSelector

pytestmark = pytest.mark.usefixtures("qapp")


def test_pressable_groups_are_nine_and_split_letters_from_digits():
    assert len(PRESSABLE_GROUPS) == 9
    groups = dict(PRESSABLE_GROUPS)
    assert groups["字母键"] == tuple("ABCDEFGHIJKLMNOPQRSTUVWXYZ")
    assert groups["数字键"] == tuple("0123456789")
    listed = [name for _group, names in PRESSABLE_GROUPS for name in names]
    assert len(listed) == len(set(listed))
    assert set(listed) == KNOWN_PRESS_NAMES


def test_selector_only_lists_keys_from_selected_group():
    selector = PressableSelector()
    selector.group_combo.setCurrentIndex(5)

    choices = {
        selector.key_combo.itemData(index, Qt.ItemDataRole.UserRole)
        for index in range(selector.key_combo.count())
    }
    assert "GRAVE" in choices
    assert "MOUSE_LEFT" not in choices


def test_initial_value_selects_its_group():
    selector = PressableSelector("MOUSE_LEFT")

    assert selector.group_combo.currentText() == "鼠标按钮"
    assert selector.currentText() == "MOUSE_LEFT"


def test_punctuation_uses_keycap_label_but_selects_canonical_name():
    selector = PressableSelector("GRAVE")
    index = selector.key_combo.findText("GRAVE(`)")
    assert index >= 0
    assert selector.key_combo.itemData(
        index, Qt.ItemDataRole.UserRole) == "GRAVE"

    selector.key_combo.activated.emit(index)

    assert selector.currentText() == "GRAVE"


def test_selector_keeps_freely_entered_value():
    selector = PressableSelector("custom_key")

    assert selector.isEditable()
    assert selector.currentText() == "custom_key"
