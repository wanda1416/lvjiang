"""调律配置对话框 —— 行为处理页（扫描处理 / 结束处理）

状态机行为点配置（behavior 段），每页一张有序条件规则表
（自上而下首条命中即生效）：
- 扫描处理（ScanBehaviorPage，behavior.scan）：进调律前，
  传入规则预期评级 ≥ 进入门槛即进入调律；未达门槛的装备按
  处置表决定回收/保留（无命中 = 保留）；
- 结束处理（TuneBehaviorPage，behavior.tune）：每轮调律结束后
  按预期评级决策 继续调律/重置调律/回收/结束保留（词条满为
  边界条件：继续调律不可达，无命中默认结束保留；未满默认
  继续调律）；另有单件重置次数上限 + 次数用尽转处置动作。
两页均有「判定规则语义」（预期评级识别用哪个流派规则集）：
传入规则（运行期勾选）/ 全部规则 / 自选规则（附规则多选）。
每条规则 = 部位多选（至少勾一项，全选展示 - 全部 -）
+ 品阶/首词条初始数值/判定评级三个有序 ≤ 门槛（单选，
取最高档 = 不限）+ 动作（候选按行为点白名单锁定）。
沿用「变更即校验即保存」模式：控件变更即重建 raw dict → 校验 →
通过才写盘并 reload，失败时状态栏红字提示。
`_build()` 以管理器最新 raw 为底、各自只替换 behavior.scan /
behavior.tune 子段，与基础/材料配置页各管各段互不覆盖。
"""

from __future__ import annotations

from datetime import datetime
from typing import Callable

from loguru import logger
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMenu,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from lvjiang.apps.yysls.evaluator import get_rule_names
from lvjiang.apps.yysls.evaluator.tuning_rules import (
    BEHAVIOR_ACTION_LABELS,
    BEHAVIOR_STAGE_ACTIONS,
    JUDGE_SCOPE_LABELS,
    JUDGE_SCOPES,
    MAX_TUNE_RESETS,
    QUALITY_LABELS,
    QUALITY_PARTS,
    RATING_KEYS,
    RATING_LABELS,
    BehaviorRule,
    TuningBaseManager,
)

# 品阶 ≤ 门槛候选（从高到低，最高档 = 不限）
_QUALITY_KEYS = ("gold", "purple", "blue")

# 规则表列定义
_COLS = ("部位", "品阶 ≤", "首词条 ≤ %", "评级 ≤", "动作")

# tune 行为点的 ignore 语义是「结束保留」，动作文案按行为点区分
_STAGE_ACTION_LABELS = {
    "scan": BEHAVIOR_ACTION_LABELS,
    "tune": {**BEHAVIOR_ACTION_LABELS, "ignore": "结束保留"},
}


class _CheckMenu(QMenu):
    """勾选项点击后不收起的菜单，支持一次展开连续勾选多项"""

    def mouseReleaseEvent(self, e):
        act = self.actionAt(e.pos())
        if act and act.isCheckable():
            act.setChecked(not act.isChecked())
            e.accept()
            return
        super().mouseReleaseEvent(e)


class _MultiSelect(QPushButton):
    """按钮式多选下拉：弹出可勾选菜单（勾选不收起，可连选）。
    至少保留一项（取消最后一项自动回弹），避免「空选 = 不限」
    与全选语义重叠的歧义；全选时展示 - 全部 -"""

    def __init__(self, items: list[tuple[str, str]],
                 changed: Callable[[], None], parent=None):
        super().__init__(parent)
        self._changed = changed
        self._menu = _CheckMenu(self)
        self._actions: dict[str, QAction] = {}
        for key, label in items:
            act = QAction(label, self._menu)
            act.setCheckable(True)
            act.toggled.connect(self._on_toggled)
            self._menu.addAction(act)
            self._actions[key] = act
        self.setMenu(self._menu)
        self._refresh_text()

    def selected(self) -> list[str]:
        return [k for k, a in self._actions.items() if a.isChecked()]

    def set_selected(self, keys: list[str]):
        # 空入参（历史配置的「不限」）归一化为全选，语义等价
        checked = set(keys) if keys else set(self._actions)
        for k, a in self._actions.items():
            a.blockSignals(True)
            a.setChecked(k in checked)
            a.blockSignals(False)
        self._refresh_text()

    def _on_toggled(self, checked: bool):
        if not checked and not self.selected():
            # 至少保留一项：取消最后一项时回弹，不触发保存
            act = self.sender()
            act.blockSignals(True)
            act.setChecked(True)
            act.blockSignals(False)
            return
        self._refresh_text()
        self._changed()

    def _refresh_text(self):
        labels = [a.text() for a in self._actions.values() if a.isChecked()]
        if len(labels) == len(self._actions):
            self.setText("- 全部 -")
        else:
            self.setText("/".join(labels))


