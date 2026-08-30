"""调律配置对话框 —— 行为处理页（扫描处理 / 结束处理）

状态机行为点配置（基础规则组 behavior 段），每页一张有序条件规则表
（自上而下首条命中即生效）：
- 扫描处理（ScanBehaviorPage，behavior.scan）：进调律前，
  传入规则预期评级 ≥ 进入门槛即进入调律；未达门槛的装备按
  处置表决定回收/保留（无命中 = 保留）；
- 结束处理（TuneBehaviorPage，behavior.tune）：每轮调律结束后
  按预期评级决策 继续调律/重置调律/回收/结束保留（词条满为
  边界条件：继续调律不可达，无命中默认结束保留；未满默认
  继续调律）；另有单件重置次数上限 + 次数用尽转处置动作。
每条规则 = 部位多选（至少勾一项，全选展示 - 全部 -）
+ 品阶（不限/金装/紫色/紫装及以下/蓝装及以下）+ 首词条初始数值（≥/≤ 方向可选 + 数值，
≤ 100 / ≥ 0 = 不限）+ 判定结果多选
（候选域随判定语义联动：评级四档 ↔ 自选词条词条集）
+ 仅首词条（仅扫描处理，取评级时只注入首词条）
+ 判定语义（四选一：传入规则/全部规则/自选规则/自选词条；
自选经弹窗勾选；自选词条不跑潜力判定，按装备词条名匹配）
+ 动作（候选按行为点白名单锁定）。
沿用「变更即校验即保存」模式：控件变更即重建 raw dict → 校验 →
通过才写盘并 reload，失败时状态栏红字提示。
`_build()` 以管理器最新 raw 为底、各自只替换 behavior.scan /
behavior.tune 子段，与基础规则/材料配置页各管各段互不覆盖。
页面对准当前选中的基础规则组（set_group 切换后重载）。
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

from lvjiang.apps.yysls.core.evaluator import get_rule_names
from lvjiang.apps.yysls.core.tuning_rules import (
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
    TuningGroup,
    TuningGroupManager,
    rule_affix_candidates,
)
from lvjiang.apps.yysls.ui.game_settings.level_combo import LevelCombo
from lvjiang.apps.yysls.ui.layout_helpers import (
    fit_combo_popup_to_contents,
    fit_combo_to_contents,
)
from lvjiang.ui.button_styles import apply_button_style

from .....i18n import tr

# 品阶候选（从高到低，最高档 = 不限；扫描/结束处理共用）
_QUALITY_KEYS = ("gold", "gold_only", "purple_only", "purple", "blue")
_QUALITY_LABELS = {
    "gold": tr("- 不限 -"),
    "gold_only": tr("金装"),
    "purple_only": tr("紫色"),
    "purple": tr("紫装及以下"),
    "blue": tr("蓝装及以下"),
}

# 规则表列定义（第一列为序号；扫描处理在判定结果后多一列
# 「仅首词条」，列索引经 self._ci 按列 key 取）
_SEQ_COL = 0
_BASE_COL_KEYS = ("seq", "parts", "quality", "judge", "ratings",
                  "pct", "action")
_COL_TITLES = {
    "seq": "#", "parts": tr("部位"), "quality": tr("品阶"),
    "judge": tr("判定语义"), "ratings": tr("判定结果"), "first_affix": tr("仅首词条"),
    "pct": tr("首词条 %"), "action": tr("动作"),
}  # runtime tr()

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
        apply_button_style(self, variant="neutral")
        self._changed = changed
        self._menu = _CheckMenu(self)
        self.setMenu(self._menu)
        self.set_items(items)

    def set_items(self, items: list[tuple[str, str]]):
        """重建候选项（判定语义切换候选域用，保留 changed 回调）；
        重建后全部未勾选，调用方须随后 set_selected 指定选中集"""
        self._menu.clear()
        self._actions = {}
        for key, label in items:
            act = QAction(label, self._menu)
            act.setCheckable(True)
            act.toggled.connect(self._on_toggled)
            self._menu.addAction(act)
            self._actions[key] = act
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
            assert act is not None
            act.blockSignals(True)
            act.setChecked(True)
            act.blockSignals(False)
            return
        self._refresh_text()
        self._changed()

    def _refresh_text(self):
        labels = [a.text() for a in self._actions.values() if a.isChecked()]
        if len(labels) == len(self._actions):
            self.setText(tr("- 全部 -"))
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
        self._op.setToolTip(tr("首词条初始数值比较方向"))
        layout.addWidget(self._op)
        self._spin = QSpinBox()
        self._spin.setRange(0, 100)
        self._spin.setSuffix(" %")
        self._spin.setToolTip(
            tr("命中条件：首词条初始数值按方向比较该值；"
               "≤ 100 / ≥ 0 = 不限（识别失败视为不达标）"))
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
        self.setWindowTitle(tr("自选判定规则"))
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(tr("预期评级识别使用勾选的流派规则：")))
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
        apply_button_style(
            self._buttons.button(QDialogButtonBox.StandardButton.Ok)
        )
        apply_button_style(
            self._buttons.button(QDialogButtonBox.StandardButton.Cancel),
            variant="neutral",
        )
        layout.addWidget(self._buttons)
        self._sync_ok()

    def selected(self) -> list[str]:
        return [k for k, cb in self._boxes.items() if cb.isChecked()]

    def _sync_ok(self):
        ok = self._buttons.button(QDialogButtonBox.StandardButton.Ok)
        ok.setEnabled(bool(self.selected()))


class _AffixEntriesDialog(QDialog):
    """自选词条弹窗：勾选装备词条名（至少一项才可确定）"""

    def __init__(self, items: list[tuple[str, str]], checked: list[str],
                 parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("自选词条"))
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(tr("勾选装备词条名（至少一项）：")))
        # 词条数量较多（41 项），使用滚动区域
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll_widget = QWidget()
        scroll_layout = QVBoxLayout(scroll_widget)
        self._boxes: dict[str, QCheckBox] = {}
        for key, label in items:
            cb = QCheckBox(label)
            cb.setChecked(key in checked)
            cb.toggled.connect(lambda _c: self._sync_ok())
            scroll_layout.addWidget(cb)
            self._boxes[key] = cb
        scroll_layout.addStretch()
        scroll.setWidget(scroll_widget)
        layout.addWidget(scroll)
        self._buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel)
        self._buttons.accepted.connect(self.accept)
        self._buttons.rejected.connect(self.reject)
        apply_button_style(
            self._buttons.button(QDialogButtonBox.StandardButton.Ok)
        )
        apply_button_style(
            self._buttons.button(QDialogButtonBox.StandardButton.Cancel),
            variant="neutral",
        )
        layout.addWidget(self._buttons)
        self._sync_ok()

    def selected(self) -> list[str]:
        return [k for k, cb in self._boxes.items() if cb.isChecked()]

    def _sync_ok(self):
        ok = self._buttons.button(QDialogButtonBox.StandardButton.Ok)
        ok.setEnabled(bool(self.selected()))


class _AffixEntriesButton(QPushButton):
    """按钮式词条多选：点击弹出勾选对话框（至少保留一项）

    接口与 _MultiSelect 对齐：selected() / set_selected() / set_items()
    """

    def __init__(self, items: list[tuple[str, str]],
                 changed: Callable[[], None], parent=None):
        super().__init__(parent)
        apply_button_style(self, variant="neutral")
        self._items = items
        self._checked: set[str] = set()
        self._changed = changed
        self.clicked.connect(self._on_clicked)
        self._refresh_text()

    def selected(self) -> list[str]:
        return [k for k, _ in self._items if k in self._checked]

    def set_selected(self, keys: list[str]):
        # 空入参回退到首个候选（至少保留一项）
        if keys:
            self._checked = set(keys)
        elif self._items:
            self._checked = {self._items[0][0]}
        else:
            self._checked = set()
        self._refresh_text()

    def set_items(self, items: list[tuple[str, str]]):
        self._items = items
        self._checked = set()
        self._refresh_text()

    def _on_clicked(self):
        dlg = _AffixEntriesDialog(self._items, list(self._checked), self)
        if dlg.exec():
            self._checked = set(dlg.selected())
            self._refresh_text()
            self._changed()

    def _refresh_text(self):
        labels = [label for key, label in self._items if key in self._checked]
        if len(labels) == len(self._items) and self._items:
            self.setText(tr("- 全部 -"))
        elif labels:
            self.setText("/".join(labels))
        else:
            self.setText("")


class _JudgeScopeCell(QComboBox):
    """规则行「判定语义」单元格：四选一下拉，自选经弹窗勾选

    选中「自选规则」时弹出勾选对话框（取消则回退原语义）；
    切到「自选词条」不弹窗（词条勾选在判定结果列完成）。
    custom 项文本动态显示摘要（「{首个规则名} 等X个」）。
    rules() 仅 custom 时返回勾选 key，与解析层「judge_rules 仅
    custom 可声明」的约束对齐。on_scope_changed 回调供行内
    联动重置判定结果候选域（在保存前触发）。
    """

    def __init__(self, changed: Callable[[], None],
                 on_scope_changed: Callable[[str], None] | None = None,
                 parent=None):
        super().__init__(parent)
        self._changed = changed
        self._on_scope_changed = on_scope_changed or (lambda _s: None)
        self._keys: list[str] = []
        self._prev = 0
        for scope in JUDGE_SCOPES:
            self.addItem(JUDGE_SCOPE_LABELS.get(scope, scope), scope)
        fit_combo_to_contents(self, minimum=140)
        self.setToolTip(
            tr("本条规则的判定方式：传入规则=运行期勾选的规则；全部规则；"
               "自选规则=弹窗勾选；自选词条=判定结果列改勾词条，"
               "按装备词条名匹配，不跑潜力判定"))
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
        self._on_scope_changed(self.scope())
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
        # 自选规则摘要可能比固定四项更长；同步放宽闭合控件与弹出列表。
        fit_combo_to_contents(self, minimum=140)


class _BehaviorPageBase(QWidget):
    """行为处理页共用骨架：滚动容器 + 有序规则表 + 变更即保存

    子类声明 STAGE（动作白名单/文案按此取），实现 _init_head()
    （表格上方的启用/门槛/判定语义等头部控件）、_stage_raw()
    （收集本行为点子段 raw dict）与 _load_stage()（回填）。
    """

    STAGE = ""

    def __init__(self, manager: TuningGroupManager, group_key: str,
                 status_cb: Callable[[str, bool], None], parent=None):
        super().__init__(parent)
        self._manager = manager
        self._group_key = group_key
        self._status_cb = status_cb
        self._save_cb: Callable[[], None] | None = None
        self._loading = True
        self._init_ui()
        self._load()
        self._loading = False

    def set_save_callback(self, cb: Callable[[], None]):
        """设置保存成功后的回调（用于通知其他页面刷新）"""
        self._save_cb = cb

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
        self._table.setHorizontalHeaderLabels([tr(titles[k]) for k in keys])
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        # 部位/判定结果是按钮式多选，可拉伸吸收剩余空间；真正的下拉列
        # 按最长中文选项保宽，窗口不足时使用横向滚动。
        header.setSectionResizeMode(
            self._ci["parts"], QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(
            self._ci["ratings"], QHeaderView.ResizeMode.Stretch)
        # 序号列固定宽度，首词条列（符号 + 数值）与仅首词条列
        # 按内容自适应，隐藏原生行号
        header.setSectionResizeMode(
            _SEQ_COL, QHeaderView.ResizeMode.Fixed)
        self._table.setColumnWidth(_SEQ_COL, 32)
        header.setSectionResizeMode(
            self._ci["pct"], QHeaderView.ResizeMode.ResizeToContents)
        if "first_affix" in self._ci:
            header.setSectionResizeMode(
                self._ci["first_affix"],
                QHeaderView.ResizeMode.ResizeToContents)
        self._table.verticalHeader().setVisible(False)
        self._table.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.customContextMenuRequested.connect(
            self._on_rule_context_menu)
        self._table.setMinimumHeight(140)
        layout.addWidget(self._table)

        btn_row = QHBoxLayout()
        add_btn = QPushButton(tr("添加规则"))
        add_btn.clicked.connect(lambda _c: self._on_add_rule())
        btn_row.addWidget(add_btn)
        del_btn = QPushButton(tr("删除选中规则"))
        del_btn.clicked.connect(lambda _c: self._on_del_rule())
        btn_row.addWidget(del_btn)
        btn_row.addSpacing(8)
        self._up_btn = QPushButton(tr("▲ 上移"))
        self._up_btn.setToolTip(tr("将选中规则上移一行"))
        self._up_btn.clicked.connect(lambda _c: self._on_move_up())
        self._up_btn.setEnabled(False)
        btn_row.addWidget(self._up_btn)
        self._down_btn = QPushButton(tr("▼ 下移"))
        self._down_btn.setToolTip(tr("将选中规则下移一行"))
        self._down_btn.clicked.connect(lambda _c: self._on_move_down())
        self._down_btn.setEnabled(False)
        btn_row.addWidget(self._down_btn)
        apply_button_style(add_btn)
        apply_button_style(del_btn, variant="danger")
        apply_button_style(self._up_btn, self._down_btn, variant="neutral")
        btn_row.addStretch()
        layout.addLayout(btn_row)
        layout.addStretch()

        # 表格选中变化时更新移动按钮状态
        self._table.itemSelectionChanged.connect(self._update_move_buttons)

    def _init_head(self, layout: QVBoxLayout):
        raise NotImplementedError

    def _apply_ratings_domain(self, ratings, scope: str,
                              selected: list[str]) -> None:
        """按判定语义切换判定结果控件候选域：affix → 词条全集
        （选中集非空，空入参回退首个候选）；其余 → 评级四档
        （空入参 = 全选 = 不限），tooltip 同步切换"""
        if scope == "affix":
            vocab = rule_affix_candidates()
            ratings.set_items([(n, n) for n in vocab])
            ratings.setToolTip(
                tr("命中条件：装备任一条题名属于勾选词条\n"
                   "（勾选仅首词条时只判定装备首词条）；\n"
                   "自选词条语义不跑潜力判定"))
            ratings.set_selected(selected or vocab[:1])
            return
        ratings.set_items(
            [(r, RATING_LABELS.get(r, r)) for r in reversed(RATING_KEYS)])
        ratings.setToolTip(
            tr("命中条件：预期评级属于勾选档位（全选 = 不限，"
               "不取评级）"))
        ratings.set_selected(selected)

    def _create_ratings_widget(self, scope: str):
        """根据判定语义创建对应的判定结果控件：affix → 弹窗勾选，其余 → 下拉菜单"""
        if scope == "affix":
            return _AffixEntriesButton([], self._apply)
        return _MultiSelect([], self._apply)

    def _make_row_widgets(self, rule: BehaviorRule) -> None:
        """在规则表尾新增一行并填充该规则的编辑控件"""
        table = self._table
        row = table.rowCount()
        table.insertRow(row)

        # 序号列（只读标签）
        seq_label = QLabel(str(row + 1))
        seq_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        seq_label.setProperty("rule_enabled", rule.enabled)
        table.setCellWidget(row, _SEQ_COL, seq_label)

        parts = _MultiSelect([(p, p) for p in QUALITY_PARTS], self._apply)
        parts.set_selected(rule.parts)
        table.setCellWidget(row, self._ci["parts"], parts)

        # 品阶候选（不限/金装/紫色/紫装及以下/蓝装及以下）
        quals = QComboBox()
        for q in _QUALITY_KEYS:
            quals.addItem(_QUALITY_LABELS.get(q, q), q)
        fit_combo_to_contents(quals, minimum=112)
        quals.setCurrentIndex(max(quals.findData(rule.max_quality), 0))
        quals.currentIndexChanged.connect(lambda _i: self._apply())
        table.setCellWidget(row, self._ci["quality"], quals)

        pct_widget = _PctCell(rule.pct_op, rule.pct, self._apply)
        table.setCellWidget(row, self._ci["pct"], pct_widget)

        ratings = self._create_ratings_widget(rule.judge_scope)
        # 初始候选域按规则自身判定语义决定（affix → 词条集）
        self._apply_ratings_domain(ratings, rule.judge_scope,
                                   rule.ratings)
        table.setCellWidget(row, self._ci["ratings"], ratings)

        if "first_affix" in self._ci:
            fao = QCheckBox()
            fao.setChecked(rule.first_affix_only)
            fao.setToolTip(
                tr("本条规则取评级时只注入首词条，忽略已有的其他词条\n"
                   "（其余槽视作空槽由潜力判定自由填充）；避免回收掉\n"
                   "非首词条已成垃圾但可重置调律的装备"))
            fao.stateChanged.connect(lambda _s: self._apply())
            table.setCellWidget(row, self._ci["first_affix"], fao)

        judge = _JudgeScopeCell(
            self._apply,
            # 语义切换时重置判定结果候选域（跨域旧值无效，
            # 选中集重置：affix → 首个候选；其余 → 全选 = 不限）
            on_scope_changed=lambda scope: self._apply_ratings_domain(
                ratings, scope, []),
        )
        judge.set_value(rule.judge_scope, rule.judge_rules)
        fit_combo_popup_to_contents(judge, minimum=140)
        table.setCellWidget(row, self._ci["judge"], judge)

        action = QComboBox()
        labels = _STAGE_ACTION_LABELS[self.STAGE]
        for key in BEHAVIOR_STAGE_ACTIONS[self.STAGE]:
            action.addItem(labels.get(key, key), key)
            # 设置每项的 tooltip
            idx = action.count() - 1
            action.setItemData(idx, BEHAVIOR_ACTION_TOOLTIPS.get(key, ""),
                               Qt.ItemDataRole.ToolTipRole)
        fit_combo_to_contents(action, minimum=152)
        action.setCurrentIndex(max(action.findData(rule.action), 0))
        action.currentIndexChanged.connect(lambda _i: self._apply())
        table.setCellWidget(row, self._ci["action"], action)
        self._set_row_enabled(row, rule.enabled)

    def _on_rule_context_menu(self, pos) -> None:
        index = self._table.indexAt(pos)
        if not index.isValid() or index.column() != _SEQ_COL:
            return
        row = index.row()
        seq = self._table.cellWidget(row, _SEQ_COL)
        enabled = bool(seq.property("rule_enabled"))
        menu = QMenu(self._table)
        action = menu.addAction(tr("禁用") if enabled else tr("启用"))
        if menu.exec(self._table.viewport().mapToGlobal(pos)) is action:
            self._set_row_enabled(row, not enabled)
            self._apply()

    def _set_row_enabled(self, row: int, enabled: bool) -> None:
        seq = self._table.cellWidget(row, _SEQ_COL)
        if seq is not None:
            seq.setProperty("rule_enabled", enabled)
            seq.setStyleSheet("" if enabled else "color: gray;")
            seq.setToolTip(tr("右键禁用") if enabled else tr("规则已禁用；右键启用"))
        for col in range(1, self._table.columnCount()):
            widget = self._table.cellWidget(row, col)
            if widget is not None:
                widget.setEnabled(enabled)

    # ── 行增删 ──

    def _on_add_rule(self):
        self._loading = True
        self._make_row_widgets(BehaviorRule())
        self._loading = False
        self._update_move_buttons()
        self._apply()

    def _on_del_rule(self):
        row = self._table.currentRow()
        if row < 0:
            self._status_cb(tr("请先选中要删除的规则行"), True)
            return
        self._table.removeRow(row)
        self._refresh_seq_numbers()
        self._update_move_buttons()
        self._apply()

    def _on_move_up(self):
        row = self._table.currentRow()
        if row <= 0:
            self._status_cb(tr("已是第一条规则，无法上移"), True)
            return
        self._swap_rows(row, row - 1)
        self._table.selectRow(row - 1)
        self._refresh_seq_numbers()
        self._apply()

    def _on_move_down(self):
        row = self._table.currentRow()
        if row < 0 or row >= self._table.rowCount() - 1:
            self._status_cb(tr("已是最后一条规则，无法下移"), True)
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
        table = self._table
        parts: _MultiSelect = table.cellWidget(row, self._ci["parts"])
        parts.set_selected(values["parts"])
        quals: QComboBox = table.cellWidget(row, self._ci["quality"])
        quals.setCurrentIndex(max(quals.findData(values["max_quality"]), 0))
        pct: _PctCell = table.cellWidget(row, self._ci["pct"])
        pct.set_value(values["pct_op"], values["pct"])
        ratings = table.cellWidget(row, self._ci["ratings"])
        self._apply_ratings_domain(ratings, values["judge_scope"],
                                   values["ratings"])
        if "first_affix" in self._ci:
            fao: QCheckBox = table.cellWidget(row, self._ci["first_affix"])
            fao.setChecked(values.get("first_affix_only", False))
        judge: _JudgeScopeCell = table.cellWidget(row, self._ci["judge"])
        judge.set_value(values["judge_scope"], values["judge_rules"])
        action: QComboBox = table.cellWidget(row, self._ci["action"])
        action.setCurrentIndex(max(action.findData(values["action"]), 0))
        self._set_row_enabled(row, values.get("enabled", True))

    # ── 回填 ──

    def _load(self):
        # 用管理器已解析的 ScanBehavior/TuneBehavior 回填（缺省段/字段已在
        # 解析层落到默认值，无需重复处理）
        stage = getattr(self._current_group(), self.STAGE)
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
        ratings = table.cellWidget(row, self._ci["ratings"])
        judge: _JudgeScopeCell = table.cellWidget(row, self._ci["judge"])
        action: QComboBox = table.cellWidget(row, self._ci["action"])
        seq = table.cellWidget(row, _SEQ_COL)
        selected_parts = parts.selected()
        # 全选时折叠为简写，保持 YAML 紧凑
        if set(selected_parts) == set(QUALITY_PARTS):
            selected_parts = [tr("全部")]
        rule = {
            "enabled": bool(seq.property("rule_enabled")),
            "parts": selected_parts,
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
        # 以最新 raw 为底只替换 <stage> 顶级段，保留其他页
        # 负责的段与另一行为点子段
        data = self._manager.get_raw(self._group_key)
        data[self.STAGE] = self._stage_raw()
        return data

    def _apply(self):
        if self._loading:
            return
        data = self._build()
        err = self._manager.validate(data)
        if err:
            self._status_cb(tr("校验失败（未保存）：{err}").format(err=err), True)
            return
        try:
            self._manager.save_group(self._group_key, data)
        except Exception as e:  # noqa: BLE001
            logger.exception("行为配置保存失败")
            self._status_cb(tr("保存失败：{e}").format(e=e), True)
            return
        now = datetime.now().strftime("%H:%M:%S")
        self._status_cb(tr("已保存并生效（{now}）").format(now=now), False)
        # 通知其他页面刷新（如基础规则页展示门槛值）
        if self._save_cb is not None:
            self._save_cb()


class ScanBehaviorPage(_BehaviorPageBase):
    """扫描处理编辑页（只负责 behavior.scan 子段）"""

    STAGE = "scan"

    def _init_head(self, layout: QVBoxLayout):
        layout.addWidget(QLabel(
            "<b>" + tr("扫描处理") + "</b>（" + tr("进调律前的行为点）：未达调律门槛的装备"
            "按下表处置，自上而下首条命中即生效并阻断后续规则；"
            "无命中 = 保留") + "）"))

        # 门槛设置区（位于处置表启用开关上方）
        half_line = self.fontMetrics().height() // 2
        threshold_row = QHBoxLayout()
        threshold_row.addWidget(QLabel(tr("等级门槛")))
        self._min_level_combo = LevelCombo(allow_empty=False)
        fit_combo_to_contents(self._min_level_combo, minimum=88)
        self._min_level_combo.setToolTip(tr("低于该等级的装备直接跳过，不进入任何判定"))
        self._min_level_combo.currentIndexChanged.connect(lambda _v: self._apply())
        threshold_row.addWidget(self._min_level_combo)
        threshold_row.addSpacing(half_line)
        threshold_row.addWidget(QLabel(tr("调律门槛")))
        self._entry_combo = QComboBox()
        # 从高到低：顶级 → 优秀 → 一般 → 垃圾
        for key in reversed(RATING_KEYS):
            self._entry_combo.addItem(RATING_LABELS.get(key, key), key)
        fit_combo_to_contents(self._entry_combo, minimum=96)
        self._entry_combo.setToolTip(tr("预期评级 ≥ 该档即进入调律（固定用传入规则判定）"))
        self._entry_combo.currentIndexChanged.connect(lambda _i: self._apply())
        threshold_row.addWidget(self._entry_combo)
        threshold_row.addStretch()
        layout.addLayout(threshold_row)

        # 最大连续回收次数
        recycle_row = QHBoxLayout()
        recycle_row.addWidget(QLabel(tr("最大连续回收次数")))
        self._max_recycle_spin = QSpinBox()
        self._max_recycle_spin.setRange(1, 999)
        self._max_recycle_spin.setToolTip(
            tr("回收补位循环上限：回收后重读同格续处理的最大次数，必须大于 0"))
        self._max_recycle_spin.valueChanged.connect(lambda _v: self._apply())
        recycle_row.addWidget(self._max_recycle_spin)
        recycle_row.addWidget(QLabel(tr("（须大于 0）")))
        recycle_row.addStretch()
        layout.addLayout(recycle_row)
        layout.addSpacing(half_line)

        # 处置表区：启用开关紧贴规则表
        layout.addSpacing(self.fontMetrics().height())
        head = QHBoxLayout()
        self._enabled_cb = QCheckBox(tr("启用处置表"))
        self._enabled_cb.setToolTip(
            tr("停用后不进调律的装备一律保留（调律门槛仍生效）"))
        self._enabled_cb.stateChanged.connect(lambda _s: self._apply())
        head.addWidget(self._enabled_cb)
        head.addStretch()
        layout.addLayout(head)

    def _load_stage(self, stage) -> None:
        self._min_level_combo.set_level(stage.min_level)
        idx = self._entry_combo.findData(stage.entry_min_rating)
        self._entry_combo.setCurrentIndex(max(idx, 0))
        self._max_recycle_spin.setValue(stage.max_consecutive_recycles)
        self._enabled_cb.setChecked(stage.enabled)

    def _stage_raw(self) -> dict:
        return {
            "enabled": self._enabled_cb.isChecked(),
            "min_level": self._min_level_combo.get_level() or 100,
            "entry_min_rating": self._entry_combo.currentData(),
            "max_consecutive_recycles": self._max_recycle_spin.value(),
            "rules": self._rules_raw(),
        }


class TuneBehaviorPage(_BehaviorPageBase):
    """结束处理编辑页（只负责 behavior.tune 子段）"""

    STAGE = "tune"

    def _init_head(self, layout: QVBoxLayout):
        layout.addWidget(QLabel(
            "<b>" + tr("结束处理") + "</b>（" + tr("每轮调律结束后的行为点）：按预期评级决策"
            "（首条命中）。无命中默认：未满 = 继续调律、词条满 = "
            "结束保留；材料不足/用户中断属阻断，不触发") + "）"))
        half_line = self.fontMetrics().height() // 2

        head = QHBoxLayout()
        self._enabled_cb = QCheckBox(tr("启用行为表"))
        self._enabled_cb.setToolTip(tr("停用后按无命中默认行为执行"))
        self._enabled_cb.stateChanged.connect(lambda _s: self._apply())
        head.addWidget(self._enabled_cb)
        head.addSpacing(half_line)
        head.addWidget(QLabel(tr("单件重置次数上限")))
        self._resets_spin = QSpinBox()
        self._resets_spin.setRange(0, MAX_TUNE_RESETS)
        self._resets_spin.valueChanged.connect(lambda _v: self._apply())
        head.addWidget(self._resets_spin)
        head.addSpacing(half_line)
        head.addWidget(QLabel(tr("次数用尽后")))
        self._exhausted_combo = QComboBox()
        labels = _STAGE_ACTION_LABELS["tune"]
        for act in ("recycle", "skip"):
            self._exhausted_combo.addItem(labels.get(act, act), act)
        fit_combo_to_contents(self._exhausted_combo, minimum=132)
        self._exhausted_combo.setToolTip(
            tr("规则命中重置但次数已用尽（按钮文本读不到数字）"
               "时的转处置动作"))
        self._exhausted_combo.currentIndexChanged.connect(
            lambda _i: self._apply())
        head.addWidget(self._exhausted_combo)
        head.addStretch()
        layout.addLayout(head)

        # 初始判定复选框
        init_row = QHBoxLayout()
        self._initial_check_cb = QCheckBox(tr("启用初始判定"))
        self._initial_check_cb.setToolTip(
            tr("勾选后，对每件装备进行第一次调律前会先执行一次结束处理判定。\n"
               "用于支持本身已经是废品的装备先重置，再开始正常调律。"))
        self._initial_check_cb.stateChanged.connect(lambda _s: self._apply())
        init_row.addWidget(self._initial_check_cb)
        init_row.addWidget(QLabel(
            "<font color='gray'>" + tr("对装备进行第一次调律前执行一次结束处理，"
            "用于支持装备直接重置") + "</font>"))
        init_row.addStretch()
        layout.addLayout(init_row)

    def _load_stage(self, stage) -> None:
        self._enabled_cb.setChecked(stage.enabled)
        self._resets_spin.setValue(stage.max_resets)
        idx = self._exhausted_combo.findData(stage.reset_exhausted_action)
        self._exhausted_combo.setCurrentIndex(max(idx, 0))
        self._initial_check_cb.setChecked(stage.initial_check)

    def _stage_raw(self) -> dict:
        return {
            "enabled": self._enabled_cb.isChecked(),
            "rules": self._rules_raw(),
            "max_resets": self._resets_spin.value(),
            "reset_exhausted_action": self._exhausted_combo.currentData(),
            "initial_check": self._initial_check_cb.isChecked(),
        }
