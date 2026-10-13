"""最优组合候选、装备详情和结果展示组件。"""
from __future__ import annotations

from typing import Any

from PyQt6.QtCore import (
    Qt,
    pyqtSignal,
)
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QSizePolicy,
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
from .....ui.presentation import (
    highlight_pill,
    make_pill,
    muted_pill,
)
from ...config.equipment_slots import SLOT_SPECS
from ...core.affix_cap import affix_dict_cap_pct
from ..domain_labels import domain_label
from .widgets import (
    assumption_pill,
)

_SLOT_ORDER: list[tuple[str, str, str]] = [
    (spec.key, spec.label, spec.filter_type) for spec in SLOT_SPECS
]
_QUALITY_COLORS = {
    "gold": "#B8860B",
    "purple": "#8B5CF6",
    "blue": "#2563EB",
    "green": "#16A34A",
}
_STRIP_ROW_HEIGHT = 20
_CHANGE_SWAP = "swap"    # 备战方案穿着别的装备，要换下来
_CHANGE_NEW = "new"      # 备战方案这个部位是空的，直接穿上
_CHANGE_SAME = "same"    # 与备战方案一致
_CHANGE_NONE = "none"    # 组合里没有这个部位

#: 「评级要求」可选档位，由高到低。垃圾不列：要求「至少是垃圾」等于没有要求。
_MIN_RATING_CHOICES: tuple[str, ...] = ("顶级", "优秀", "一般")

#: 默认要求。一般是「这件装备还能用」的下限，比顶级/优秀都不容易把候选筛空。
_DEFAULT_MIN_RATING = "一般"

#: 单个部位候选区默认最多展示的候选行数。候选再多也只占这些行的高度，
#: 剩余由该区域自己的滚动条承担——部位候选数量不参与对话框高度计算。
_VISIBLE_CANDIDATE_ROWS = 10


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
    if isinstance(dingyin, dict) and dingyin.get("name"):
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
        # 保留标题行引用：区域高度按「标题 + N 行候选」换算（见 visible_height）
        self._header = header
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

    def visible_height(self, rows: int = _VISIBLE_CANDIDATE_ROWS) -> int:
        """只展示 ``rows`` 行候选时该区域应占的高度（标题 + 行 + 内边距）。

        用于给候选区设高度上限：候选再多也只占这么多行，其余交给本区滚动条。
        """
        layout = self.layout()
        if layout is None:
            return 0
        margins = layout.contentsMargins()
        spacing = max(int(layout.spacing()), 0)
        height = margins.top() + margins.bottom()
        height += self._header.sizeHint().height() + spacing
        row_height = max((row.sizeHint().height() for row in self.rows), default=0)
        if row_height > 0:
            height += rows * row_height + spacing * (rows - 1)
        return height

    def get_selected(self) -> list[dict]:
        """返回勾选的装备列表。"""
        return [row.equip for row in self.rows if row.checkbox.isChecked()]


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

    apply_clicked = pyqtSignal(dict)   # emits complete result metadata
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
        from ...config import get_game_config
        equipment_set_name = get_game_config().equipment_set_name(
            str(result.get("equipment_set") or ""))
        equipment_set_label = QLabel(
            tr("攻具套装：{name}").format(
                name=equipment_set_name or tr("未选择")))
        equipment_set_label.setStyleSheet(
            "font-size: 12px; color: palette(mid);")
        top.addWidget(equipment_set_label)
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
            lambda: self.apply_clicked.emit(result),
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
