"""可自由输入、按功能两级选择的 press 名称控件。"""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QComboBox, QHBoxLayout, QLineEdit, QWidget

from ..core.key_names import normalize_pressable
from ..i18n import tr

_PUNCTUATION_LABELS = {
    "GRAVE": "GRAVE(`)",
    "MINUS": "MINUS(-)",
    "EQUALS": "EQUALS(=)",
    "LBRACKET": "LBRACKET([)",
    "RBRACKET": "RBRACKET(])",
    "BACKSLASH": "BACKSLASH(\\)",
    "SEMICOLON": "SEMICOLON(;)",
    "APOSTROPHE": "APOSTROPHE(')",
    "COMMA": "COMMA(,)",
    "PERIOD": "PERIOD(.)",
    "SLASH": "SLASH(/)",
}


PRESSABLE_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("常用控制与导航", (
        "ESC", "ENTER", "SPACE", "TAB", "BACKSPACE", "DELETE", "INSERT",
        "HOME", "END", "PAGEUP", "PAGEDOWN", "UP", "DOWN", "LEFT", "RIGHT",
    )),
    ("字母键", tuple(chr(code) for code in range(ord("A"), ord("Z") + 1))),
    ("数字键", tuple(str(number) for number in range(10))),
    ("功能键", tuple(f"F{number}" for number in range(1, 13))),
    ("修饰键", (
        "SHIFT", "CTRL", "ALT", "WIN", "LSHIFT", "RSHIFT", "LCTRL", "RCTRL",
        "LALT", "RALT", "LWIN", "RWIN",
    )),
    ("标点键", (
        "GRAVE", "MINUS", "EQUALS", "LBRACKET", "RBRACKET", "BACKSLASH",
        "SEMICOLON", "APOSTROPHE", "COMMA", "PERIOD", "SLASH", "OEM102",
    )),
    ("小键盘", (
        *(f"NUMPAD{number}" for number in range(10)),
        "NUMPAD_MULTIPLY", "NUMPAD_ADD", "NUMPAD_SUBTRACT", "NUMPAD_DECIMAL",
        "NUMPAD_DIVIDE", "NUMPAD_ENTER",
    )),
    ("系统与锁定键", (
        "CAPSLOCK", "NUMLOCK", "SCROLLLOCK", "PRINTSCREEN", "PAUSE",
    )),
    ("鼠标按钮", (
        "MOUSE_LEFT", "MOUSE_RIGHT", "MOUSE_MIDDLE", "MOUSE_X1", "MOUSE_X2",
    )),
)


class PressableSelector(QWidget):
    """左侧选择功能组，右侧选择或自由输入标准 press 名。"""

    currentTextChanged = pyqtSignal(str)

    def __init__(self, value: str = "", parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self.group_combo = QComboBox()
        self.group_combo.addItems([tr(name) for name, _keys in PRESSABLE_GROUPS])
        self.group_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToContents)
        layout.addWidget(self.group_combo)

        self.key_combo = QComboBox()
        self.key_combo.setEditable(True)
        self.key_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.key_combo.setMaxVisibleItems(24)
        layout.addWidget(self.key_combo, 1)

        normalized = self._normalize_if_known(value)
        group_index = self._group_index_for(normalized)
        self.group_combo.setCurrentIndex(group_index)
        self._populate_keys(group_index)
        self.key_combo.setCurrentIndex(-1)
        self.key_combo.setEditText(normalized)

        self.group_combo.currentIndexChanged.connect(self._change_group)
        self.key_combo.activated.connect(self._apply_selected_name)
        self.key_combo.currentTextChanged.connect(self.currentTextChanged.emit)

        completer = self.key_combo.completer()
        if completer is not None:
            completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
            completer.setFilterMode(Qt.MatchFlag.MatchContains)

    @staticmethod
    def _normalize_if_known(value: str) -> str:
        if not value:
            return ""
        try:
            return normalize_pressable(value)
        except ValueError:
            return value

    @staticmethod
    def _group_index_for(value: str) -> int:
        for index, (_name, keys) in enumerate(PRESSABLE_GROUPS):
            if value in keys:
                return index
        return 0

    def _populate_keys(self, group_index: int) -> None:
        self.key_combo.clear()
        self.key_combo.addItem(tr("不绑定（使用坐标点击）"), "")
        if not 0 <= group_index < len(PRESSABLE_GROUPS):
            return
        for name in PRESSABLE_GROUPS[group_index][1]:
            self.key_combo.addItem(_PUNCTUATION_LABELS.get(name, name), name)

    def _change_group(self, group_index: int) -> None:
        self._populate_keys(group_index)
        self.key_combo.setCurrentIndex(-1)
        self.key_combo.clearEditText()

    def _apply_selected_name(self, index: int) -> None:
        value = self.key_combo.itemData(index, Qt.ItemDataRole.UserRole)
        if isinstance(value, str):
            self.key_combo.setEditText(value)

    def currentText(self) -> str:
        return self.key_combo.currentText()

    def lineEdit(self) -> QLineEdit | None:
        return self.key_combo.lineEdit()

    def isEditable(self) -> bool:
        return self.key_combo.isEditable()
