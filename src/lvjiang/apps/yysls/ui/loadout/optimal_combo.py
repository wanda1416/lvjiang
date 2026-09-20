"""最优毕业率装备组合搜索对话框。

从当前用户的 equipped + bag_items 中收集候选装备，
按槽位分组展示，用户勾选后暴力穷举 + 支配剪枝搜索最优组合。
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from typing import Any

from loguru import logger
from PyQt6.QtCore import (
    Qt,
    pyqtSignal,
)
from PyQt6.QtWidgets import (
    QAbstractScrollArea,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLayout,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from lvjiang.ui.button_styles import (
    apply_button_style,
    apply_compact_button_style,
    apply_dialog_button_box_style,
)
from lvjiang.ui.widgets import FlowLayout

from .....i18n import tr
from ...config.equipment_slots import SLOT_SPECS
from ...core.affix_cap import affix_dict_cap_pct
from ...core.combat.combat_attrs import (
    CombatAttributes,
)
from ...core.equip_parser.dingyin_parser import is_zhige_dingyin
from ...core.graduation.assumptions import Assumptions
from ...core.graduation.candidate_pool import (
    CandidateFilter,
    collect_candidates,
)
from ...core.graduation.context import gongjue_attrs
from ...core.graduation.optimal_combo import OPTIMAL_RESULT_LIMIT
from ..domain_labels import domain_label
from ..events import EQUIPMENT_CHANGED, get_event_hub
from ..layout_helpers import fit_combo_to_contents
from .background import JobContext, JobController, JobProgress
from .widgets import (
    assumption_pill,
    highlight_pill,
    make_pill,
    muted_pill,
    style_document_tabs,
)

#: 「评级要求」可选档位，由高到低。垃圾不列：要求「至少是垃圾」等于没有要求。
_MIN_RATING_CHOICES: tuple[str, ...] = ("顶级", "优秀", "一般")

#: 默认要求。一般是「这件装备还能用」的下限，比顶级/优秀都不容易把候选筛空。
_DEFAULT_MIN_RATING = "一般"


def _global_top_results(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """跨弓玦场景按毕业率统一排名，只保留全局 Top N。"""
    return sorted(
        results,
        key=lambda result: float(result.get("rate") or 0.0),
        reverse=True,
    )[:OPTIMAL_RESULT_LIMIT]


def _playstyle_match_scope(
    name: str,
    config: dict,
    *,
    current_playstyle: str,
    current_school: str,
    current_attr: str,
) -> str:
    """返回玩法相对当前方案的最精确层级，空串表示仅作其他候选。"""
    if current_playstyle and name == current_playstyle:
        return "plan"
    if current_school and config.get("school") == current_school:
        return "school"
    if current_attr and config.get("attr") == current_attr:
        return "attr"
    return ""


class _PlaystylePickerDialog(QDialog):
    """挑选参与筛选的「调律规则-玩法」组合。

    一件装备往往同时符合好几套玩法，只能选一条规则时能留下的装备极少。
    所以这里是多选，判定取各条规则给出的**最高**评级。

    本方案、本流派、本属性依次排在最前：全部规则的玩法加起来有几十条，
    不分层的话找相关玩法要翻半天。
    """

    def __init__(self, options: list[tuple[str, str, str, str]],
                 selected: set[tuple[str, str]], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("选择调律玩法"))
        self.resize(420, 520)
        layout = QVBoxLayout(self)
        hint = QLabel(tr("勾选参与候选筛选的玩法；装备只要有一条玩法达标就保留"))
        hint.setWordWrap(True)
        hint.setProperty("tone", "muted")
        layout.addWidget(hint)

        self._list = QListWidget()
        layout.addWidget(self._list)
        scope_labels = {
            "plan": tr("（本方案）"),
            "school": tr("（本流派）"),
            "attr": tr("（本属性）"),
        }
        for rule_key, playstyle, label, scope in options:
            scope_label = scope_labels.get(scope, "")
            item = QListWidgetItem(
                f"{label}　{scope_label}" if scope_label else label)
            item.setData(Qt.ItemDataRole.UserRole, (rule_key, playstyle))
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(
                Qt.CheckState.Checked
                if (rule_key, playstyle) in selected
                else Qt.CheckState.Unchecked)
            self._list.addItem(item)

        row = QHBoxLayout()
        for text, checked in ((tr("全选"), True), (tr("全不选"), False)):
            button = QPushButton(text)
            apply_compact_button_style(button, variant="neutral")
            button.clicked.connect(lambda _c, v=checked: self._set_all(v))
            row.addWidget(button)
        row.addStretch()
        layout.addLayout(row)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        apply_dialog_button_box_style(buttons)
        layout.addWidget(buttons)

    def _set_all(self, checked: bool) -> None:
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        for index in range(self._list.count()):
            item = self._list.item(index)
            if item is not None:
                item.setCheckState(state)

    def values(self) -> list[tuple[str, str]]:
        chosen = []
        for index in range(self._list.count()):
            item = self._list.item(index)
            if item is not None and item.checkState() == Qt.CheckState.Checked:
                rule_key, playstyle = item.data(Qt.ItemDataRole.UserRole)
                chosen.append((str(rule_key), str(playstyle)))
        return chosen


class _ClickableLineEdit(QLineEdit):
    """只读展示框，点一下就打开选择器。

    多选的结果是一串「规则-玩法」，下拉框装不下也表达不了；用输入框展示、
    点击弹选择器，既看得见全部选中项，也不必再点一个额外的按钮。
    """

    clicked = pyqtSignal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setReadOnly(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mousePressEvent(self, a0) -> None:  # noqa: N802 — Qt 命名
        super().mousePressEvent(a0)
        self.clicked.emit()


class _MultiSelectMenu(QMenu):
    """勾选后保持展开，便于一次选择多种弓玦套装。"""

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 — Qt 命名
        action = self.activeAction()
        if action is not None and action.isCheckable():
            action.trigger()
            event.accept()
            return
        super().mouseReleaseEvent(event)


# 8 个装备槽位的显示顺序与分组映射
#: (slot_key, display_name, bag_filter_type)；唯一定义见 config.equipment_slots
_SLOT_ORDER: list[tuple[str, str, str]] = [
    (spec.key, spec.label, spec.filter_type) for spec in SLOT_SPECS
]

_QUALITY_COLORS = {
    "gold": "#B8860B",
    "purple": "#8B5CF6",
    "blue": "#2563EB",
    "green": "#16A34A",
}

# ---------------------------------------------------------------------------
# Worker signals + runnable
# ---------------------------------------------------------------------------

class _ScenarioProgress:
    """把单套弓玦的搜索进度折算到多场景总进度（写入共享的 JobProgress）。"""

    def __init__(self, progress: JobProgress, name: str, index: int, count: int,
                 completed: int) -> None:
        self._progress = progress
        self._name = name or tr("无")
        self._index = index
        self._count = count
        self._completed = completed
        self._evaluated = 0
        self._total = 0

    @property
    def evaluated(self) -> int:
        return self._evaluated

    @evaluated.setter
    def evaluated(self, value: int) -> None:
        self._evaluated = value
        self._progress.evaluated = self._completed + value

    @property
    def total(self) -> int:
        return self._total

    @total.setter
    def total(self, value: int) -> None:
        self._total = value
        remaining = self._count - self._index + 1
        self._progress.total = self._completed + value * remaining

    @property
    def message(self) -> str:
        return self._progress.message

    @message.setter
    def message(self, value: str) -> None:
        prefix = tr("弓玦 {name}（{index}/{total}）").format(
            name=self._name, index=self._index, total=self._count)
        self._progress.message = f"{prefix}　{value}"


def _search_job(
    candidates: dict[str, list[dict]],
    school: str,
    scheme: str,
    scenarios: list[tuple[str, CombatAttributes]],
    use_dominance_pruning: bool,
    assumptions: Assumptions,
    season_level: int,
) -> Callable[[JobContext], list[dict[str, Any]]]:
    """构造交给 JobController 的搜索函数：逐弓玦场景搜索并合并结果。"""

    def run(ctx: JobContext) -> list[dict[str, Any]]:
        from ...core.graduation import get_graduation_calculator
        from ...core.graduation.optimal_combo import search_optimal_combo

        calc = get_graduation_calculator(school, scheme)
        if calc is None:
            raise ValueError(tr("未找到对应流派的毕业率方案"))

        results: list[dict[str, Any]] = []
        completed = 0
        for index, (gongjue, base_attrs) in enumerate(scenarios, 1):
            if ctx.is_cancelled():
                break
            progress = _ScenarioProgress(
                ctx.progress, gongjue, index, len(scenarios), completed)
            scenario_results = search_optimal_combo(
                candidates,
                calc,
                base_attrs,
                use_dominance_pruning=use_dominance_pruning,
                cancel_flag=ctx.is_cancelled,
                assumptions=assumptions,
                season_level=season_level,
                progress_counter=progress,
            )
            completed += progress.evaluated
            for result in scenario_results:
                result["gongjue"] = gongjue
            results.extend(scenario_results)
        return _global_top_results(results)

    return run


# ---------------------------------------------------------------------------
# UI components
# ---------------------------------------------------------------------------

def _equip_label(equip: dict) -> str:
    """装备的简短显示文本。"""
    name = equip.get("name", tr("未知"))
    quality = equip.get("quality", "")
    level = equip.get("level", "?")
    color = _QUALITY_COLORS.get(quality, "#666")
    return f'<span style="color:{color}">{name}</span> (Lv{level})'


def _equip_tooltip(equip: dict) -> str:
    """装备详细信息 tooltip（HTML）。"""
    parts: list[str] = []
    name = equip.get("name", tr("未知"))
    level = equip.get("level", "?")
    quality = equip.get("quality", "")
    color = _QUALITY_COLORS.get(quality, "#666")
    parts.append(
        f'<b><span style="color:{color}">{name}</span></b>  Lv{level}')
    is_cy = equip.get("is_chengyin", False)
    if is_cy:
        parts.append(tr("承音"))
    # 基础属性
    base = equip.get("base_attr")
    if isinstance(base, dict) and base.get("name"):
        val = base.get("value")
        if isinstance(val, list):
            parts.append(f"{base['name']}: {val[0]}~{val[1]}")
        else:
            parts.append(f"{base['name']}: {val}")
    # 普通词条
    level = equip.get("level")
    for i in range(1, 6):
        affix = equip.get(f"affix_{i}")
        if isinstance(affix, dict) and affix.get("name"):
            val = affix.get("value", "")
            cap_pct = affix_dict_cap_pct(affix, level)
            line = f"{affix['name']}: {val}"
            if cap_pct is not None:
                line += f" ({cap_pct:.0f}%)"
            parts.append(line)
    # 定音词条
    dingyin = equip.get("dingyin")
    if is_zhige_dingyin(equip):
        parts.append(tr("&lt;止戈定音&gt;"))
    elif isinstance(dingyin, dict) and dingyin.get("name"):
        val = dingyin.get("value", "")
        cap_pct = affix_dict_cap_pct(dingyin, level)
        line = f"{tr('定音')} {dingyin['name']}: {val}"
        if cap_pct is not None:
            line += f" ({cap_pct:.0f}%)"
        parts.append(line)
    return "<br>".join(parts)


class _ClickableLabel(QLabel):
    """点一下就发信号的标签。"""

    clicked = pyqtSignal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mousePressEvent(self, ev) -> None:  # noqa: N802 — Qt 命名
        super().mousePressEvent(ev)
        self.clicked.emit()


class _EquipDetailPopup(QFrame):
    """装备详情浮窗：点名称弹出，点别处关闭。

    原来挂的是 tooltip，要悬停等系统那 ~700ms 延迟才出来，而这一页正是
    逐件比对词条的地方，等待成本乘以候选数量。改成单击即出，且不会因为
    鼠标一动就消失——照着念词条时这点很重要。
    """

    def __init__(self, html: str, parent: QWidget | None = None) -> None:
        super().__init__(parent, Qt.WindowType.Popup)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setStyleSheet(
            "QFrame { background: palette(base);"
            " border: 1px solid palette(mid); border-radius: 6px; }"
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        body = QLabel(html)
        body.setTextFormat(Qt.TextFormat.RichText)
        body.setStyleSheet("font-size: 12px; border: none;")
        layout.addWidget(body)


class _CandidateRow(QWidget):
    """单件候选装备行：勾选框 + 名称 + 评分。"""

    def __init__(
        self, equip: dict, rating: str, parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.equip = equip
        layout = QHBoxLayout(self)
        self.setObjectName("optimalCandidateRow")
        self.setStyleSheet(
            "QWidget#optimalCandidateRow { border-radius: 4px; }"
            "QWidget#optimalCandidateRow:hover { background: palette(alternate-base); }"
        )
        layout.setContentsMargins(5, 3, 5, 3)
        layout.setSpacing(6)

        self.checkbox = QCheckBox()
        self.checkbox.setChecked(True)
        layout.addWidget(self.checkbox)

        self.label = _ClickableLabel()
        self.label.setTextFormat(Qt.TextFormat.RichText)
        self.label.setText(_equip_label(equip))
        self.label.setToolTip(tr("单击查看词条详情"))
        self.label.clicked.connect(self._show_detail)
        layout.addWidget(self.label, stretch=1)
        self._popup: _EquipDetailPopup | None = None

        self.score_label = QLabel(rating)
        self.score_label.setToolTip(tr("所选调律规则的实际评级"))
        self.score_label.setStyleSheet("font-size: 11px; color: palette(mid);")
        layout.addWidget(self.score_label)

    def set_rating(self, rating: str) -> None:
        self.score_label.setText(rating)

    def _show_detail(self) -> None:
        """在名称正下方弹出详情浮窗。

        每次重建：装备数据本身不变，但重建比缓存一个跟着滚动跑的窗口简单，
        而这个窗口一次只可能开一个（Qt.Popup 会自动关掉上一个）。
        """
        self._popup = _EquipDetailPopup(_equip_tooltip(self.equip), self)
        self._popup.move(
            self.label.mapToGlobal(self.label.rect().bottomLeft()))
        self._popup.show()


class _SlotGroup(QFrame):
    """单槽位候选区：标题 + 候选行列表。"""

    def __init__(
        self, slot_key: str, display_name: str,
        equips: list[dict], school: str,
        tuning_pairs: list[tuple[str, str]] | None = None,
        min_rating: str = _DEFAULT_MIN_RATING,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.slot_key = slot_key
        self.setProperty("surface", "card")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 7, 8, 7)
        layout.setSpacing(3)

        header = QHBoxLayout()
        title = QLabel(display_name)
        title.setStyleSheet("font-size: 13px; font-weight: 700;")
        header.addWidget(title)
        header.addStretch()
        count = QLabel(tr("{count} 件").format(count=len(equips)))
        count.setProperty("tone", "muted")
        count.setStyleSheet("font-size: 11px;")
        header.addWidget(count)
        layout.addLayout(header)

        from ...core.graduation.combo_rules import (
            judge_best_rating,
            rating_rank,
        )
        pairs = list(tuning_pairs or [])

        def rating_of(equip: dict) -> tuple[int, str]:
            """(排序档位, 展示文案)。多条玩法取最高评级——一件装备通常
            只对得上其中一两套练法，取最低档几乎筛不出装备。"""
            if not pairs:
                return 0, "-"
            verdict = judge_best_rating(equip, pairs)
            return rating_rank(verdict.label), domain_label(verdict.label)

        rated_equips = [(rating_of(equip), equip) for equip in equips]
        rated_equips.sort(key=lambda item: item[0][0], reverse=True)
        self.rows: list[_CandidateRow] = []
        for (_rank, rating), equip in rated_equips:
            row = _CandidateRow(equip, rating, self)
            self.rows.append(row)
            layout.addWidget(row)

        if not equips:
            empty = QLabel(tr("（无候选装备）"))
            empty.setStyleSheet("color: palette(mid); font-size: 12px;")
            layout.addWidget(empty)

        # 候选少时卡片仍填满网格单元，内容固定贴顶；候选多时
        # 布局的最小高度会交给外层 QScrollArea 产生独立滚动条。
        layout.addStretch()

    def get_selected(self) -> list[dict]:
        """返回勾选的装备列表。"""
        return [row.equip for row in self.rows if row.checkbox.isChecked()]


# ── 与备战方案的差异标注 ─────────────────────────────────

#: 计算假设的强调色：和装备卡片上原来那行假设文字保持同一种琥珀色。
#: 部位相对当前备战方案的变化。
#: 组合详情状态带每行高度（px）；两行固定，空行也占位
_STRIP_ROW_HEIGHT = 20

_CHANGE_SWAP = "swap"    # 备战方案穿着别的装备，要换下来
_CHANGE_NEW = "new"      # 备战方案这个部位是空的，直接穿上
_CHANGE_SAME = "same"    # 与备战方案一致
_CHANGE_NONE = "none"    # 组合里没有这个部位


def _slot_change(current: Any, proposed: Any) -> str:
    """比较组合里某部位的装备与备战方案当前穿戴，返回 ``_CHANGE_*``。

    按仓储 fp 比较而不是按对象：搜索结果里的装备是从候选池复制出来的，
    同一条记录可能是不同的 dict；fp 是仓储里“同一件”的唯一口径。
    """
    if not isinstance(proposed, dict) or not proposed:
        return _CHANGE_NONE
    if not isinstance(current, dict) or not current:
        return _CHANGE_NEW
    if current.get("_fp") and current.get("_fp") == proposed.get("_fp"):
        return _CHANGE_SAME
    return _CHANGE_SWAP


def _changed_slots(
    equipped: dict[str, Any], current_equipped: dict[str, Any],
) -> list[str]:
    """按部位顺序列出需要动手换装的部位（换下或新穿）。"""
    return [
        slot_key for slot_key, _dn, _ft in _SLOT_ORDER
        if _slot_change(current_equipped.get(slot_key), equipped.get(slot_key))
        in (_CHANGE_SWAP, _CHANGE_NEW)
    ]


def _change_pill(change: str, parent: QWidget | None = None) -> QLabel | None:
    """部位变化的胶囊；一致的部位也给一个弱化的确认，避免被误读为漏标。"""
    if change == _CHANGE_SWAP:
        return highlight_pill("⇄ " + tr("需更换"), parent)
    if change == _CHANGE_NEW:
        return highlight_pill("+ " + tr("新穿戴"), parent)
    if change == _CHANGE_SAME:
        return muted_pill("✓ " + tr("已穿戴"), parent)
    return None


class _SlotDetailPanel(QWidget):
    """组合详情页的单个部位：卡片上方一条状态带 + 装备卡片。

    状态带承担两件事：计算假设（原来挤在卡片内部第一行，占掉词条的
    空间，还容易被当成装备本身的属性）和「这个部位要不要换」。后者是
    用户看这一页的真正目的——不熟悉自己穿戴的人，只看八张卡片根本不知
    道哪几件是新的。
    """

    def __init__(
        self, slot_key: str, display_name: str, filter_type: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        from .equip.cards import _SlotCard

        self.slot_key = slot_key
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        # 状态带固定两行：第一行换装状态，第二行计算假设。两行都固定
        # 高度、空着也占位——否则假设多的部位纵向撑高，八张卡片对不齐。
        self.strip = QWidget()
        strip_layout = QVBoxLayout(self.strip)
        strip_layout.setContentsMargins(2, 0, 2, 0)
        strip_layout.setSpacing(2)
        change_row = QHBoxLayout()
        change_row.setContentsMargins(0, 0, 0, 0)
        change_row.setSpacing(6)
        self.change_slot = QHBoxLayout()
        self.change_slot.setContentsMargins(0, 0, 0, 0)
        self.change_slot.setSpacing(6)
        change_row.addLayout(self.change_slot)
        self.current_label = QLabel("")
        self.current_label.setProperty("tone", "muted")
        self.current_label.setStyleSheet("font-size: 11px;")
        self.current_label.setVisible(False)
        change_row.addWidget(self.current_label)
        change_row.addStretch(1)
        strip_layout.addLayout(change_row)
        assumption_row = QHBoxLayout()
        assumption_row.setContentsMargins(0, 0, 0, 0)
        assumption_row.setSpacing(4)
        self.assumption_slot = QHBoxLayout()
        self.assumption_slot.setContentsMargins(0, 0, 0, 0)
        self.assumption_slot.setSpacing(4)
        assumption_row.addLayout(self.assumption_slot)
        assumption_row.addStretch(1)
        strip_layout.addLayout(assumption_row)
        self.strip.setFixedHeight(_STRIP_ROW_HEIGHT * 2 + 2)
        layout.addWidget(self.strip)

        # 这一页只看不点：只读卡片不响应点击/悬停，也不弹右键菜单
        self.card = _SlotCard(
            slot_key, display_name, filter_type,
            display_params={"card_min_height": 180},
            read_only=True,
        )
        layout.addWidget(self.card)

        self.change = _CHANGE_NONE
        self.assumptions: list[str] = []

    @staticmethod
    def _clear(layout: QHBoxLayout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.deleteLater()

    def show_equip(
        self, equip: dict, assumptions: list[str], current: Any,
    ) -> None:
        self.card.set_equip(equip)
        self.change = _slot_change(current, equip)
        self.assumptions = [str(a) for a in assumptions if str(a)]

        self._clear(self.change_slot)
        pill = _change_pill(self.change, self.strip)
        if pill is not None:
            self.change_slot.addWidget(pill)
        if self.change == _CHANGE_SWAP and isinstance(current, dict):
            self.current_label.setText(
                tr("当前：{name}").format(name=current.get("name", "?")))
            self.current_label.setToolTip(
                tr("备战方案此部位现在穿的装备，应用组合后会被换下"))
            self.current_label.setVisible(True)
        else:
            self.current_label.setText("")
            self.current_label.setVisible(False)

        self._clear(self.assumption_slot)
        for text in self.assumptions:
            self.assumption_slot.addWidget(assumption_pill(text, self.strip))
        self.strip.setToolTip(
            tr("计算假设：") + "、".join(self.assumptions)
            if self.assumptions else "")

        self.card.set_attention(self.change in (_CHANGE_SWAP, _CHANGE_NEW))

    def show_empty(self) -> None:
        self.card.set_attention(False)
        self.card.set_empty()
        self.change = _CHANGE_NONE
        self.assumptions = []
        self._clear(self.change_slot)
        self._clear(self.assumption_slot)
        self.current_label.setText("")
        self.current_label.setVisible(False)
        self.strip.setToolTip("")


class _ResultCard(QFrame):
    """单条搜索结果卡片。"""

    apply_clicked = pyqtSignal(dict)   # emits equipped dict
    view_clicked = pyqtSignal(dict)    # emits complete result metadata

    def __init__(
        self, rank: int, result: dict[str, Any],
        slot_labels: dict[str, str],
        current_equipped: dict[str, Any] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setProperty("surface", "card")
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        self.result = result
        current_equipped = current_equipped or {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(7)

        # Top row: rank + rate + DPS + apply button
        top = QHBoxLayout()
        top.setSpacing(12)

        rank_label = QLabel(f"#{rank}")
        rank_label.setStyleSheet("font-weight: 700; font-size: 14px;")
        top.addWidget(rank_label)

        rate = result.get("rate", 0)
        rate_label = QLabel(f"{rate * 100:.2f}%")
        rate_label.setStyleSheet(
            "font-weight: 700; font-size: 17px; color: palette(highlight);")
        top.addWidget(rate_label)

        dps = result.get("dps", 0)
        dps_label = QLabel(f"DPS {dps:,.0f}")
        dps_label.setStyleSheet("font-size: 13px; color: palette(mid);")
        top.addWidget(dps_label)

        gongjue = str(result.get("gongjue") or tr("无"))
        gongjue_label = QLabel(
            tr("弓玦套装：{name}").format(name=gongjue))
        gongjue_label.setStyleSheet("font-size: 12px; color: palette(mid);")
        top.addWidget(gongjue_label)

        # 计算假设放在首行：它们决定了上面那个毕业率是在什么前提下算出来的，
        # 放在装备列表下面会先看到结论再看到前提。
        assumptions = result.get("assumptions", {})
        assumption_texts: list[str] = []
        if isinstance(assumptions, dict):
            for slot_key, _dn, _ft in _SLOT_ORDER:
                for label in assumptions.get(slot_key) or []:
                    text = str(label)
                    if text and text not in assumption_texts:
                        assumption_texts.append(text)
        self.assumption_pills: list[QLabel] = []
        for text in assumption_texts:
            pill = assumption_pill(text, self)
            pill.setToolTip(tr("计算假设：{text}").format(text=text))
            self.assumption_pills.append(pill)
            top.addWidget(pill, 0, Qt.AlignmentFlag.AlignVCenter)

        top.addStretch()

        # 两个动作同一行：应用，或一次刷新组合详情和战斗属性两个预览页。
        buttons = QHBoxLayout()
        buttons.setSpacing(6)
        apply_btn = QPushButton(tr("应用此组合"))
        apply_btn.setObjectName("resultApplyButton")
        apply_button_style(apply_btn, variant="action")
        apply_btn.clicked.connect(
            lambda: self.apply_clicked.emit(result.get("equipped", {})),
        )
        buttons.addWidget(apply_btn)
        # 一行装备名看不出这套组合到底是什么，真要判断得看词条
        view_btn = QPushButton(tr("查看该组合"))
        view_btn.setObjectName("resultViewButton")
        apply_button_style(view_btn, variant="neutral")
        view_btn.clicked.connect(
            lambda: self.view_clicked.emit(result),
        )
        buttons.addWidget(view_btn)
        top.addLayout(buttons)
        layout.addLayout(top)

        # 装备列表：一个部位一个胶囊。相对备战方案要换的部位用强调色，
        # 前面再给一个汇总数——用户扫一眼就知道这套组合要动几件、动哪几件。
        equipped = result.get("equipped", {})
        self.changed_slots = _changed_slots(equipped, current_equipped)
        chips_host = QWidget()
        chips = FlowLayout(chips_host, spacing=6)
        chips.setContentsMargins(0, 0, 0, 0)
        if self.changed_slots:
            names = "、".join(
                slot_labels.get(k, k) for k in self.changed_slots)
            self.change_summary = highlight_pill(
                tr("需更换 {count} 件").format(count=len(self.changed_slots)),
                chips_host)
            self.change_summary.setToolTip(tr("需要更换：{names}").format(names=names))
        else:
            self.change_summary = muted_pill(
                "✓ " + tr("与备战方案一致"), chips_host)
        chips.addWidget(self.change_summary)
        self.slot_chips: dict[str, QLabel] = {}
        for slot_key, _dn, _ft in _SLOT_ORDER:
            eq = equipped.get(slot_key)
            if not eq:
                continue
            label = slot_labels.get(slot_key, slot_key)
            name = eq.get("name", "?")
            change = _slot_change(current_equipped.get(slot_key), eq)
            if change in (_CHANGE_SWAP, _CHANGE_NEW):
                chip = make_pill(
                    f"⇄ {label} · {name}",
                    "palette(highlight)", "palette(alternate-base)", chips_host)
                chip.setStyleSheet(
                    chip.styleSheet() + " border: 1px solid palette(highlight);")
                current = current_equipped.get(slot_key)
                if change == _CHANGE_SWAP and isinstance(current, dict):
                    chip.setToolTip(tr("换下：{name}").format(
                        name=current.get("name", "?")))
                else:
                    chip.setToolTip(tr("备战方案此部位当前为空"))
            else:
                chip = make_pill(
                    f"{label} · {name}",
                    "palette(mid)", "palette(alternate-base)", chips_host)
                chip.setStyleSheet(chip.styleSheet() + " font-weight: 400;")
            self.slot_chips[slot_key] = chip
            chips.addWidget(chip)
        layout.addWidget(chips_host)


# ---------------------------------------------------------------------------
# Main dialog
# ---------------------------------------------------------------------------

class OptimalComboPage(QWidget):
    """最优毕业率装备组合搜索页（毕业率分析对话框的一个页签）。

    计算假设由对话框的共享假设栏提供（``assumptions_provider``），本页只
    保留候选筛选与搜索空间选项。
    """

    #: 搜索结果变化（完成或清空），供对话框写入按用户+方案隔离的缓存
    results_changed = pyqtSignal(list)

    def __init__(
        self,
        host: Any,
        school: str,
        scheme: str,
        base_attrs: CombatAttributes,
        level_threshold: int = 0,
        affix_filter: str = "all",
        gongjue: str = "",
        playstyle: str = "",
        main_martial_art: str = "",
        sub_martial_art: str = "",
        parent: QWidget | None = None,
        *,
        assumptions_provider: Callable[[], Assumptions] | None = None,
        analysis_settings: dict[str, Any] | None = None,
        settings_changed: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self._host = host
        self._assumptions_provider = assumptions_provider or Assumptions
        self._settings_changed = settings_changed
        self._restoring_settings = False
        self._results: list[dict[str, Any]] = []
        self._school = school
        self._scheme = scheme
        # base_attrs 不含弓玦，弓玦属性按需计算
        self._base_attrs_raw = base_attrs
        self._current_gongjue = gongjue
        self._playstyle = playstyle
        self._main_martial_art = main_martial_art
        self._sub_martial_art = sub_martial_art
        self._level_threshold = level_threshold
        self._affix_filter = affix_filter
        self._jobs = JobController(self, poll_interval_ms=1000)
        self._jobs.finished.connect(self._on_finished)
        self._jobs.failed.connect(self._on_error)
        self._jobs.cancelled.connect(self._on_cancelled)
        self._jobs.progress.connect(self._on_progress)
        self._slot_groups: dict[str, _SlotGroup] = {}
        self._result_cards: list[_ResultCard] = []
        #: 备战方案当前穿戴（slot_key → 装备），结果页据此标出要换的部位
        self._current_equipped: dict[str, Any] = {}
        self._slot_labels: dict[str, str] = {
            slot_key: display_name for slot_key, display_name, _ft in _SLOT_ORDER
        }

        self._setup_ui()
        self._restore_analysis_settings(analysis_settings or {})
        self._load_candidates()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        filter_settings = QFrame()
        filter_settings.setProperty("surface", "card")
        filter_layout = QVBoxLayout(filter_settings)
        filter_layout.setContentsMargins(14, 10, 14, 10)
        filter_layout.setSpacing(8)
        filter_title = QLabel(tr("筛选设置"))
        filter_title.setStyleSheet("font-size: 14px; font-weight: 700;")
        filter_layout.addWidget(filter_title)

        filter_row = QHBoxLayout()
        filter_row.setSpacing(9)
        self._chk_exclude_mock = QCheckBox(tr("排除模拟装备"))
        self._chk_exclude_mock.setChecked(True)
        self._chk_exclude_mock.setToolTip(
            tr("搜索时排除模拟装备，仅使用真实背包和已穿戴装备"))
        # 其余选项都在开始搜索时才读，唯独本项决定候选池内容，
        # 必须当场重建——否则改了也只在下次打开对话框才生效。
        self._chk_exclude_mock.toggled.connect(self._on_exclude_mock_toggled)
        filter_row.addWidget(self._chk_exclude_mock)
        self._chk_apply_tuning = QCheckBox(tr("应用调律规则"))
        self._chk_apply_tuning.setChecked(True)
        self._chk_apply_tuning.toggled.connect(self._on_apply_tuning_toggled)
        filter_row.addWidget(self._chk_apply_tuning)
        tuning_label = QLabel(tr("候选评级"))
        tuning_label.setProperty("tone", "muted")
        tuning_label.setToolTip(tr("仅用于辅助筛选候选装备，不参与装备合法性判断"))
        filter_row.addWidget(tuning_label)
        self._edit_tuning = _ClickableLineEdit()
        self._edit_tuning.setPlaceholderText(tr("点击选择玩法（不选则不应用规则）"))
        self._edit_tuning.setToolTip(
            tr("玩法评级只辅助勾选候选，不作为装备合法性规则；"
               "多选时按各条规则给出的最高评级判定"))
        self._edit_tuning.clicked.connect(self._on_pick_playstyles)
        filter_row.addWidget(self._edit_tuning, 1)

        rating_label = QLabel(tr("评级 ≥"))
        rating_label.setProperty("tone", "muted")
        rating_label.setToolTip(
            tr("装备需至少有一条已选玩法给出该级别及以上的评级；不选玩法时不生效"))
        filter_row.addWidget(rating_label)
        self._combo_min_rating = QComboBox()
        for rating in _MIN_RATING_CHOICES:
            self._combo_min_rating.addItem(domain_label(rating), rating)
        index = self._combo_min_rating.findData(_DEFAULT_MIN_RATING)
        if index >= 0:
            self._combo_min_rating.setCurrentIndex(index)
        fit_combo_to_contents(self._combo_min_rating, minimum=88)
        self._combo_min_rating.currentIndexChanged.connect(
            self._on_min_rating_changed)
        filter_row.addWidget(self._combo_min_rating)
        filter_layout.addLayout(filter_row)
        self._load_tuning_options()
        layout.addWidget(filter_settings)

        compute_settings = QFrame()
        compute_settings.setProperty("surface", "card")
        compute_layout = QVBoxLayout(compute_settings)
        compute_layout.setContentsMargins(14, 10, 14, 10)
        compute_layout.setSpacing(8)
        compute_title = QLabel(tr("计算设置"))
        compute_title.setStyleSheet("font-size: 14px; font-weight: 700;")
        compute_layout.addWidget(compute_title)

        compute_row = QHBoxLayout()
        compute_row.setSpacing(14)
        self._chk_pruning = QCheckBox(tr("智能分析"))
        self._chk_pruning.setChecked(True)
        self._chk_pruning.setToolTip(
            tr("自动淘汰被其他候选完全压制的装备，缩减搜索空间"))
        self._chk_pruning.toggled.connect(self._persist_analysis_settings)
        compute_row.addWidget(self._chk_pruning)
        # 搜索空间选项而非投影假设：原装备保留，额外派生同等级承音分支
        self._chk_season_chengyin = QCheckBox(tr("赛季装备假设承音"))
        self._chk_season_chengyin.setToolTip(tr(
            "为本赛季等级的原生装备额外创建同等级承音分支；"
            "只将普通词条拉到承音上限，定音保持原值"))
        self._chk_season_chengyin.toggled.connect(
            self._persist_analysis_settings)
        compute_row.addWidget(self._chk_season_chengyin)

        gongjue_label = QLabel(tr("弓玦套装"))
        gongjue_label.setProperty("tone", "muted")
        compute_row.addWidget(gongjue_label)
        self._btn_gongjue = QPushButton()
        self._gongjue_menu = _MultiSelectMenu(self._btn_gongjue)
        self._gongjue_actions = {}
        for gongjue_type in ("会意", "精准", "会心"):
            action = self._gongjue_menu.addAction(gongjue_type)
            assert action is not None
            action.setCheckable(True)
            action.setChecked(gongjue_type == self._current_gongjue)
            action.toggled.connect(self._refresh_gongjue_text)
            self._gongjue_actions[gongjue_type] = action
        self._btn_gongjue.setMenu(self._gongjue_menu)
        self._btn_gongjue.setMinimumWidth(132)
        apply_compact_button_style(self._btn_gongjue, variant="neutral")
        self._refresh_gongjue_text()
        compute_row.addWidget(self._btn_gongjue)
        self._btn_gongjue_all = QPushButton(tr("全选"))
        self._btn_gongjue_all.setObjectName("selectAllGongjueButton")
        self._btn_gongjue_all.setFlat(True)
        self._btn_gongjue_all.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_gongjue_all.setStyleSheet(
            "QPushButton { border: none; background: transparent;"
            " color: palette(link); text-decoration: underline; padding: 0; }"
            "QPushButton:disabled { color: palette(mid); }")
        self._btn_gongjue_all.clicked.connect(self._select_all_gongjues)
        compute_row.addWidget(self._btn_gongjue_all)
        compute_row.addStretch()
        compute_layout.addLayout(compute_row)
        layout.addWidget(compute_settings)

        status_card = QFrame()
        status_card.setProperty("status", "info")
        status_layout = QHBoxLayout(status_card)
        status_layout.setContentsMargins(12, 8, 12, 8)
        status_layout.setSpacing(9)
        self._candidate_summary = QLabel(tr("正在读取候选装备…"))
        self._candidate_summary.setWordWrap(True)
        self._candidate_summary.setStyleSheet("font-size: 12px;")
        status_layout.addWidget(self._candidate_summary, 1)

        action_row = QHBoxLayout()
        action_row.setSpacing(8)
        self._btn_clear_results = QPushButton(tr("清除结果"))
        apply_button_style(self._btn_clear_results, variant="neutral")
        self._btn_clear_results.setVisible(False)
        self._btn_clear_results.clicked.connect(self._on_clear_results)
        action_row.addWidget(self._btn_clear_results)

        self._btn_search = QPushButton(tr("开始搜索"))
        apply_button_style(self._btn_search, variant="action")
        self._btn_search.clicked.connect(self._on_search)
        action_row.addWidget(self._btn_search)

        self._btn_cancel = QPushButton(tr("取消"))
        apply_button_style(self._btn_cancel, variant="neutral")
        self._btn_cancel.setVisible(False)
        self._btn_cancel.clicked.connect(self._on_cancel)
        action_row.addWidget(self._btn_cancel)

        self._progress = QProgressBar()
        self._progress.setVisible(False)
        self._progress.setFixedWidth(200)
        action_row.addWidget(self._progress)

        self._progress_label = QLabel("")
        self._progress_label.setStyleSheet("font-size: 12px; color: palette(mid);")
        self._progress_label.setVisible(False)
        action_row.addWidget(self._progress_label)

        status_layout.addLayout(action_row)
        layout.addWidget(status_card)

        # Tab widget: 候选装备 / 最优结果
        self._tab_widget = QTabWidget()
        style_document_tabs(self._tab_widget, "optimalComboTabs")

        # Tab 1: 候选装备 (4×2 grid)
        candidates_tab = QWidget()
        candidates_layout = QVBoxLayout(candidates_tab)
        candidates_layout.setContentsMargins(8, 8, 8, 8)
        grid = QGridLayout()
        grid.setSpacing(8)
        self._slot_scroll_areas: dict[str, QScrollArea] = {}
        for idx, (slot_key, _display_name, _ft) in enumerate(_SLOT_ORDER):
            row = idx // 4
            col = idx % 4
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            # 候选数量不参与外层 2×4 网格的尺寸计算：八个区域
            # 始终等高填满，每个区域内部再按需滚动。
            scroll.setSizeAdjustPolicy(
                QAbstractScrollArea.SizeAdjustPolicy.AdjustIgnored)
            scroll.setSizePolicy(
                QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
            scroll.setHorizontalScrollBarPolicy(
                Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            scroll.setVerticalScrollBarPolicy(
                Qt.ScrollBarPolicy.ScrollBarAsNeeded)
            scroll.setFrameShape(QFrame.Shape.NoFrame)
            grid.addWidget(scroll, row, col)
            self._slot_scroll_areas[slot_key] = scroll
        for c in range(4):
            grid.setColumnStretch(c, 1)
        for r in range(2):
            grid.setRowStretch(r, 1)
        candidates_layout.addLayout(grid, 1)
        self._tab_widget.addTab(candidates_tab, tr("候选装备"))

        # Tab 2: 最优结果
        results_tab = QWidget()
        results_layout = QVBoxLayout(results_tab)
        results_layout.setContentsMargins(0, 0, 0, 0)
        self._results_scroll = QScrollArea()
        self._results_scroll.setWidgetResizable(True)
        self._results_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._results_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._results_scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        results_container = QWidget()
        self._results_inner = QVBoxLayout()
        self._results_inner.setContentsMargins(8, 8, 8, 8)
        self._results_inner.setSpacing(8)
        self._results_inner.setSizeConstraint(
            QLayout.SizeConstraint.SetMinimumSize)
        self._results_inner.setAlignment(Qt.AlignmentFlag.AlignTop)
        results_container.setLayout(self._results_inner)
        self._results_scroll.setWidget(results_container)
        results_layout.addWidget(self._results_scroll)
        self._tab_widget.addTab(results_tab, tr("最优结果"))

        # Tab 3: 组合详情（4×2 装备卡片，复用穿戴装备那套卡片）
        detail_tab = QWidget()
        detail_layout = QVBoxLayout(detail_tab)
        detail_layout.setContentsMargins(8, 8, 8, 8)
        detail_layout.setSpacing(6)
        self._detail_hint = QLabel(tr("在「最优结果」里点某一条的「查看该组合」"))
        self._detail_hint.setProperty("tone", "muted")
        self._detail_hint.setStyleSheet("font-size: 12px;")
        detail_layout.addWidget(self._detail_hint)
        detail_grid = QGridLayout()
        detail_grid.setSpacing(8)
        self._detail_panels: dict[str, _SlotDetailPanel] = {}
        self._detail_cards: dict[str, Any] = {}
        for index, (slot_key, display_name, filter_type) in enumerate(_SLOT_ORDER):
            panel = _SlotDetailPanel(slot_key, display_name, filter_type)
            detail_grid.addWidget(panel, index // 4, index % 4)
            self._detail_panels[slot_key] = panel
            self._detail_cards[slot_key] = panel.card
        for column in range(4):
            detail_grid.setColumnStretch(column, 1)
        detail_layout.addLayout(detail_grid)
        detail_layout.addStretch()
        self._tab_widget.addTab(detail_tab, tr("组合详情"))

        # Tab 4: 战斗属性（复用角色详情的战斗属性面板，全屏 2×2 排布）
        attrs_tab = QWidget()
        attrs_layout = QVBoxLayout(attrs_tab)
        attrs_layout.setContentsMargins(8, 8, 8, 8)
        attrs_layout.setSpacing(6)
        self._attrs_hint = QLabel(tr("在「最优结果」里点某一条的「查看该组合」"))
        self._attrs_hint.setProperty("tone", "muted")
        self._attrs_hint.setStyleSheet("font-size: 12px;")
        attrs_layout.addWidget(self._attrs_hint)
        self._attrs_preview = self._make_attrs_preview()
        if self._attrs_preview is not None:
            attrs_layout.addWidget(self._attrs_preview, 1)
        self._tab_widget.addTab(attrs_tab, tr("战斗属性"))

        layout.addWidget(self._tab_widget, stretch=1)

    def _make_attrs_preview(self):
        """战斗属性预览面板；宿主不可用（测试桩）时留空。"""
        try:
            from .combat.attrs_tab import CombatAttrsTab

            preview = CombatAttrsTab(self._host, preview=True)
            preview.set_embedded_mode("full")
            return preview
        except Exception as exc:  # noqa: BLE001 - 预览不可用不影响搜索
            logger.warning(f"战斗属性预览面板不可用: {exc}")
            return None

    def _preview_equipped_for(self, result: dict) -> dict[str, dict]:
        """按搜索时的假设把这套组合投影成算分用的虚拟装备。

        赛季承音分支是逐件选择的：只有结果里标了「同等级承音假设」的部位
        才按承音投影，其余部位用搜索时的共享假设。
        """
        equipped = result.get("equipped", {})
        per_slot = result.get("assumptions", {})
        base = getattr(self, "_searched_assumptions", None) or (
            self._assumptions_provider().with_playstyle(self._playstyle))
        projected: dict[str, dict] = {}
        for slot_key, equip in equipped.items():
            if not isinstance(equip, dict):
                continue
            labels = per_slot.get(slot_key) or [] if isinstance(per_slot, dict) else []
            assumptions = replace(
                base, season_chengyin="同等级承音假设" in labels)
            projected[slot_key] = assumptions.project({slot_key: equip})[slot_key]
        return projected

    def _on_show_attrs(self, result: dict, *, activate: bool = True) -> None:
        """把这套组合的战斗属性铺到「战斗属性」页。"""
        if self._attrs_preview is None:
            return
        gongjue = str(result.get("gongjue") or "")
        self._attrs_preview.show_preview(
            self._preview_equipped_for(result), gongjue=gongjue)
        rate = result.get("rate", 0)
        self._attrs_hint.setText(
            tr("方案 #{rank}　弓玦套装：{gongjue}　毕业率 {rate:.2f}%　·　"
               "属性按搜索时的计算假设与该弓玦套装计算，不是穿戴后的实测值")
            .format(rank=result.get("rank") or "-", gongjue=gongjue or tr("无"),
                    rate=rate * 100))
        self._tab_widget.setTabText(3, self._ranked_title(tr("战斗属性"), result))
        if activate:
            self._tab_widget.setCurrentIndex(3)

    def _selected_gongjues(self) -> list[str]:
        """返回选中的弓玦场景；全不选表示按无弓玦计算。"""
        return [
            name for name, action in self._gongjue_actions.items()
            if action.isChecked()
        ]

    def _refresh_gongjue_text(self, _checked: bool = False) -> None:
        selected = self._selected_gongjues()
        self._btn_gongjue.setText(" / ".join(selected) if selected else tr("无"))

    def _select_all_gongjues(self) -> None:
        for action in self._gongjue_actions.values():
            action.setChecked(True)

    def _load_tuning_options(self) -> None:
        """收集全部规则玩法，按本方案、本流派、本属性分层排序。

        三层相关玩法全部默认参与候选评级；其他玩法仍然列出，供用户手动
        扩大范围。层级直接使用公共玩法注册表中的完整 school/attr，不能再用
        武器集合猜测，否则主副武器相反的牵丝·霖和牵丝·玉会被混为一类。
        """
        from ...config import get_game_config
        from ...core.evaluator import get_tuning_rules

        game_config = get_game_config()
        school_cfg = game_config.get_schools().get(self._school, {})
        school_attr = school_cfg.get("attr", "")
        registry = game_config.get_playstyles()

        groups: dict[str, list[tuple[str, str, str, str]]] = {
            "plan": [], "school": [], "attr": [], "": [],
        }
        for key, rule in get_tuning_rules().items():
            for name in rule.playstyles:
                scope = _playstyle_match_scope(
                    name, registry.get(name, {}),
                    current_playstyle=self._playstyle,
                    current_school=self._school,
                    current_attr=school_attr,
                )
                groups[scope].append(
                    (key, name, f"{rule.name}-{name}", scope))
        self._tuning_options = [
            entry
            for scope in ("plan", "school", "attr", "")
            for entry in groups[scope]
        ]
        # 三层相关玩法全部默认勾选，其他属性玩法只展示、由用户按需选择。
        self._tuning_selection = [
            (key, name)
            for key, name, _label, scope in self._tuning_options
            if scope
        ]
        self._refresh_tuning_display()

    def _refresh_tuning_display(self) -> None:
        """把选中的玩法用「/」拼进展示框。"""
        labels = {
            (key, name): label
            for key, name, label, _hit in self._tuning_options
        }
        text = " / ".join(
            labels.get(pair, f"{pair[0]}-{pair[1]}")
            for pair in self._tuning_selection
        )
        self._edit_tuning.setText(text)
        self._edit_tuning.setToolTip(text or tr("未选择玩法，不应用规则"))

    def _on_pick_playstyles(self) -> None:
        dialog = _PlaystylePickerDialog(
            self._tuning_options, set(self._tuning_selection), self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self._tuning_selection = dialog.values()
        self._refresh_tuning_display()
        self._on_tuning_changed()
        self._persist_analysis_settings()

    def _effective_tuning_selection(self) -> list[tuple[str, str]]:
        return list(self._tuning_selection) if self._chk_apply_tuning.isChecked() else []

    def _on_apply_tuning_toggled(self, checked: bool) -> None:
        self._edit_tuning.setEnabled(checked)
        self._combo_min_rating.setEnabled(checked)
        self._on_tuning_changed()
        self._persist_analysis_settings()

    def _on_min_rating_changed(self, _index: int) -> None:
        self._on_tuning_changed()
        self._persist_analysis_settings()

    def _min_rating(self) -> str:
        return str(self._combo_min_rating.currentData() or _DEFAULT_MIN_RATING)

    def _on_tuning_changed(self) -> None:
        """规则或评级要求变化时，前置过滤装备勾选状态。"""
        from ...core.graduation.combo_rules import judge_best_rating

        pairs = self._effective_tuning_selection()
        if not pairs:
            # 没选玩法：不应用规则，全部勾选
            for group in self._slot_groups.values():
                for row in group.rows:
                    row.checkbox.setChecked(True)
                    row.checkbox.setVisible(True)
                    row.set_rating("-")
            return
        minimum = self._min_rating()
        for group in self._slot_groups.values():
            for row in group.rows:
                verdict = judge_best_rating(row.equip, pairs)
                # 一条规则都没给出评级 = 垃圾：skipped 的实际含义就是品阶
                # 无调律价值或没有词条数据，那正是垃圾胚子。
                row.checkbox.setChecked(verdict.meets(minimum))
                row.checkbox.setVisible(True)
                row.set_rating(domain_label(verdict.label))

    def _on_exclude_mock_toggled(self, _checked: bool) -> None:
        """「排除模拟」变化后重建候选池。

        候选池只在对话框构造时加载一次，而本项默认勾选，用户没有机会
        在加载前取消——不当场重建的话这个开关等于没有。
        """
        if self._restoring_settings:
            return
        self._persist_analysis_settings()
        self._load_candidates()

    def _analysis_settings(self) -> dict[str, Any]:
        """返回最优组合页自身选项，不包含调用方传入的假设和弓玦。"""
        return {
            "apply_tuning_rules": self._chk_apply_tuning.isChecked(),
            "candidate_rating_rules": [
                [rule_key, playstyle]
                for rule_key, playstyle in self._tuning_selection
            ],
            "minimum_rating": self._min_rating(),
            "exclude_mock": self._chk_exclude_mock.isChecked(),
            "smart_analysis": self._chk_pruning.isChecked(),
            "season_chengyin": self._chk_season_chengyin.isChecked(),
        }

    def _persist_analysis_settings(self, _value: object = None) -> None:
        if self._restoring_settings or self._settings_changed is None:
            return
        self._settings_changed(self._analysis_settings())

    def _restore_analysis_settings(self, settings: dict[str, Any]) -> None:
        """回填当前备战方案的最优组合选项。"""
        if not settings:
            return
        valid_pairs = {
            (rule_key, playstyle)
            for rule_key, playstyle, _label, _scope in self._tuning_options
        }
        raw_pairs = settings.get("candidate_rating_rules")
        selected: list[tuple[str, str]] = []
        if isinstance(raw_pairs, list):
            for pair in raw_pairs:
                if not isinstance(pair, (list, tuple)) or len(pair) != 2:
                    continue
                candidate = (str(pair[0]), str(pair[1]))
                if candidate in valid_pairs:
                    selected.append(candidate)

        controls = (
            self._chk_apply_tuning,
            self._chk_exclude_mock,
            self._combo_min_rating,
            self._chk_pruning,
            self._chk_season_chengyin,
        )
        self._restoring_settings = True
        for control in controls:
            control.blockSignals(True)
        try:
            if isinstance(raw_pairs, list):
                self._tuning_selection = selected
            self._chk_apply_tuning.setChecked(
                bool(settings.get("apply_tuning_rules", True)))
            self._chk_exclude_mock.setChecked(
                bool(settings.get("exclude_mock", True)))
            rating_index = self._combo_min_rating.findData(
                settings.get("minimum_rating"))
            if rating_index >= 0:
                self._combo_min_rating.setCurrentIndex(rating_index)
            self._chk_pruning.setChecked(
                bool(settings.get("smart_analysis", True)))
            self._chk_season_chengyin.setChecked(
                bool(settings.get("season_chengyin", False)))
        finally:
            for control in controls:
                control.blockSignals(False)
            self._restoring_settings = False
        enabled = self._chk_apply_tuning.isChecked()
        self._edit_tuning.setEnabled(enabled)
        self._combo_min_rating.setEnabled(enabled)
        self._refresh_tuning_display()

    def _load_candidates(self) -> None:
        """从 session 加载候选装备并按槽位分组。

        数据来源：equipped（已穿戴）+ bag_items（背包真实装备），
        未勾选「排除模拟」时再并入 mock_items（模拟装备）。
        ``bag_items`` 本身就只含真实装备，模拟装备另存于 ``mock_items``，
        所以不并进来的话「排除模拟」这个开关无论勾不勾都没有模拟装备可用。

        武器类型过滤：两个武器槽分别按照方案当前位置上的武学派生类型，
        与流派配置中武学的声明顺序无关。
        """
        user_name = self._host.active_user_name()
        if not user_name:
            return

        try:
            from ...core.combat.equipment import EquipmentInventory
            inv = EquipmentInventory(user_name)
        except Exception as e:
            logger.error(f"加载装备数据失败: {e}")
            return

        equipped = inv.equipped
        self._current_equipped = {
            slot_key: eq for slot_key, eq in equipped.items()
            if isinstance(eq, dict) and eq
        }

        # 主副槽位取决于方案中两门武学的当前位置，不能使用流派配置的顺序。
        from ...config import get_game_config
        gc = get_game_config()
        main_weapon_type = gc.get_martial_art_weapon(self._main_martial_art)
        sub_weapon_type = gc.get_martial_art_weapon(self._sub_martial_art)
        logger.debug(
            f"流派 {self._school}: 主武器={main_weapon_type}, 副武器={sub_weapon_type}")

        # 候选池的收集与去重是领域逻辑，见 core.graduation.candidate_pool
        exclude_mock = self._chk_exclude_mock.isChecked()
        pooled = collect_candidates(
            inv.bag_items, None if exclude_mock else inv.mock_items,
            main_weapon_type=main_weapon_type, sub_weapon_type=sub_weapon_type,
            filters=CandidateFilter(self._level_threshold, self._affix_filter),
        )

        # Build UI groups
        slot_labels = {}
        total_candidates = 0
        available_slots = 0
        for slot_key, display_name, _ in _SLOT_ORDER:
            slot_labels[slot_key] = display_name
            unique = pooled.get(slot_key, [])
            group = _SlotGroup(
                slot_key, display_name, unique, self._school,
                self._effective_tuning_selection(), self._min_rating(),
            )
            self._slot_groups[slot_key] = group
            total_candidates += len(unique)
            if unique:
                available_slots += 1
            scroll = self._slot_scroll_areas.get(slot_key)
            if scroll:
                # group 本身填满可见区，它的布局末尾有 stretch，
                # 因此空间会留在底部，候选行始终顶部对齐。
                scroll.setWidget(group)

        self._slot_labels = slot_labels
        self._candidate_summary.setText(
            tr("已载入 {count} 件候选装备，覆盖 {slots}/8 个部位。"
               "勾选参与搜索的装备后，系统会重新计算整套毕业率。").format(
                   count=total_candidates, slots=available_slots,
               )
        )
        self._tab_widget.setTabText(
            0, tr("候选装备  {count}").format(count=total_candidates),
        )
        # 默认就选中了本流派的玩法，勾选状态必须当场按它过一遍——否则
        # 展示框里写着规则，候选却是全勾的，两边对不上。
        self._on_tuning_changed()

    def _on_search(self) -> None:
        """启动搜索。"""
        # Collect selected candidates per slot
        candidates: dict[str, list[dict]] = {}
        total = 1
        for slot_key, _dn, _ft in _SLOT_ORDER:
            group = self._slot_groups.get(slot_key)
            if not group:
                continue
            selected = group.get_selected()
            if selected:
                candidates[slot_key] = selected
                total *= len(selected)

        missing = [display_name for slot_key, display_name, _ft in _SLOT_ORDER
                   if not candidates.get(slot_key)]
        if missing:
            QMessageBox.warning(
                self, tr("无法搜索"),
                tr("以下部位没有候选装备：") + "、".join(missing))
            return

        gongjues = self._selected_gongjues() or [""]
        total *= len(gongjues)

        # UI state
        self._btn_clear_results.setVisible(False)
        self._btn_search.setVisible(False)
        self._btn_cancel.setVisible(True)
        self._set_search_controls_enabled(False)
        self._progress.setVisible(True)
        self._progress_label.setVisible(True)
        self._progress.setMaximum(max(total, 1))
        self._progress.setValue(0)
        self._progress_label.setText(f"0 / {total:,}")
        self._candidate_summary.setText(
            tr("正在比较 {count} 种装备组合，搜索期间仍可取消。")
            .format(count=f"{total:,}"),
        )
        # 切回候选装备 Tab，重置结果/详情/属性 Tab 标题
        self._tab_widget.setCurrentIndex(0)
        self._tab_widget.setTabText(1, tr("最优结果"))
        self._tab_widget.setTabText(2, tr("组合详情"))
        if self._tab_widget.count() > 3:
            self._tab_widget.setTabText(3, tr("战斗属性"))

        # Clear old rendered results.  self._results 留到搜索成功后再覆盖，
        # 取消或失败时仍可以恢复上一次结果的操作按钮。
        self._clear_rendered_results()

        # Launch worker
        from ...config import get_game_config
        gc = get_game_config()
        season_level = gc.current_equip_level()
        scenarios = [
            (name, self._base_attrs_raw + gongjue_attrs(name))
            for name in gongjues
        ]
        # 假设在点击时定格：搜索期间改动假设栏不影响本次结果；赛季承音是
        # 本页的搜索空间选项，与共享假设合成后一起交给搜索
        assumptions = replace(
            self._assumptions_provider().with_playstyle(self._playstyle),
            season_chengyin=self._chk_season_chengyin.isChecked())
        self._searched_assumptions = assumptions
        self._jobs.start(_search_job(
            candidates,
            self._school,
            self._scheme,
            scenarios,
            self._chk_pruning.isChecked(),
            assumptions,
            season_level,
        ))

    def _on_cancel(self) -> None:
        self._jobs.cancel()

    def _on_cancelled(self) -> None:
        """取消：复位界面，保留上一次完成的结果（不渲染半截结果）。"""
        self._restore_idle_controls()
        self._candidate_summary.setText(
            tr("已取消本次搜索；上一次的结果保留。") if self._results
            else tr("已取消本次搜索。"))

    def _restore_idle_controls(self) -> None:
        self._btn_clear_results.setVisible(bool(self._results))
        self._btn_search.setVisible(True)
        self._btn_cancel.setVisible(False)
        self._set_search_controls_enabled(True)
        self._progress.setVisible(False)
        self._progress_label.setVisible(False)

    def _set_search_controls_enabled(self, enabled: bool) -> None:
        """搜索期间冻结条件快照，防止界面与后台参数错位。"""
        for control in (
            self._chk_exclude_mock,
            self._chk_apply_tuning,
            self._edit_tuning,
            self._combo_min_rating,
            self._chk_pruning,
            self._chk_season_chengyin,
            self._btn_gongjue,
            self._btn_gongjue_all,
        ):
            control.setEnabled(enabled)
        apply_tuning = enabled and self._chk_apply_tuning.isChecked()
        self._edit_tuning.setEnabled(apply_tuning)
        self._combo_min_rating.setEnabled(apply_tuning)
        for group in self._slot_groups.values():
            group.setEnabled(enabled)

    def _on_progress(self, evaluated: int, total: int, message: str) -> None:
        self._progress.setMaximum(max(total, 1))
        self._progress.setValue(evaluated)
        self._progress_label.setText(
            f"{evaluated:,} / {total:,}" + (f"  {message}" if message else ""))

    def _on_finished(self, results: list) -> None:
        self._results = _global_top_results(list(results))
        self._restore_idle_controls()
        self._candidate_summary.setText(
            tr("搜索完成，共得到 {count} 个可用结果。")
            .format(count=len(self._results)),
        )
        self.results_changed.emit(self._results)
        self._render_results(self._results)

    def results(self) -> list[dict[str, Any]]:
        return list(self._results)

    def restore_results(self, results: list[dict[str, Any]]) -> None:
        """回填缓存的搜索结果（不触发搜索，不写缓存）。"""
        self._results = _global_top_results(list(results))
        self._btn_clear_results.setVisible(bool(self._results))
        self._clear_rendered_results()
        if self._results:
            self._candidate_summary.setText(
                tr("显示上次搜索的 {count} 个结果；重新搜索会覆盖。")
                .format(count=len(self._results)))
        self._render_results(self._results)

    def _clear_rendered_results(self) -> None:
        """清空结果列表控件，不改动搜索结果数据。"""
        while self._results_inner.count():
            item = self._results_inner.takeAt(0)
            if item is not None:
                w = item.widget()
                if w is not None:
                    w.deleteLater()
        self._result_cards.clear()

    def _on_clear_results(self) -> None:
        """主动清除当前方案的搜索结果及对话框缓存。"""
        if self._worker_running() or not self._results:
            return
        self._results = []
        self._clear_rendered_results()
        self._btn_clear_results.setVisible(False)
        self._tab_widget.setTabText(1, tr("最优结果"))
        self._tab_widget.setTabText(2, tr("组合详情"))
        if self._tab_widget.count() > 3:
            self._tab_widget.setTabText(3, tr("战斗属性"))
        self._tab_widget.setCurrentIndex(0)
        self._candidate_summary.setText(tr("已清除上次搜索结果。"))
        self.results_changed.emit([])

    def mark_stale(self) -> None:
        """假设栏改动后提示结果已过期，不自动重算、不清空。"""
        if self._results and not self._worker_running():
            self._candidate_summary.setText(
                tr("计算假设已变化，当前结果按旧假设得出；请重新搜索。"))

    def _worker_running(self) -> bool:
        return self._jobs.running

    def _render_results(self, results: list) -> None:
        if not results:
            self._tab_widget.setTabText(1, tr("最优结果"))
            lbl = QLabel(tr("未找到有效组合"))
            lbl.setStyleSheet("color: palette(mid);")
            self._results_inner.addWidget(lbl)
            self._tab_widget.setCurrentIndex(1)
            return

        self._tab_widget.setTabText(
            1, tr("最优结果") + f"  (Top {len(results)})")
        self._tab_widget.setCurrentIndex(1)

        for rank, result in enumerate(results, 1):
            # 方案编号跟着结果走：组合详情 / 战斗属性页签用它标明看的是第几套
            result["rank"] = rank
            card = _ResultCard(
                rank, result, self._slot_labels,
                self._current_equipped,
            )
            card.apply_clicked.connect(self._on_apply_result)
            card.view_clicked.connect(self._on_show_result)
            self._results_inner.addWidget(card)
            self._result_cards.append(card)

    def _on_error(self, message: str) -> None:
        self._restore_idle_controls()
        self._candidate_summary.setText(tr("搜索失败，请检查候选装备后重试。"))
        QMessageBox.critical(self, tr("搜索失败"), message)

    def _on_show_result(self, result: dict) -> None:
        """同步刷新两个组合预览页，并先展示组合详情。"""
        self._on_show_detail(result, activate=False)
        self._on_show_attrs(result, activate=False)
        self._tab_widget.setCurrentIndex(2)

    def _on_show_detail(self, result: dict, *, activate: bool = True) -> None:
        """把这套组合铺到「组合详情」页。

        卡片只显示原始装备；虚拟计算装备绝不进入展示层。为了兼容直接调用
        本方法的旧代码，不带结果元数据时将参数本身视为 equipped。
        """
        equipped = result.get("equipped", result)
        assumptions = result.get("assumptions", {})
        if not isinstance(equipped, dict):
            equipped = {}
        if not isinstance(assumptions, dict):
            assumptions = {}
        current_equipped = self._current_equipped
        for slot_key, panel in self._detail_panels.items():
            equip = equipped.get(slot_key)
            if isinstance(equip, dict) and equip:
                panel.show_equip(
                    equip, list(assumptions.get(slot_key) or []),
                    current_equipped.get(slot_key),
                )
            else:
                panel.show_empty()
        filled = sum(1 for eq in equipped.values() if isinstance(eq, dict))
        gongjue = str(result.get("gongjue") or tr("无"))
        changed = _changed_slots(equipped, current_equipped)
        if changed:
            change_text = tr("需更换 {count} 件：{names}").format(
                count=len(changed),
                names="、".join(self._slot_labels.get(k, k) for k in changed),
            )
        else:
            change_text = tr("与当前备战方案一致，无需更换")
        self._detail_hint.setText(
            tr("弓玦套装：{gongjue}　共 {n} 件　·　{change}　·　"
               "卡片显示原始装备数值，计算假设标注在卡片上方")
            .format(gongjue=gongjue, n=filled, change=change_text))
        self._tab_widget.setTabText(2, self._ranked_title(tr("组合详情"), result))
        if activate:
            self._tab_widget.setCurrentIndex(2)

    @staticmethod
    def _ranked_title(title: str, result: dict) -> str:
        """页签标题带上方案编号：「组合详情 (#2)」。"""
        rank = result.get("rank")
        return f"{title} (#{rank})" if rank else title

    def _on_apply_result(self, equipped: dict) -> None:
        """将搜索结果的装备组合写入 session（按槽位合并，不覆盖未参与槽位）。"""
        user_name = self._host.active_user_name()
        if not user_name:
            QMessageBox.warning(self, tr("提示"), tr("没有激活的用户"))
            return

        try:
            from ...core.combat.equipment import EquipmentInventory
            inv = EquipmentInventory(user_name)
            inv.apply_combos(equipped)

            # Notify host
            get_event_hub(self._host).publish(EQUIPMENT_CHANGED)
            QMessageBox.information(
                self, tr("已应用"),
                tr("最优组合已应用到装备栏"))
        except Exception as e:
            logger.error(f"应用组合失败: {e}")
            QMessageBox.critical(self, tr("应用失败"), str(e))
