"""调律配置对话框 —— 材料处理页（基础规则组的 materials 段）

状态机行为点「材料处理」（每轮调律开始前的行为）：
- 大律准石数量检查：开关 + 数量基准 + 不足处理（低于基准判材料
  不足，按不足处理执行：跳过该装备 / 结束全部调律 / 询问是否继续）；
- 狗粮添加规则：有序规则表（可自由增删行），每条规则 = 三条件
  （首词条百分比 / 装备期望 / 装备品阶）+ 动作（添加狗粮或不添加）
  + 材料不足时行为（继续走后续规则 / 跳过该装备）。
沿用「变更即校验即保存」模式：控件变更即重建 raw dict → 校验 →
通过才写盘并 reload，失败时状态栏红字提示。
`_build()` 以管理器最新 raw 为底、只替换 materials 段，
与基础规则页各管各段互不覆盖。
页面对准当前选中的基础规则组（set_group 切换后重载）。
"""

from __future__ import annotations

from datetime import datetime
from typing import Callable

from loguru import logger
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from lvjiang.apps.yysls.evaluator.tuning_rules import (
    FOOD_EXPECT_KEYS,
    FOOD_LABELS,
    INSUFFICIENT_LABELS,
    QUALITY_LABELS,
    RATING_LABELS,
    STONE_ACTION_LABELS,
    FoodRule,
    TuningGroup,
    TuningGroupManager,
)

# 狗粮下拉框的「不添加」占位项（对应配置空串）
_NO_FOOD = "- 不添加 -"
# 品阶下拉候选（按品阶从低到高，blue=不限）
_QUALITY_KEYS = ("blue", "purple", "gold")

# 规则表列定义（第一列为序号）
_SEQ_COL = 0
_COLS = ("#", "首词条 ≥ %", "期望 ≥", "品阶 ≥", "每轮添加", "材料不足时")


