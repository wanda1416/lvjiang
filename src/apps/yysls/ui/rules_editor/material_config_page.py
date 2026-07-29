"""材料配置页（全局 tuning_base.yaml 的 materials 段）

编辑材料设置（materials）：
- 大律准石数量检查：开关 + 数量基准（低于基准判材料不足，全部退出）；
- 狗粮添加规则：首词条百分比阈值 + 达标狗粮 + 品阶→狗粮映射。
沿用「变更即校验即保存」模式：控件变更即重建 raw dict → 校验 →
通过才写盘并 reload，失败时状态栏红字提示。
`_build()` 以管理器最新 raw 为底、只替换 materials 段，
与基础配置页各管各段互不覆盖。
"""

from __future__ import annotations

from datetime import datetime
from typing import Callable

from loguru import logger
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QHBoxLayout, QLabel, QSpinBox, QVBoxLayout,
    QWidget,
)

from src.apps.yysls.evaluator.tuning_rules import (
    FOOD_LABELS, FOOD_QUALITIES, TuningBaseManager,
)

# 狗粮下拉框的「不添加」占位项（对应配置空串）
_NO_FOOD = "- 不添加 -"


class MaterialConfigPage(QWidget):
    """全局材料配置编辑页（只负责 materials 段）"""

    def __init__(self, manager: TuningBaseManager,
                 status_cb: Callable[[str, bool], None], parent=None):
        super().__init__(parent)
        self._manager = manager
        self._status_cb = status_cb
        self._loading = True
        self._init_ui()
        self._load()
        self._loading = False

    # ── UI 构建 ──

    def _init_ui(self):
        layout = QVBoxLayout(self)

        # 大律准石数量检查
        layout.addWidget(QLabel(
            "<b>大律准石数量检查</b>（调律前识别材料区数量，"
            "低于基准判材料不足，全部退出）"))
        stone_row = QHBoxLayout()
        self._stone_cb = QCheckBox("启用检查")
        self._stone_cb.stateChanged.connect(lambda _s: self._apply())
        stone_row.addWidget(self._stone_cb)
        stone_row.addWidget(QLabel("数量基准"))
        self._stone_min = QSpinBox()
        self._stone_min.setRange(1, 99999)
        self._stone_min.valueChanged.connect(lambda _v: self._apply())
        stone_row.addWidget(self._stone_min)
        stone_row.addStretch()
        layout.addLayout(stone_row)

        # 狗粮添加规则
        layout.addWidget(QLabel(
            "<b>狗粮添加规则</b>（每轮调律按首词条百分比与装备品阶"
            "决定添加的狗粮）"))
        food_row = QHBoxLayout()
        food_row.addWidget(QLabel("首词条 ≥"))
        self._pct_spin = QSpinBox()
        self._pct_spin.setRange(0, 100)
        self._pct_spin.setSuffix(" %")
        self._pct_spin.valueChanged.connect(lambda _v: self._apply())
        food_row.addWidget(self._pct_spin)
        food_row.addWidget(QLabel("→ 每轮添加"))
        self._high_food = QComboBox()
        self._high_food.addItems(list(FOOD_LABELS))
        self._high_food.currentIndexChanged.connect(lambda _i: self._apply())
        food_row.addWidget(self._high_food)
        food_row.addStretch()
        layout.addLayout(food_row)

        quality_row = QHBoxLayout()
        quality_row.addWidget(QLabel("未达标时："))
        self._q_food: dict[str, QComboBox] = {}
        for q_key, q_label in FOOD_QUALITIES.items():
            quality_row.addWidget(QLabel(f"{q_label}品阶 →"))
            combo = QComboBox()
            combo.addItem(_NO_FOOD)
            combo.addItems(list(FOOD_LABELS))
            combo.currentIndexChanged.connect(lambda _i: self._apply())
            self._q_food[q_key] = combo
            quality_row.addWidget(combo)
        quality_row.addStretch()
        layout.addLayout(quality_row)
        layout.addStretch()

    # ── 回填 ──

    def _load(self):
        # 用管理器已解析的 MaterialSettings 回填（缺省段/字段已在
        # 解析层落到默认值，无需重复处理）
        m = self._manager.get().materials
        self._stone_cb.setChecked(m.stone_check_enabled)
        self._stone_min.setValue(m.stone_min_count)
        self._pct_spin.setValue(m.high_pct)
        self._high_food.setCurrentText(m.high_pct_food)
        for q_key, combo in self._q_food.items():
            food = m.quality_food.get(q_key, "")
            combo.setCurrentText(food or _NO_FOOD)

    # ── 收集 → 校验 → 写盘 → reload ──

    def _build(self) -> dict:
        # 以最新 raw 为底只替换 materials 段，保留基础配置页负责的
        # quality_thresholds / switches 等其他段
        data = self._manager.get_raw()
        data["materials"] = {
            "stone_check": {
                "enabled": self._stone_cb.isChecked(),
                "min_count": self._stone_min.value(),
            },
            "food_strategy": {
                "high_pct": self._pct_spin.value(),
                "high_pct_food": self._high_food.currentText(),
                "quality_food": {
                    q_key: ("" if combo.currentText() == _NO_FOOD
                            else combo.currentText())
                    for q_key, combo in self._q_food.items()
                },
            },
        }
        return data

    def _apply(self):
        if self._loading:
            return
        data = self._build()
        err = self._manager.validate(data)
        if err:
            self._status_cb(f"校验失败（未保存）：{err}", True)
            return
        try:
            self._manager.save(data)
        except Exception as e:  # noqa: BLE001
            logger.exception("材料配置保存失败")
            self._status_cb(f"保存失败：{e}", True)
            return
        now = datetime.now().strftime("%H:%M:%S")
        self._status_cb(f"已保存并生效（{now}）", False)
