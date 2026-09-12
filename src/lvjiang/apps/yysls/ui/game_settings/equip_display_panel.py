"""装备展示设置面板

控制「其他装备」Tab 的卡片外观：字号、卡片高度、网格列数。
数据存于 session.json → settings.equip_display。
"""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QFormLayout,
    QGroupBox,
    QLabel,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from .....i18n import tr

# 参数定义：(key, label, min, max, suffix)
_PARAM_DEFS = [
    ("name_font_size", tr("装备名字号"), 10, 20, "px"),
    ("level_font_size", tr("等级字号"), 9, 18, "px"),
    ("affix_font_size", tr("词条字号"), 8, 16, "px"),
    ("card_min_height", tr("卡片高度"), 80, 300, "px"),
    ("grid_columns", tr("网格列数"), 2, 8, ""),
]


class EquipDisplayPanel(QWidget):
    """装备展示参数设置面板"""

    def __init__(self, parent=None, *, on_changed=None):
        super().__init__(parent)
        self._on_changed = on_changed
        self._loading = True
        self._spinboxes: dict[str, QSpinBox] = {}
        self._init_ui()
        self._load()
        self._loading = False

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        layout.addWidget(QLabel(
            "<b>" + tr("装备展示设置") + "</b>\n"
            + tr("调整「其他装备」Tab 中卡片的外观参数，保存后刷新即生效。")
        ))

        # 参数组
        box = QGroupBox(tr("卡片外观"))
        form = QFormLayout(box)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        for key, label, lo, hi, suffix in _PARAM_DEFS:
            spin = QSpinBox()
            spin.setRange(lo, hi)
            spin.setSuffix(suffix)
            spin.setMinimumWidth(140)
            self._spinboxes[key] = spin
            spin.valueChanged.connect(self._changed)
            form.addRow(label + ":", spin)

        layout.addWidget(box)

        layout.addStretch()

    def _load(self):
        from ...config.equip_display import load_equip_display
        params = load_equip_display()
        for key, spin in self._spinboxes.items():
            spin.setValue(int(params.get(key, spin.minimum())))

    def _changed(self, _value: int) -> None:
        if not self._loading and self._on_changed is not None:
            self._on_changed()

    def save(self) -> None:
        from ...config.equip_display import save_equip_display
        params = {key: spin.value() for key, spin in self._spinboxes.items()}
        save_equip_display(params)