class MaterialConfigPage(QWidget):
    """材料配置编辑页（只负责当前规则组的 materials 段）"""

    def __init__(self, manager: TuningGroupManager, group_key: str,
                 status_cb: Callable[[str, bool], None], parent=None):
        super().__init__(parent)
        self._manager = manager
        self._group_key = group_key
        self._status_cb = status_cb
        self._loading = True
        self._init_ui()
        self._load()
        self._loading = False

    # ── 规则组切换 ──

    def set_group(self, group_key: str):
        """切换目标基础规则组并重载本页控件"""
        self._group_key = group_key
        self._loading = True
        self._load()
        self._loading = False

    def _current_group(self) -> TuningGroup:
        group = self._manager.get_group(self._group_key)
        if group is None:
            groups = self._manager.get_groups()
            first_key = next(iter(groups), "")
            group = (self._manager.get_group(first_key)
                     or TuningGroup())
        return group

    # ── UI 构建 ──

    def _init_ui(self):
        layout = QVBoxLayout(self)

        # 页面说明 + 大律准石数量检查
        layout.addWidget(QLabel(
            "<b>材料处理</b>（每轮调律开始前的行为点）："
            "律准石数量检查、狗粮检查与添加"))
        layout.addWidget(QLabel(
            "<b>大律准石数量检查</b>（调律前识别材料区数量，"
            "低于基准判材料不足，按不足处理执行）"))
        stone_row = QHBoxLayout()
        self._stone_cb = QCheckBox("启用检查")
        self._stone_cb.stateChanged.connect(lambda _s: self._apply())
        stone_row.addWidget(self._stone_cb)
        stone_row.addWidget(QLabel("数量基准"))
        self._stone_min = QSpinBox()
        self._stone_min.setRange(1, 99999)
        self._stone_min.valueChanged.connect(lambda _v: self._apply())
        stone_row.addWidget(self._stone_min)
        stone_row.addWidget(QLabel("不足时"))
        self._stone_action = QComboBox()
        for key, label in STONE_ACTION_LABELS.items():
            self._stone_action.addItem(label, key)
        self._stone_action.setToolTip(
            "询问是否继续：弹窗确认，继续则本次运行不再检查")
        self._stone_action.currentIndexChanged.connect(
            lambda _i: self._apply())
        stone_row.addWidget(self._stone_action)
        stone_row.addStretch()
        layout.addLayout(stone_row)

        # 狗粮添加规则（标题顶部留半个字高度）
        layout.addSpacing(self.fontMetrics().height() // 2)
        layout.addWidget(QLabel(
            "<b>狗粮添加规则</b>（每轮调律自上而下匹配，首条命中即生效；"
            "全部不命中则不添加）"))
        self._table = QTableWidget(0, len(_COLS))
        self._table.setHorizontalHeaderLabels(list(_COLS))
        self._table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch)
        # 序号列固定宽度，隐藏原生行号
        self._table.horizontalHeader().setSectionResizeMode(
            _SEQ_COL, QHeaderView.ResizeMode.Fixed)
        self._table.setColumnWidth(_SEQ_COL, 32)
        self._table.verticalHeader().setVisible(False)
        layout.addWidget(self._table)

        btn_row = QHBoxLayout()
        add_btn = QPushButton("添加规则")
        add_btn.clicked.connect(self._on_add_rule)
        btn_row.addWidget(add_btn)
        del_btn = QPushButton("删除选中规则")
        del_btn.clicked.connect(self._on_del_rule)
        btn_row.addWidget(del_btn)
        btn_row.addSpacing(8)
        self._up_btn = QPushButton("▲ 上移")
        self._up_btn.setToolTip("将选中规则上移一行")
        self._up_btn.clicked.connect(self._on_move_up)
        self._up_btn.setEnabled(False)
        btn_row.addWidget(self._up_btn)
        self._down_btn = QPushButton("▼ 下移")
        self._down_btn.setToolTip("将选中规则下移一行")
        self._down_btn.clicked.connect(self._on_move_down)
        self._down_btn.setEnabled(False)
        btn_row.addWidget(self._down_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)
        layout.addStretch()

        # 表格选中变化时更新移动按钮状态
        self._table.itemSelectionChanged.connect(self._update_move_buttons)

    def _make_row_widgets(self, rule: FoodRule) -> None:
        """在表尾新增一行并填充该规则的编辑控件"""
        row = self._table.rowCount()
        self._table.insertRow(row)

        # 序号列（只读标签）
        seq_label = QLabel(str(row + 1))
        seq_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._table.setCellWidget(row, _SEQ_COL, seq_label)

        pct = QSpinBox()
        pct.setRange(0, 100)
        pct.setSuffix(" %")
        pct.setToolTip("0 = 不限首词条")
        pct.setValue(rule.pct)
        pct.valueChanged.connect(lambda _v: self._apply())
        self._table.setCellWidget(row, 1, pct)

        expect = QComboBox()
        for key in FOOD_EXPECT_KEYS:
            expect.addItem(RATING_LABELS.get(key, key), key)
        expect.setCurrentIndex(max(expect.findData(rule.min_expect), 0))
        expect.currentIndexChanged.connect(lambda _i: self._apply())
        self._table.setCellWidget(row, 2, expect)

        quality = QComboBox()
        for key in _QUALITY_KEYS:
            quality.addItem(QUALITY_LABELS.get(key, key), key)
        quality.setCurrentIndex(max(quality.findData(rule.min_quality), 0))
        quality.currentIndexChanged.connect(lambda _i: self._apply())
        self._table.setCellWidget(row, 3, quality)

        food = QComboBox()
        food.addItem(_NO_FOOD, "")
        for label in FOOD_LABELS:
            food.addItem(label, label)
        food.setCurrentIndex(max(food.findData(rule.food), 0))
        food.currentIndexChanged.connect(lambda _i: self._apply())
        self._table.setCellWidget(row, 4, food)

        action = QComboBox()
        for key, label in INSUFFICIENT_LABELS.items():
            action.addItem(label, key)
        action.setCurrentIndex(max(action.findData(rule.on_insufficient), 0))
        action.currentIndexChanged.connect(lambda _i: self._apply())
        self._table.setCellWidget(row, 5, action)

    # ── 回填 ──

    def _load(self):
        # 用管理器已解析的 MaterialSettings 回填（缺省段/字段已在
        # 解析层落到默认值，无需重复处理）
        m = self._current_group().materials
        self._stone_cb.setChecked(m.stone_check_enabled)
        self._stone_min.setValue(m.stone_min_count)
        self._stone_action.setCurrentIndex(max(
            self._stone_action.findData(m.stone_insufficient_action), 0))
        self._table.setRowCount(0)
        for rule in m.food_rules:
            self._make_row_widgets(rule)

    # ── 行增删 ──

    def _on_add_rule(self):
        self._loading = True
        self._make_row_widgets(FoodRule())
        self._loading = False
        self._update_move_buttons()
        self._apply()

    def _on_del_rule(self):
        row = self._table.currentRow()
        if row < 0:
            self._status_cb("请先选中要删除的规则行", True)
            return
        self._table.removeRow(row)
        self._refresh_seq_numbers()
        self._update_move_buttons()
        self._apply()

    def _on_move_up(self):
        row = self._table.currentRow()
        if row <= 0:
            self._status_cb("已是第一条规则，无法上移", True)
            return
        self._swap_rows(row, row - 1)
        self._table.selectRow(row - 1)
        self._refresh_seq_numbers()
        self._apply()

    def _on_move_down(self):
        row = self._table.currentRow()
        if row < 0 or row >= self._table.rowCount() - 1:
            self._status_cb("已是最后一条规则，无法下移", True)
            return
        self._swap_rows(row, row + 1)
        self._table.selectRow(row + 1)
        self._refresh_seq_numbers()
        self._apply()

    def _refresh_seq_numbers(self) -> None:
        """刷新序号列，保持与行序一致"""
        for r in range(self._table.rowCount()):
            w = self._table.cellWidget(r, _SEQ_COL)
            if isinstance(w, QLabel):
                w.setText(str(r + 1))

    def _update_move_buttons(self) -> None:
        """根据当前选中行更新移动按钮的启用状态"""
        row = self._table.currentRow()
        has_selection = row >= 0
        # 上移：有选中且不是第一行
        self._up_btn.setEnabled(has_selection and row > 0)
        # 下移：有选中且不是最后一行
        self._down_btn.setEnabled(has_selection and row < self._table.rowCount() - 1)

    def _swap_rows(self, row_a: int, row_b: int) -> None:
        """交换两行的规则数据（含所有控件值）"""
        values_a = self._row_rule(row_a)
        values_b = self._row_rule(row_b)
        self._set_row_values(row_a, values_b)
        self._set_row_values(row_b, values_a)

    def _set_row_values(self, row: int, values: dict) -> None:
        """将 raw dict 写回指定行的控件"""
        pct: QSpinBox = self._table.cellWidget(row, 1)
        pct.setValue(values["pct"])
        expect: QComboBox = self._table.cellWidget(row, 2)
        expect.setCurrentIndex(max(expect.findData(values["min_expect"]), 0))
        quality: QComboBox = self._table.cellWidget(row, 3)
        quality.setCurrentIndex(max(quality.findData(values["min_quality"]), 0))
        food: QComboBox = self._table.cellWidget(row, 4)
        food.setCurrentIndex(max(food.findData(values["food"]), 0))
        action: QComboBox = self._table.cellWidget(row, 5)
        action.setCurrentIndex(max(action.findData(values["on_insufficient"]), 0))

    # ── 收集 → 校验 → 写盘 → reload ──

    def _row_rule(self, row: int) -> dict:
        """收集一行的规则控件值为 raw dict"""
        pct: QSpinBox = self._table.cellWidget(row, 1)
        expect: QComboBox = self._table.cellWidget(row, 2)
        quality: QComboBox = self._table.cellWidget(row, 3)
        food: QComboBox = self._table.cellWidget(row, 4)
        action: QComboBox = self._table.cellWidget(row, 5)
        return {
            "pct": pct.value(),
            "min_expect": expect.currentData(),
            "min_quality": quality.currentData(),
            "food": food.currentData(),
            "on_insufficient": action.currentData(),
        }

    def _build(self) -> dict:
        # 以最新 raw 为底只替换 materials 段，保留基础规则页负责的
        # min_level / behavior 等其他段
        data = self._manager.get_raw(self._group_key)
        data["materials"] = {
            "stone_check": {
                "enabled": self._stone_cb.isChecked(),
                "min_count": self._stone_min.value(),
                "insufficient_action": self._stone_action.currentData(),
            },
            "food_rules": [
                self._row_rule(row) for row in range(self._table.rowCount())
            ],
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
            self._manager.save_group(self._group_key, data)
        except Exception as e:  # noqa: BLE001
            logger.exception("材料配置保存失败")
            self._status_cb(f"保存失败：{e}", True)
            return
        now = datetime.now().strftime("%H:%M:%S")
        self._status_cb(f"已保存并生效（{now}）", False)
