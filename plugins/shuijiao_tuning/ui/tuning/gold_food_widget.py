"""按等阶用金的桌面配置。2026-10-31 新增。"""
from __future__ import annotations

from lvjiang.apps.shuijiao_tuning.core.gold_food import GoldFoodPolicy
from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QDoubleSpinBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QSpinBox,
    QVBoxLayout,
)


class GoldFoodWidget(QGroupBox):
    config_changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__("狗粮库存与高首条保护", parent)
        layout = QVBoxLayout(self)
        self.enabled_cb = QCheckBox("启用等级策略（替代基础规则组的狗粮选择）")
        layout.addWidget(self.enabled_cb)
        row = QHBoxLayout()
        row.addWidget(QLabel("110 / 115 阶首条至少"))
        self.pct_spin = QDoubleSpinBox()
        self.pct_spin.setRange(0, 100)
        self.pct_spin.setDecimals(1)
        self.pct_spin.setSuffix("% 才用金")
        self.pct_spin.setValue(85)
        row.addWidget(self.pct_spin)
        row.addStretch()
        layout.addLayout(row)
        row = QHBoxLayout()
        row.addWidget(QLabel("金狗粮少于"))
        self.stock_spin = QSpinBox()
        self.stock_spin.setRange(0, 999999)
        self.stock_spin.setValue(10)
        self.stock_spin.setSuffix(" 份时自动停用金狗粮")
        row.addWidget(self.stock_spin)
        row.addStretch()
        layout.addLayout(row)
        note = QLabel("105阶忽略初始数值；110/115阶沿用首条比例和转律潜力。低库存或库存未识别时不投金，继续无金调律，不弹窗。")
        note.setWordWrap(True)
        layout.addWidget(note)
        self.reserve_cb = QCheckBox("预留115级武库坯子（仅保留原件，不自动入库）")
        layout.addWidget(self.reserve_cb)
        row = QHBoxLayout()
        row.addWidget(QLabel("首条高于满值的"))
        self.reserve_pct = QDoubleSpinBox()
        self.reserve_pct.setRange(0, 100)
        self.reserve_pct.setSuffix("% 时预留")
        self.reserve_pct.setValue(95)
        row.addWidget(self.reserve_pct)
        row.addStretch()
        layout.addLayout(row)
        from lvjiang.apps.shuijiao_tuning.core.build_profiles import PART_NAMES, PARTS
        self.reserve_parts = {}
        row = QHBoxLayout()
        for key, label in zip(PARTS, PART_NAMES, strict=True):
            check = QCheckBox(label)
            self.reserve_parts[key] = check
            row.addWidget(check)
            if key in ("main_weapon", "sub_weapon"):
                check.setToolTip("背包不区分主副武器，任一武器勾选会预留所有武器类型。")
            check.toggled.connect(self._changed)
        layout.addLayout(row)
        self.reserve_cb.toggled.connect(self._changed)
        self.reserve_pct.valueChanged.connect(self._changed)
        self.refund_wait = QDoubleSpinBox()
        self.refund_wait.setRange(0.5, 10)
        self.refund_wait.setValue(2)
        self.refund_wait.setSuffix(" 秒")
        row = QHBoxLayout()
        row.addWidget(QLabel("返还动画最短等待（仍须提示稳定才右键）"))
        row.addWidget(self.refund_wait)
        layout.addLayout(row)
        self.refund_wait.valueChanged.connect(self._changed)
        self.color_cb = QCheckBox("优先使用彩狗粮")
        self.color_pct = QDoubleSpinBox()
        self.color_pct.setRange(0, 100)
        self.color_pct.setValue(98)
        self.color_pct.setSuffix("% 首条以上（严格高于）")
        self.color_stock = QSpinBox()
        self.color_stock.setRange(0, 999999)
        row = QHBoxLayout()
        row.addWidget(self.color_cb)
        row.addWidget(self.color_pct)
        row.addWidget(QLabel("彩狗粮少于"))
        row.addWidget(self.color_stock)
        row.addWidget(QLabel("份时停用"))
        layout.addLayout(row)
        self.keep_cb = QCheckBox("分解时保护本次目标流派的高首条装备")
        self.keep_pct = QDoubleSpinBox()
        self.keep_pct.setRange(0, 100)
        self.keep_pct.setValue(95)
        self.keep_pct.setSuffix("% 首条以上（严格高于）")
        row = QHBoxLayout()
        row.addWidget(self.keep_cb)
        row.addWidget(self.keep_pct)
        layout.addLayout(row)
        for control in (self.color_cb, self.keep_cb):
            control.toggled.connect(self._changed)
        for control in (self.color_pct, self.color_stock, self.keep_pct):
            control.valueChanged.connect(self._changed)
        self.enabled_cb.toggled.connect(self._changed)
        self.pct_spin.valueChanged.connect(self._changed)
        self.stock_spin.valueChanged.connect(self._changed)
        self._update_enabled()

    def _update_enabled(self):
        self.pct_spin.setEnabled(self.enabled_cb.isChecked())
        self.stock_spin.setEnabled(True)

    def _changed(self, *_args):
        self._update_enabled()
        self.config_changed.emit()

    def get_config(self) -> dict:
        return {"enabled": self.enabled_cb.isChecked(), "min_initial_pct": self.pct_spin.value(),
                "low_stock": self.stock_spin.value(), "reserve_enabled": self.reserve_cb.isChecked(),
                "reserve_pct": self.reserve_pct.value(),
                "reserve_parts": [k for k, c in self.reserve_parts.items() if c.isChecked()],
                "color_enabled": self.color_cb.isChecked(), "color_pct": self.color_pct.value(),
                "color_low_stock": self.color_stock.value(), "keep_enabled": self.keep_cb.isChecked(), "keep_pct": self.keep_pct.value(), "refund_wait": self.refund_wait.value()}

    def set_config(self, raw: dict | None):
        policy = GoldFoodPolicy.from_config(raw)
        controls = (self.enabled_cb, self.pct_spin, self.stock_spin, self.reserve_cb, self.reserve_pct, *self.reserve_parts.values(), self.color_cb, self.color_pct, self.color_stock, self.keep_cb, self.keep_pct, self.refund_wait)
        for control in controls:
            control.blockSignals(True)
        self.enabled_cb.setChecked(policy.enabled)
        self.pct_spin.setValue(policy.min_initial_pct)
        self.stock_spin.setValue(policy.low_stock)
        self.color_cb.setChecked(policy.color_enabled)
        self.color_pct.setValue(policy.color_pct)
        self.color_stock.setValue(policy.color_low_stock)
        self.refund_wait.setValue(policy.refund_wait)
        self.keep_cb.setChecked(policy.keep_enabled)
        self.keep_pct.setValue(policy.keep_pct)
        self.reserve_cb.setChecked(policy.reserve_enabled)
        self.reserve_pct.setValue(policy.reserve_pct)
        for key, check in self.reserve_parts.items():
            check.setChecked(key in policy.reserve_parts)
        for control in controls:
            control.blockSignals(False)
        self._update_enabled()