class _JudgeScopeSelect(QWidget):
    """「判定规则语义」组合控件：三选一下拉 + 自选时的规则多选

    scope=custom 时显示流派规则 _MultiSelect，其余隐藏；
    rules() 仅 custom 时返回勾选 key（其余返回空列表，与解析层
    「judge_rules 仅 custom 可声明」的约束对齐）。
    """

    def __init__(self, changed: Callable[[], None], parent=None):
        super().__init__(parent)
        self._changed = changed
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        self._combo = QComboBox()
        for scope in JUDGE_SCOPES:
            self._combo.addItem(JUDGE_SCOPE_LABELS.get(scope, scope), scope)
        self._combo.setToolTip(
            "预期评级识别用哪个流派规则集：传入规则=运行期勾选的规则；"
            "全部规则；自选规则=右侧多选声明")
        self._combo.currentIndexChanged.connect(self._on_scope_changed)
        row.addWidget(self._combo)
        self._rules = _MultiSelect(
            [(k, name) for k, name in get_rule_names().items()], changed)
        row.addWidget(self._rules)
        self._sync_visible()

    def scope(self) -> str:
        return self._combo.currentData()

    def rules(self) -> list[str]:
        return self._rules.selected() if self.scope() == "custom" else []

    def set_value(self, scope: str, keys: list[str]):
        self._combo.blockSignals(True)
        self._combo.setCurrentIndex(max(self._combo.findData(scope), 0))
        self._combo.blockSignals(False)
        self._rules.set_selected(keys)
        self._sync_visible()

    def _on_scope_changed(self, _i):
        self._sync_visible()
        self._changed()

    def _sync_visible(self):
        self._rules.setVisible(self.scope() == "custom")


