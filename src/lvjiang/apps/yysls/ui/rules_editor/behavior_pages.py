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
每条规则 = 部位多选（至少勾一项，全选展示 - 全部 -）
+ 品阶（不限/金装/紫装及以下/蓝装及以下）+ 首词条初始数值（≥/≤ 方向可选 + 数值，
≤ 100 / ≥ 0 = 不限）+ 判定评级多选
（四档自由勾选，全选 = 不限）+ 仅首词条（仅扫描处理，
取评级时只注入首词条）+ 判定语义（预期评级识别用
哪个流派规则集：传入规则/全部规则/自选规则，自选经弹窗
勾选）+ 动作（候选按行为点白名单锁定）。
沿用「变更即校验即保存」模式：控件变更即重建 raw dict → 校验 →
通过才写盘并 reload，失败时状态栏红字提示。
`_build()` 以管理器最新 raw 为底、各自只替换 behavior.scan /
behavior.tune 子段，与基础/材料配置页各管各段互不覆盖。
"""

from __future__ import annotations

from datetime import datetime
from typing import Callable

from loguru import logger
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
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
    BEHAVIOR_ACTION_TOOLTIPS,
    BEHAVIOR_STAGE_ACTIONS,
    JUDGE_SCOPE_LABELS,
    JUDGE_SCOPES,
    MAX_TUNE_RESETS,
    PCT_OP_LABELS,
    PCT_OPS,
    QUALITY_PARTS,
    RATING_KEYS,
    RATING_LABELS,
    BehaviorRule,
    TuningBaseManager,
)

# 品阶候选（从高到低，最高档 = 不限；扫描/结束处理共用）
_QUALITY_KEYS = ("gold", "gold_only", "purple", "blue")
_QUALITY_LABELS = {
    "gold": "- 不限 -",
    "gold_only": "金装",
    "purple": "紫装及以下",
    "blue": "蓝装及以下",
}

# 规则表列定义（第一列为排序按钮；扫描处理在判定结果后多一列
# 「仅首词条」，列索引经 self._ci 按列 key 取）
_SORT_COL = 0
_BASE_COL_KEYS = ("sort", "parts", "quality", "judge", "ratings",
                  "pct", "action")
_COL_TITLES = {
    "sort": "", "parts": "部位", "quality": "品阶",
    "judge": "判定规则", "ratings": "判定结果", "first_affix": "仅首词条",
    "pct": "首词条 %", "action": "动作",
}

# 各行为点的动作标签（统一使用 BEHAVIOR_ACTION_LABELS）
_STAGE_ACTION_LABELS = {
    "scan": BEHAVIOR_ACTION_LABELS,
    "tune": BEHAVIOR_ACTION_LABELS,
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


class _PctCell(QWidget):
    """规则行「首词条」单元格：比较方向（≥/≤）下拉 + 数值

    不限语义跟随方向：≤ 100 / ≥ 0 均为不限；非不限时
    识别失败视为不达标。
    """

    def __init__(self, op: str, value: int,
                 changed: Callable[[], None], parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(2, 0, 2, 0)
        layout.setSpacing(2)
        self._op = QComboBox()
        for key in PCT_OPS:
            self._op.addItem(PCT_OP_LABELS.get(key, key), key)
        # 符号列需容纳「符号 + 下拉箭头」，过窄会被箭头挤掉
        self._op.setMinimumWidth(52)
        self._op.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToContents)
        self._op.setToolTip("首词条初始数值比较方向")
        layout.addWidget(self._op)
        self._spin = QSpinBox()
        self._spin.setRange(0, 100)
        self._spin.setSuffix(" %")
        self._spin.setToolTip(
            "命中条件：首词条初始数值按方向比较该值；"
            "≤ 100 / ≥ 0 = 不限（识别失败视为不达标）")
        layout.addWidget(self._spin, stretch=1)
        self.set_value(op, value)
        self._op.currentIndexChanged.connect(lambda _i: changed())
        self._spin.valueChanged.connect(lambda _v: changed())

    def op(self) -> str:
        return self._op.currentData()

    def value(self) -> int:
        return self._spin.value()

    def set_value(self, op: str, value: int):
        self._op.blockSignals(True)
        self._op.setCurrentIndex(max(self._op.findData(op), 0))
        self._op.blockSignals(False)
        self._spin.blockSignals(True)
        self._spin.setValue(value)
        self._spin.blockSignals(False)


class _JudgeRulesDialog(QDialog):
    """自选判定规则弹窗：勾选流派规则（至少一项才可确定）"""

    def __init__(self, checked: list[str], parent=None):
        super().__init__(parent)
        self.setWindowTitle("自选判定规则")
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("预期评级识别使用勾选的流派规则："))
        self._boxes: dict[str, QCheckBox] = {}
        for key, name in get_rule_names().items():
            cb = QCheckBox(name)
            cb.setChecked(key in checked)
            cb.toggled.connect(lambda _c: self._sync_ok())
            layout.addWidget(cb)
            self._boxes[key] = cb
        self._buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel)
        self._buttons.accepted.connect(self.accept)
        self._buttons.rejected.connect(self.reject)
        layout.addWidget(self._buttons)
        self._sync_ok()

    def selected(self) -> list[str]:
        return [k for k, cb in self._boxes.items() if cb.isChecked()]

    def _sync_ok(self):
        ok = self._buttons.button(QDialogButtonBox.StandardButton.Ok)
        ok.setEnabled(bool(self.selected()))


class _JudgeScopeCell(QComboBox):
    """规则行「判定语义」单元格：三选一下拉，自选经弹窗勾选

    选中「自选规则」时弹出勾选对话框（取消则回退原语义）；
    custom 项文本动态显示摘要（「{首个规则名} 等X个」）。
    rules() 仅 custom 时返回勾选 key，与解析层「judge_rules 仅
    custom 可声明」的约束对齐。
    """

    def __init__(self, changed: Callable[[], None], parent=None):
        super().__init__(parent)
        self._changed = changed
        self._keys: list[str] = []
        self._prev = 0
        for scope in JUDGE_SCOPES:
            self.addItem(JUDGE_SCOPE_LABELS.get(scope, scope), scope)
        self.setToolTip(
            "本条规则的评级用哪个流派规则集：传入规则=运行期勾选的"
            "规则；全部规则；自选规则=弹窗勾选")
        self.activated.connect(self._on_activated)

    def scope(self) -> str:
        return self.currentData()

    def rules(self) -> list[str]:
        return list(self._keys) if self.scope() == "custom" else []

    def set_value(self, scope: str, keys: list[str]):
        self._keys = list(keys)
        self.blockSignals(True)
        self.setCurrentIndex(max(self.findData(scope), 0))
        self.blockSignals(False)
        self._prev = self.currentIndex()
        self._refresh_summary()

    def _on_activated(self, index: int):
        if self.itemData(index) == "custom":
            dlg = _JudgeRulesDialog(self._keys, self)
            if dlg.exec():
                self._keys = dlg.selected()
            else:
                # 取消弹窗：回退原判定语义，不触发保存
                self.setCurrentIndex(self._prev)
                return
        self._prev = self.currentIndex()
        self._refresh_summary()
        self._changed()

    def _refresh_summary(self):
        idx = self.findData("custom")
        if not self._keys:
            text = JUDGE_SCOPE_LABELS["custom"]
        else:
            names = get_rule_names()
            first = names.get(self._keys[0], self._keys[0])
            text = (first if len(self._keys) == 1
                    else f"{first} 等{len(self._keys)}个")
        self.setItemText(idx, text)


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

        # 扫描处理多「仅首词条」列（插入在判定结果后、首词条%前）
        keys = list(_BASE_COL_KEYS)
        titles = dict(_COL_TITLES)
        if self.STAGE == "scan":
            keys.insert(keys.index("pct"), "first_affix")
        self._ci = {k: i for i, k in enumerate(keys)}
        self._table = QTableWidget(0, len(keys))
        self._table.setHorizontalHeaderLabels([titles[k] for k in keys])
        self._table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch)
        # 排序列固定宽度，首词条列（符号 + 数值）与仅首词条列
        # 按内容自适应，隐藏序号列
        self._table.horizontalHeader().setSectionResizeMode(
            _SORT_COL, QHeaderView.ResizeMode.Fixed)
        self._table.setColumnWidth(_SORT_COL, 28)
        self._table.horizontalHeader().setSectionResizeMode(
            self._ci["pct"], QHeaderView.ResizeMode.ResizeToContents)
        if "first_affix" in self._ci:
            self._table.horizontalHeader().setSectionResizeMode(
                self._ci["first_affix"],
                QHeaderView.ResizeMode.ResizeToContents)
        self._table.verticalHeader().setVisible(False)
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

        # 排序按钮列（上三角 + 下三角）
        sort_widget = QWidget()
        sort_layout = QVBoxLayout(sort_widget)
        sort_layout.setContentsMargins(2, 2, 2, 2)
        sort_layout.setSpacing(1)
        up_btn = QPushButton("▲")
        up_btn.setFixedSize(22, 16)
        up_btn.setToolTip("上移")
        up_btn.clicked.connect(lambda: self._on_move_up(row))
        sort_layout.addWidget(up_btn)
        down_btn = QPushButton("▼")
        down_btn.setFixedSize(22, 16)
        down_btn.setToolTip("下移")
        down_btn.clicked.connect(lambda: self._on_move_down(row))
        sort_layout.addWidget(down_btn)
        sort_layout.addStretch()
        sort_layout.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        table.setCellWidget(row, _SORT_COL, sort_widget)

        parts = _MultiSelect([(p, p) for p in QUALITY_PARTS], self._apply)
        parts.set_selected(rule.parts)
        table.setCellWidget(row, self._ci["parts"], parts)

        # 品阶候选（不限/金装/紫装及以下/蓝装及以下）
        quals = QComboBox()
        for q in _QUALITY_KEYS:
            quals.addItem(_QUALITY_LABELS.get(q, q), q)
        quals.setCurrentIndex(max(quals.findData(rule.max_quality), 0))
        quals.currentIndexChanged.connect(lambda _i: self._apply())
        table.setCellWidget(row, self._ci["quality"], quals)

        pct_widget = _PctCell(rule.pct_op, rule.pct, self._apply)
        table.setCellWidget(row, self._ci["pct"], pct_widget)

        ratings = _MultiSelect(
            [(r, RATING_LABELS.get(r, r)) for r in reversed(RATING_KEYS)],
            self._apply)
        ratings.setToolTip(
            "命中条件：预期评级属于勾选档位（全选 = 不限，"
            "不取评级）")
        ratings.set_selected(rule.ratings)
        table.setCellWidget(row, self._ci["ratings"], ratings)

        if "first_affix" in self._ci:
            fao = QCheckBox()
            fao.setChecked(rule.first_affix_only)
            fao.setToolTip(
                "本条规则取评级时只注入首词条，忽略已有的其他词条\n"
                "（其余槽视作空槽由潜力判定自由填充）；避免回收掉\n"
                "非首词条已成垃圾但可重置调律的装备")
            fao.stateChanged.connect(lambda _s: self._apply())
            table.setCellWidget(row, self._ci["first_affix"], fao)

        judge = _JudgeScopeCell(self._apply)
        judge.set_value(rule.judge_scope, rule.judge_rules)
        table.setCellWidget(row, self._ci["judge"], judge)

        action = QComboBox()
        labels = _STAGE_ACTION_LABELS[self.STAGE]
        for key in BEHAVIOR_STAGE_ACTIONS[self.STAGE]:
            action.addItem(labels.get(key, key), key)
            # 设置每项的 tooltip
            idx = action.count() - 1
            action.setItemData(idx, BEHAVIOR_ACTION_TOOLTIPS.get(key, ""),
                               Qt.ItemDataRole.ToolTipRole)
        action.setCurrentIndex(max(action.findData(rule.action), 0))
        action.currentIndexChanged.connect(lambda _i: self._apply())
        table.setCellWidget(row, self._ci["action"], action)

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

    def _on_move_up(self, row: int):
        if row <= 0:
            self._status_cb("已是第一条规则，无法上移", True)
            return
        self._swap_rows(row, row - 1)
        self._table.selectRow(row - 1)
        self._apply()

    def _on_move_down(self, row: int):
        if row < 0 or row >= self._table.rowCount() - 1:
            self._status_cb("已是最后一条规则，无法下移", True)
            return
        self._swap_rows(row, row + 1)
        self._table.selectRow(row + 1)
        self._apply()

    def _swap_rows(self, row_a: int, row_b: int) -> None:
        """交换两行的规则数据（含所有控件值）"""
        values_a = self._row_rule(row_a)
        values_b = self._row_rule(row_b)
        self._set_row_values(row_a, values_b)
        self._set_row_values(row_b, values_a)

    def _set_row_values(self, row: int, values: dict) -> None:
        """将 raw dict 写回指定行的控件"""
        table = self._table
        parts: _MultiSelect = table.cellWidget(row, self._ci["parts"])
        parts.set_selected(values["parts"])
        quals: QComboBox = table.cellWidget(row, self._ci["quality"])
        quals.setCurrentIndex(max(quals.findData(values["max_quality"]), 0))
        pct: _PctCell = table.cellWidget(row, self._ci["pct"])
        pct.set_value(values["pct_op"], values["pct"])
        ratings: _MultiSelect = table.cellWidget(row, self._ci["ratings"])
        ratings.set_selected(values["ratings"])
        if "first_affix" in self._ci:
            fao: QCheckBox = table.cellWidget(row, self._ci["first_affix"])
            fao.setChecked(values.get("first_affix_only", False))
        judge: _JudgeScopeCell = table.cellWidget(row, self._ci["judge"])
        judge.set_value(values["judge_scope"], values["judge_rules"])
        action: QComboBox = table.cellWidget(row, self._ci["action"])
        action.setCurrentIndex(max(action.findData(values["action"]), 0))

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
        parts: _MultiSelect = table.cellWidget(row, self._ci["parts"])
        quals: QComboBox = table.cellWidget(row, self._ci["quality"])
        pct: _PctCell = table.cellWidget(row, self._ci["pct"])
        ratings: _MultiSelect = table.cellWidget(row, self._ci["ratings"])
        judge: _JudgeScopeCell = table.cellWidget(row, self._ci["judge"])
        action: QComboBox = table.cellWidget(row, self._ci["action"])
        rule = {
            "parts": parts.selected(),
            "max_quality": quals.currentData(),
            "pct_op": pct.op(),
            "pct": pct.value(),
            "ratings": ratings.selected(),
            "judge_scope": judge.scope(),
            "judge_rules": judge.rules(),
            "action": action.currentData(),
        }
        if "first_affix" in self._ci:
            fao: QCheckBox = table.cellWidget(row, self._ci["first_affix"])
            rule["first_affix_only"] = fao.isChecked()
        return rule

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
            "<b>扫描处理</b>（进调律前的行为点）：未达调律门槛的装备"
            "按下表处置，自上而下首条命中即生效并阻断后续规则；"
            "无命中 = 保留"))

        # 处置表区：启用开关紧贴规则表
        layout.addSpacing(self.fontMetrics().height())
        head = QHBoxLayout()
        self._enabled_cb = QCheckBox("启用处置表")
        self._enabled_cb.setToolTip(
            "停用后不进调律的装备一律保留（调律门槛仍生效）")
        self._enabled_cb.stateChanged.connect(lambda _s: self._apply())
        head.addWidget(self._enabled_cb)
        head.addStretch()
        layout.addLayout(head)

    def _load_stage(self, stage) -> None:
        self._enabled_cb.setChecked(stage.enabled)

    def _stage_raw(self) -> dict:
        # 调律门槛已移至基础配置页，整段重建时透传最新值避免丢失
        scan = ((self._manager.get_raw().get("behavior") or {})
                .get("scan") or {})
        return {
            "enabled": self._enabled_cb.isChecked(),
            "entry_min_rating": scan.get("entry_min_rating", "excellent"),
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
        head.addWidget(QLabel("单件重置次数上限"))
        self._resets_spin = QSpinBox()
        self._resets_spin.setRange(0, MAX_TUNE_RESETS)
        self._resets_spin.valueChanged.connect(lambda _v: self._apply())
        head.addWidget(self._resets_spin)
        head.addSpacing(half_line)
        head.addWidget(QLabel("次数用尽后"))
        self._exhausted_combo = QComboBox()
        labels = _STAGE_ACTION_LABELS["tune"]
        for act in ("recycle", "skip"):
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
        self._resets_spin.setValue(stage.max_resets)
        idx = self._exhausted_combo.findData(stage.reset_exhausted_action)
        self._exhausted_combo.setCurrentIndex(max(idx, 0))

    def _stage_raw(self) -> dict:
        return {
            "enabled": self._enabled_cb.isChecked(),
            "rules": self._rules_raw(),
            "max_resets": self._resets_spin.value(),
            "reset_exhausted_action": self._exhausted_combo.currentData(),
        }
