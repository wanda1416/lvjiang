"""装备展示设置面板

控制「其他装备」Tab 的卡片外观：字号、卡片高度、网格列数。
数据存于 session.json → settings.equip_display。
"""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
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

    def __init__(self, parent=None):
        super().__init__(parent)
        self._spinboxes: dict[str, QSpinBox] = {}
        self._init_ui()
        self._load()

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
            spin.setFixedWidth(100)
            self._spinboxes[key] = spin
            form.addRow(label + ":", spin)

        layout.addWidget(box)

        # 保存按钮
        btn_row = QHBoxLayout()
        btn_save = QPushButton(tr("保存"))
        btn_save.setFixedWidth(80)
        btn_save.setStyleSheet(
            "QPushButton { background-color: #4CAF50; color: white; "
            "font-weight: bold; padding: 6px; border-radius: 3px; }"
            "QPushButton:hover { background-color: #45a049; }"
        )
        btn_save.clicked.connect(self._save)
        btn_row.addWidget(btn_save)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        layout.addStretch()

    def _load(self):
        from ...config.equip_display import load_equip_display
        params = load_equip_display()
        for key, spin in self._spinboxes.items():
            spin.setValue(int(params.get(key, spin.minimum())))

    def _save(self):
        from ...config.equip_display import save_equip_display
        params = {key: spin.value() for key, spin in self._spinboxes.items()}
        try:
            save_equip_display(params)
            QMessageBox.information(self, tr("保存成功"), tr("装备展示参数已保存"))
        except Exception as e:
            QMessageBox.warning(self, tr("保存失败"), str(e))