class _BehaviorPageBase(QWidget):
    """行为处理页共用骨架：滚动容器 + 有序规则表 + 变更即保存

    子类声明 STAGE（动作白名单/文案按此取），实现 _init_head()
    （表格上方的启用/门槛/判定语义等头部控件）、_stage_raw()
    （收集本行为点子段 raw dict）与 _load_stage()（回填）。
    """

    STAGE = ""

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
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        outer.addWidget(scroll)
        content = QWidget()
        scroll.setWidget(content)
        layout = QVBoxLayout(content)

        self._init_head(layout)

        self._table = QTableWidget(0, len(_COLS))
        self._table.setHorizontalHeaderLabels(list(_COLS))
        self._table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch)
        self._table.verticalHeader().setVisible(True)
        self._table.setMinimumHeight(140)
        layout.addWidget(self._table)

        btn_row = QHBoxLayout()
        add_btn = QPushButton("添加规则")
        add_btn.clicked.connect(lambda _c: self._on_add_rule())
        btn_row.addWidget(add_btn)
        del_btn = QPushButton("删除选中规则")
        del_btn.clicked.connect(lambda _c: self._on_del_rule())
        btn_row.addWidget(del_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)
        layout.addStretch()

    def _init_head(self, layout: QVBoxLayout):
        raise NotImplementedError

    def _make_row_widgets(self, rule: BehaviorRule) -> None:
        """在规则表尾新增一行并填充该规则的编辑控件"""
        table = self._table
        row = table.rowCount()
        table.insertRow(row)

        parts = _MultiSelect([(p, p) for p in QUALITY_PARTS], self._apply)
        parts.set_selected(rule.parts)
        table.setCellWidget(row, 0, parts)

        # 品阶/评级是有序量表 → ≤ 门槛单选，最高档即不限
        quals = QComboBox()
        for q in _QUALITY_KEYS:
            label = QUALITY_LABELS.get(q, q)
            quals.addItem("- 不限 -" if q == "gold" else f"≤ {label}", q)
        quals.setCurrentIndex(max(quals.findData(rule.max_quality), 0))
        quals.currentIndexChanged.connect(lambda _i: self._apply())
        table.setCellWidget(row, 1, quals)

        pct = QSpinBox()
        pct.setRange(0, 100)
        pct.setSuffix(" %")
        pct.setToolTip("命中条件：首词条初始数值 ≤ 该值；"
                       "100 = 不限（识别失败视为不达标）")
        pct.setValue(rule.max_pct)
        pct.valueChanged.connect(lambda _v: self._apply())
        table.setCellWidget(row, 2, pct)

        ratings = QComboBox()
        for r in reversed(RATING_KEYS):
            label = RATING_LABELS.get(r, r)
            ratings.addItem("- 不限 -" if r == "top" else f"≤ {label}", r)
        ratings.setCurrentIndex(max(ratings.findData(rule.max_rating), 0))
        ratings.currentIndexChanged.connect(lambda _i: self._apply())
        table.setCellWidget(row, 3, ratings)

        action = QComboBox()
        labels = _STAGE_ACTION_LABELS[self.STAGE]
        for key in BEHAVIOR_STAGE_ACTIONS[self.STAGE]:
            action.addItem(labels.get(key, key), key)
        action.setCurrentIndex(max(action.findData(rule.action), 0))
        action.currentIndexChanged.connect(lambda _i: self._apply())
        table.setCellWidget(row, 4, action)

    # ── 行增删 ──

    def _on_add_rule(self):
        self._loading = True
        self._make_row_widgets(BehaviorRule())
        self._loading = False
        self._apply()

    def _on_del_rule(self):
        row = self._table.currentRow()
        if row < 0:
            self._status_cb("请先选中要删除的规则行", True)
            return
        self._table.removeRow(row)
        self._apply()

    # ── 回填 ──

    def _load(self):
        # 用管理器已解析的 BehaviorSettings 回填（缺省段/字段已在
        # 解析层落到默认值，无需重复处理）
        stage = getattr(self._manager.get().behavior, self.STAGE)
        self._load_stage(stage)
        self._table.setRowCount(0)
        for rule in stage.rules:
            self._make_row_widgets(rule)

    def _load_stage(self, stage) -> None:
        raise NotImplementedError

    # ── 收集 → 校验 → 写盘 → reload ──

    def _row_rule(self, row: int) -> dict:
        """收集一行的规则控件值为 raw dict"""
        table = self._table
        parts: _MultiSelect = table.cellWidget(row, 0)
        quals: QComboBox = table.cellWidget(row, 1)
        pct: QSpinBox = table.cellWidget(row, 2)
        ratings: QComboBox = table.cellWidget(row, 3)
        action: QComboBox = table.cellWidget(row, 4)
        return {
            "parts": parts.selected(),
            "max_quality": quals.currentData(),
            "max_pct": pct.value(),
            "max_rating": ratings.currentData(),
            "action": action.currentData(),
        }

    def _rules_raw(self) -> list[dict]:
        return [self._row_rule(row)
                for row in range(self._table.rowCount())]

    def _stage_raw(self) -> dict:
        raise NotImplementedError

    def _build(self) -> dict:
        # 以最新 raw 为底只替换 behavior.<stage> 子段，保留其他页
        # 负责的段与另一行为点子段
        data = self._manager.get_raw()
        behavior = data.setdefault("behavior", {})
        behavior[self.STAGE] = self._stage_raw()
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
            logger.exception("行为配置保存失败")
            self._status_cb(f"保存失败：{e}", True)
            return
        now = datetime.now().strftime("%H:%M:%S")
        self._status_cb(f"已保存并生效（{now}）", False)


class ScanBehaviorPage(_BehaviorPageBase):
    """扫描处理编辑页（只负责 behavior.scan 子段）"""

    STAGE = "scan"

    def _init_head(self, layout: QVBoxLayout):
        layout.addWidget(QLabel(
            "<b>扫描处理</b>（进调律前的行为点）：传入规则预期评级 ≥ "
            "进入门槛 → 进入调律；未达门槛的装备按下表处置"
            "（首条命中；无命中 = 保留）"))
        half_line = self.fontMetrics().height() // 2

        head = QHBoxLayout()
        self._enabled_cb = QCheckBox("启用处置表")
        self._enabled_cb.setToolTip(
            "停用后不进调律的装备一律保留（进入门槛仍生效）")
        self._enabled_cb.stateChanged.connect(lambda _s: self._apply())
        head.addWidget(self._enabled_cb)
        head.addSpacing(half_line)
        head.addWidget(QLabel("进入门槛"))
        self._entry_combo = QComboBox()
        for r in reversed(RATING_KEYS):
            label = RATING_LABELS.get(r, r)
            self._entry_combo.addItem(f"预期 ≥ {label} 进入调律", r)
        self._entry_combo.setToolTip(
            "传入规则（运行期勾选）预期评级达到该档才进入调律")
        self._entry_combo.currentIndexChanged.connect(
            lambda _i: self._apply())
        head.addWidget(self._entry_combo)
        head.addSpacing(half_line)
        head.addWidget(QLabel("判定规则语义"))
        self._judge = _JudgeScopeSelect(self._apply)
        head.addWidget(self._judge)
        head.addStretch()
        layout.addLayout(head)

    def _load_stage(self, stage) -> None:
        self._enabled_cb.setChecked(stage.enabled)
        idx = self._entry_combo.findData(stage.entry_min_rating)
        self._entry_combo.setCurrentIndex(max(idx, 0))
        self._judge.set_value(stage.judge_scope, stage.judge_rules)

    def _stage_raw(self) -> dict:
        return {
            "enabled": self._enabled_cb.isChecked(),
            "entry_min_rating": self._entry_combo.currentData(),
            "judge_scope": self._judge.scope(),
            "judge_rules": self._judge.rules(),
            "rules": self._rules_raw(),
        }


class TuneBehaviorPage(_BehaviorPageBase):
    """结束处理编辑页（只负责 behavior.tune 子段）"""

    STAGE = "tune"

    def _init_head(self, layout: QVBoxLayout):
        layout.addWidget(QLabel(
            "<b>结束处理</b>（每轮调律结束后的行为点）：按预期评级决策"
            "（首条命中）。无命中默认：未满 = 继续调律、词条满 = "
            "结束保留；材料不足/用户中断属阻断，不触发"))
        half_line = self.fontMetrics().height() // 2

        head = QHBoxLayout()
        self._enabled_cb = QCheckBox("启用行为表")
        self._enabled_cb.setToolTip("停用后按无命中默认行为执行")
        self._enabled_cb.stateChanged.connect(lambda _s: self._apply())
        head.addWidget(self._enabled_cb)
        head.addSpacing(half_line)
        head.addWidget(QLabel("判定规则语义"))
        self._judge = _JudgeScopeSelect(self._apply)
        head.addWidget(self._judge)
        head.addSpacing(half_line)
        head.addWidget(QLabel("单件重置次数上限"))
        self._resets_spin = QSpinBox()
        self._resets_spin.setRange(0, MAX_TUNE_RESETS)
        self._resets_spin.valueChanged.connect(lambda _v: self._apply())
        head.addWidget(self._resets_spin)
        head.addSpacing(half_line)
        head.addWidget(QLabel("次数用尽后"))
        self._exhausted_combo = QComboBox()
        labels = _STAGE_ACTION_LABELS["tune"]
        for act in ("recycle", "ignore"):
            self._exhausted_combo.addItem(labels.get(act, act), act)
        self._exhausted_combo.setToolTip(
            "规则命中重置但次数已用尽（按钮文本读不到数字）"
            "时的转处置动作")
        self._exhausted_combo.currentIndexChanged.connect(
            lambda _i: self._apply())
        head.addWidget(self._exhausted_combo)
        head.addStretch()
        layout.addLayout(head)

    def _load_stage(self, stage) -> None:
        self._enabled_cb.setChecked(stage.enabled)
        self._judge.set_value(stage.judge_scope, stage.judge_rules)
        self._resets_spin.setValue(stage.max_resets)
        idx = self._exhausted_combo.findData(stage.reset_exhausted_action)
        self._exhausted_combo.setCurrentIndex(max(idx, 0))

    def _stage_raw(self) -> dict:
        return {
            "enabled": self._enabled_cb.isChecked(),
            "judge_scope": self._judge.scope(),
            "judge_rules": self._judge.rules(),
            "rules": self._rules_raw(),
            "max_resets": self._resets_spin.value(),
            "reset_exhausted_action": self._exhausted_combo.currentData(),
        }
