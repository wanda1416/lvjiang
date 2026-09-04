"""最优毕业率装备组合搜索对话框。

从当前用户的 equipped + bag_items 中收集候选装备，
按槽位分组展示，用户勾选后暴力穷举 + 支配剪枝搜索最优组合。
"""
from __future__ import annotations

import threading
from typing import Any

from loguru import logger
from PyQt6.QtCore import (
    QObject,
    QRunnable,
    Qt,
    QThreadPool,
    QTimer,
    pyqtSignal,
    pyqtSlot,
)
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .....i18n import tr
from ...core.combat.combat_attrs import (
    CombatAttributes,
)
from ...core.equip_parser.dingyin_parser import is_zhige_dingyin
from ..domain_labels import domain_label
from ..events import EQUIPMENT_CHANGED, get_event_hub
from ..layout_helpers import fit_combo_to_contents

# 8 个装备槽位的显示顺序与分组映射
_SLOT_ORDER: list[tuple[str, str, str]] = [
    # (slot_key, display_name, bag_filter_type)
    ("main_weapon", tr("主武器"), "weapon"),
    ("sub_weapon", tr("副武器"), "weapon"),
    ("head", tr("冠胄"), "head"),
    ("chest", tr("胸甲"), "chest"),
    ("ring", tr("环"), "ring"),
    ("pendant", tr("佩"), "pendant"),
    ("leg", tr("胫甲"), "leg"),
    ("wrist", tr("腕甲"), "wrist"),
]

_QUALITY_COLORS = {
    "gold": "#B8860B",
    "purple": "#8B5CF6",
    "blue": "#2563EB",
    "green": "#16A34A",
}

_PRIMARY_BUTTON_STYLE = (
    "QPushButton { background: palette(highlight); color: palette(highlighted-text); "
    "border: 1px solid palette(highlight); border-radius: 5px; padding: 6px 16px; "
    "font-weight: 700; }"
    "QPushButton:hover { border-color: palette(text); }"
    "QPushButton:pressed { background: palette(dark); }"
    "QPushButton:disabled { background: palette(midlight); color: palette(mid); "
    "border-color: palette(midlight); }"
)

_SECONDARY_BUTTON_STYLE = (
    "QPushButton { background: palette(button); color: palette(button-text); "
    "border: 1px solid palette(mid); border-radius: 5px; padding: 6px 13px; "
    "font-weight: 600; }"
    "QPushButton:hover { border-color: palette(highlight); }"
)


# ---------------------------------------------------------------------------
# Worker signals + runnable
# ---------------------------------------------------------------------------

class _SearchSignals(QObject):
    finished = pyqtSignal(list)  # results list
    error = pyqtSignal(str)


class _SearchWorker(QRunnable):
    """后台搜索线程。"""

    def __init__(
        self,
        candidates: dict[str, list[dict]],
        school: str,
        scheme: str,
        base_attrs: CombatAttributes,
        use_dominance_pruning: bool,
        full_chengyin: bool = False,
        full_dingyin: bool = False,
        full_level: int = 0,
        playstyle: str = "",
    ) -> None:
        super().__init__()
        self.candidates = candidates
        self.school = school
        self.scheme = scheme
        self.base_attrs = base_attrs
        self.use_dominance_pruning = use_dominance_pruning
        self.full_chengyin = full_chengyin
        self.full_dingyin = full_dingyin
        self.full_level = full_level
        self.playstyle = playstyle
        self.signals = _SearchSignals()
        self._cancel_event = threading.Event()
        # 进度计数器（线程安全，由 GIL 保证）
        self.evaluated = 0
        self.total = 0
        self.message = ""

    def cancel(self) -> None:
        self._cancel_event.set()

    def run(self) -> None:
        try:
            from ...core.graduation import get_graduation_calculator
            from ...core.graduation.optimal_combo import search_optimal_combo

            calc = get_graduation_calculator(self.school, self.scheme)
            if calc is None:
                self.signals.error.emit(tr("未找到对应流派的毕业率方案"))
                return

            results = search_optimal_combo(
                self.candidates,
                calc,
                self.base_attrs,
                use_dominance_pruning=self.use_dominance_pruning,
                cancel_flag=self._cancel_event.is_set,
                full_chengyin=self.full_chengyin,
                full_dingyin=self.full_dingyin,
                full_level=self.full_level,
                playstyle=self.playstyle,
                progress_counter=self,
            )
            self.signals.finished.emit(results)
        except Exception as exc:
            logger.error(f"最优组合搜索失败: {exc}")
            self.signals.error.emit(str(exc))


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
    for i in range(1, 6):
        affix = equip.get(f"affix_{i}")
        if isinstance(affix, dict) and affix.get("name"):
            val = affix.get("value", "")
            cap_pct = affix.get("cap_pct")
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
        cap_pct = dingyin.get("cap_pct")
        line = f"{tr('定音')} {dingyin['name']}: {val}"
        if cap_pct is not None:
            line += f" ({cap_pct:.0f}%)"
        parts.append(line)
    return "<br>".join(parts)


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

        self.label = QLabel()
        self.label.setTextFormat(Qt.TextFormat.RichText)
        self.label.setText(_equip_label(equip))
        self.label.setToolTip(_equip_tooltip(equip))
        layout.addWidget(self.label, stretch=1)

        self.score_label = QLabel(rating)
        self.score_label.setToolTip(tr("所选调律规则的实际评级"))
        self.score_label.setStyleSheet("font-size: 11px; color: palette(mid);")
        layout.addWidget(self.score_label)

    def set_rating(self, rating: str) -> None:
        self.score_label.setText(rating)


class _SlotGroup(QFrame):
    """单槽位候选区：标题 + 候选行列表。"""

    def __init__(
        self, slot_key: str, display_name: str,
        equips: list[dict], school: str,
        tuning_rule: str = "", playstyle: str = "",
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

        from ...core.graduation.combo_rules import judge_tuning_candidate
        def rating_of(equip: dict) -> tuple[int, str]:
            if not tuning_rule or not playstyle:
                return 0, "-"
            result = judge_tuning_candidate(equip, tuning_rule, playstyle)
            if result.not_applicable:
                return 0, tr("不适用")
            if result.skipped:
                return 0, tr("跳过")
            ranks = {"垃圾": 0, "一般": 1, "优秀": 2, "顶级": 3}
            return ranks.get(result.rating.value, 0), domain_label(result.rating.value)
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

    def get_selected(self) -> list[dict]:
        """返回勾选的装备列表。"""
        return [row.equip for row in self.rows if row.checkbox.isChecked()]


class _ResultCard(QFrame):
    """单条搜索结果卡片。"""

    apply_clicked = pyqtSignal(dict)  # emits equipped dict

    def __init__(
        self, rank: int, result: dict[str, Any],
        slot_labels: dict[str, str], parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setProperty("surface", "card")
        self.result = result

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

        top.addStretch()

        apply_btn = QPushButton(tr("应用此组合"))
        apply_btn.setMinimumHeight(30)
        apply_btn.setStyleSheet(_PRIMARY_BUTTON_STYLE)
        apply_btn.clicked.connect(
            lambda: self.apply_clicked.emit(result.get("equipped", {})),
        )
        top.addWidget(apply_btn)
        layout.addLayout(top)

        # Equipment summary
        equipped = result.get("equipped", {})
        parts: list[str] = []
        for slot_key, _dn, _ft in _SLOT_ORDER:
            eq = equipped.get(slot_key)
            if eq:
                name = eq.get("name", "?")
                label = slot_labels.get(slot_key, slot_key)
                parts.append(f"{label}: {name}")
        summary = QLabel("  ".join(parts))
        summary.setProperty("tone", "muted")
        summary.setStyleSheet("font-size: 12px;")
        summary.setWordWrap(True)
        layout.addWidget(summary)


# ---------------------------------------------------------------------------
# Main dialog
# ---------------------------------------------------------------------------

class OptimalComboDialog(QDialog):
    """最优毕业率装备组合搜索对话框。"""

    def __init__(
        self,
        host: Any,
        school: str,
        scheme: str,
        base_attrs: CombatAttributes,
        level_threshold: int = 0,
        affix_filter: str = "all",
        gongjue: str = "",
        main_martial_art: str = "",
        sub_martial_art: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._host = host
        self._school = school
        self._scheme = scheme
        # base_attrs 不含弓玦，弓玦属性按需计算
        self._base_attrs_raw = base_attrs
        self._current_gongjue = gongjue
        self._main_martial_art = main_martial_art
        self._sub_martial_art = sub_martial_art
        self._base_attrs = base_attrs + self._compute_gongjue_attrs(gongjue)
        self._level_threshold = level_threshold
        self._affix_filter = affix_filter
        self._worker: _SearchWorker | None = None
        self._slot_groups: dict[str, _SlotGroup] = {}
        self._result_cards: list[_ResultCard] = []

        self.setWindowTitle(tr("最优组合"))
        self.setMinimumSize(920, 620)
        self.resize(1080, 720)
        self._setup_ui()
        self._load_candidates()

    def _on_rotation(self) -> None:
        """打开技能轴查看器（实验性）

        轴数据只在毕业率计算器 Excel 里——方案 JSON 编译时丢掉了技能名，
        所以要用户自己选文件，不从当前方案读。
        """
        from .rotation_dialog import RotationDialog

        RotationDialog(parent=self).exec()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(12)

        context = QLabel(
            tr("{school}  ·  {scheme}  ·  基于当前备战方案").format(
                school=self._school, scheme=self._scheme,
            )
        )
        context.setProperty("tone", "muted")
        context.setStyleSheet("font-size: 12px;")

        # 首行最右侧放技能轴入口。它是实验性功能：需要用户自备毕业率计算器
        # Excel，且与本对话框的搜索流程无关，因此不放到备战方案主工具栏上，
        # 只在这里留一个不显眼的口子。
        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.addWidget(context, stretch=1)
        self._btn_rotation = QPushButton(tr("技能轴"))
        self._btn_rotation.setToolTip(
            tr("实验性：导入毕业率计算器 Excel，查看竞速轴与伤害来源"))
        self._btn_rotation.setProperty("tone", "muted")
        self._btn_rotation.setFlat(True)
        self._btn_rotation.setStyleSheet("font-size: 12px;")
        self._btn_rotation.clicked.connect(self._on_rotation)
        header.addWidget(self._btn_rotation)
        layout.addLayout(header)

        settings = QFrame()
        settings.setProperty("surface", "card")
        settings_layout = QVBoxLayout(settings)
        settings_layout.setContentsMargins(14, 11, 14, 11)
        settings_layout.setSpacing(9)
        settings_title = QLabel(tr("搜索设置"))
        settings_title.setStyleSheet("font-size: 14px; font-weight: 700;")
        settings_layout.addWidget(settings_title)

        options = QHBoxLayout()
        options.setSpacing(14)
        self._chk_pruning = QCheckBox(tr("智能筛选"))
        self._chk_pruning.setChecked(True)
        self._chk_pruning.setToolTip(
            tr("自动淘汰被其他候选完全压制的装备，缩减搜索空间"))
        options.addWidget(self._chk_pruning)
        self._chk_exclude_mock = QCheckBox(tr("排除模拟"))
        self._chk_exclude_mock.setChecked(True)
        self._chk_exclude_mock.setToolTip(
            tr("搜索时排除模拟装备，仅使用真实背包和已穿戴装备"))
        # 其余选项都在开始搜索时才读，唯独本项决定候选池内容，
        # 必须当场重建——否则改了也只在下次打开对话框才生效。
        self._chk_exclude_mock.toggled.connect(self._on_exclude_mock_toggled)
        options.addWidget(self._chk_exclude_mock)
        self._chk_full_chengyin = QCheckBox(tr("满承音"))
        self._chk_full_chengyin.setToolTip(
            tr("将承音装备的词条数值视为承音上限参与计算"))
        options.addWidget(self._chk_full_chengyin)
        self._chk_full_dingyin = QCheckBox(tr("满定音"))
        self._chk_full_dingyin.setToolTip(
            tr("将定音词条数值视为上限（100%）参与计算"))
        options.addWidget(self._chk_full_dingyin)
        self._chk_full_level = QCheckBox(tr("满等级"))
        self._chk_full_level.setToolTip(
            tr("将低于最高等级的装备视为最高等级参与计算"))
        options.addWidget(self._chk_full_level)
        options.addStretch()
        settings_layout.addLayout(options)

        tuning_row = QHBoxLayout()
        tuning_row.setSpacing(8)
        gongjue_label = QLabel(tr("弓玦套装"))
        gongjue_label.setProperty("tone", "muted")
        tuning_row.addWidget(gongjue_label)
        self._combo_gongjue = QComboBox()
        self._combo_gongjue.addItem(tr("无"), "")
        for gj_type in ["会意", "精准", "会心"]:
            self._combo_gongjue.addItem(gj_type, gj_type)
        fit_combo_to_contents(self._combo_gongjue, minimum=112)
        # 设置默认选中
        idx = self._combo_gongjue.findData(self._current_gongjue)
        if idx >= 0:
            self._combo_gongjue.setCurrentIndex(idx)
        self._combo_gongjue.currentIndexChanged.connect(
            self._on_gongjue_changed)
        tuning_row.addWidget(self._combo_gongjue)
        tuning_row.addSpacing(16)
        tuning_label = QLabel(tr("候选评级"))
        tuning_label.setProperty("tone", "muted")
        tuning_label.setToolTip(tr("仅用于辅助筛选候选装备，不参与装备合法性判断"))
        tuning_row.addWidget(tuning_label)
        self._combo_tuning = QComboBox()
        self._combo_tuning.addItem(tr("不应用规则"), None)
        self._combo_tuning.setToolTip(
            tr("玩法评级只辅助勾选候选，不作为装备合法性规则"))
        self._combo_tuning.currentIndexChanged.connect(
            self._on_tuning_changed)
        tuning_row.addWidget(self._combo_tuning, 1)
        settings_layout.addLayout(tuning_row)
        self._load_tuning_options()
        layout.addWidget(settings)

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
        self._btn_search = QPushButton(tr("开始搜索"))
        self._btn_search.setMinimumHeight(32)
        self._btn_search.setStyleSheet(_PRIMARY_BUTTON_STYLE)
        self._btn_search.clicked.connect(self._on_search)
        action_row.addWidget(self._btn_search)

        self._btn_cancel = QPushButton(tr("取消"))
        self._btn_cancel.setMinimumHeight(32)
        self._btn_cancel.setStyleSheet(_SECONDARY_BUTTON_STYLE)
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
        self._tab_widget.setObjectName("optimalComboTabs")
        self._tab_widget.setDocumentMode(True)
        self._tab_widget.setStyleSheet(
            "QTabWidget#optimalComboTabs::pane {"
            " border: 1px solid palette(midlight); border-radius: 7px; }"
            "QTabWidget#optimalComboTabs QTabBar::tab {"
            " padding: 9px 18px; min-width: 120px; }"
        )

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
            scroll.setHorizontalScrollBarPolicy(
                Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            scroll.setFrameShape(QFrame.Shape.NoFrame)
            grid.addWidget(scroll, row, col)
            self._slot_scroll_areas[slot_key] = scroll
        for c in range(4):
            grid.setColumnStretch(c, 1)
        for r in range(2):
            grid.setRowStretch(r, 1)
        candidates_layout.addLayout(grid)
        self._tab_widget.addTab(candidates_tab, tr("候选装备"))

        # Tab 2: 最优结果
        results_tab = QWidget()
        results_layout = QVBoxLayout(results_tab)
        results_layout.setContentsMargins(0, 0, 0, 0)
        results_scroll = QScrollArea()
        results_scroll.setWidgetResizable(True)
        results_scroll.setFrameShape(QFrame.Shape.NoFrame)
        results_container = QWidget()
        self._results_inner = QVBoxLayout()
        self._results_inner.setContentsMargins(8, 8, 8, 8)
        self._results_inner.setSpacing(8)
        results_container.setLayout(self._results_inner)
        results_scroll.setWidget(results_container)
        results_layout.addWidget(results_scroll)
        self._tab_widget.addTab(results_tab, tr("最优结果"))

        layout.addWidget(self._tab_widget, stretch=1)

    def _compute_gongjue_attrs(self, gongjue_type: str) -> CombatAttributes:
        """计算弓玦属性：当前赛季最大等级三率词条上限的一半。"""
        if not gongjue_type:
            return CombatAttributes()
        try:
            from ...config import get_game_config
            from ...core.combat.combat_attrs import compute_gongjue_attrs
            gc = get_game_config()
            seasons = gc.get_season_configs()
            if not seasons:
                return CombatAttributes()
            equip_level = seasons[-1].equip_level
            if not equip_level:
                return CombatAttributes()
            return compute_gongjue_attrs(
                gongjue_type, equip_level, gc.get_affix_caps)
        except Exception as e:
            logger.error(f"计算弓玦属性失败: {e}")
            return CombatAttributes()

    def _on_gongjue_changed(self, _index: int) -> None:
        """弓玦切换后重算基础属性。"""
        gongjue = self._combo_gongjue.currentData()
        if not isinstance(gongjue, str):
            gongjue = ""
        self._current_gongjue = gongjue
        self._base_attrs = self._base_attrs_raw + self._compute_gongjue_attrs(gongjue)

    def _load_tuning_options(self) -> None:
        """加载调律规则，填充合并后的下拉框。

        每个条目格式："规则名-玩法名"，data 为 (rule_key, playstyle)。
        只展示匹配当前流派的规则+玩法组合。
        """
        from ...config import get_game_config
        from ...core.evaluator import get_tuning_rules

        game_config = get_game_config()
        school_cfg = game_config.get_schools().get(self._school, {})
        main_weapon = game_config.get_martial_art_weapon(
            self._main_martial_art)
        sub_weapon = game_config.get_martial_art_weapon(
            self._sub_martial_art)
        school_attr = school_cfg.get("attr", "")

        for key, rule in get_tuning_rules().items():
            matching = [
                name for name, playstyle in rule.playstyles.items()
                if {playstyle.main.weapon, playstyle.sub.weapon}
                == {main_weapon, sub_weapon}
                and playstyle.attr == school_attr
            ]
            for name in matching:
                self._combo_tuning.addItem(
                    f"{rule.name}-{name}", (key, name))

    def _on_tuning_changed(self, _index: int) -> None:
        """调律规则切换时，前置过滤装备勾选状态。"""
        data = self._combo_tuning.currentData()
        if data is None:
            # "不应用规则"：全部勾选
            for group in self._slot_groups.values():
                for row in group.rows:
                    row.checkbox.setChecked(True)
                    row.checkbox.setVisible(True)
            return
        rule_key, playstyle = data
        from ...core.graduation.combo_rules import judge_tuning_candidate
        for group in self._slot_groups.values():
            for row in group.rows:
                result = judge_tuning_candidate(
                    row.equip, rule_key, playstyle)
                if result.not_applicable:
                    # 不适用：保持勾选，显示提示
                    row.checkbox.setChecked(True)
                    row.checkbox.setVisible(True)
                    row.set_rating(tr("不适用"))
                elif result.skipped:
                    # 跳过：取消勾选
                    row.checkbox.setChecked(False)
                    row.checkbox.setVisible(True)
                    row.set_rating(tr("跳过"))
                elif result.rating and result.rating.value == tr("垃圾"):
                    # 垃圾：取消勾选
                    row.checkbox.setChecked(False)
                    row.checkbox.setVisible(True)
                    row.set_rating(tr("垃圾"))
                else:
                    # 正常评级：保持勾选
                    row.checkbox.setChecked(True)
                    row.checkbox.setVisible(True)
                    row.set_rating(
                        result.rating.value if result.rating else "-")

    def _on_exclude_mock_toggled(self, _checked: bool) -> None:
        """「排除模拟」变化后重建候选池。

        候选池只在对话框构造时加载一次，而本项默认勾选，用户没有机会
        在加载前取消——不当场重建的话这个开关等于没有。
        """
        self._load_candidates()

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

        # 主副槽位取决于方案中两门武学的当前位置，不能使用流派配置的顺序。
        from ...config import get_game_config
        gc = get_game_config()
        main_weapon_type = gc.get_martial_art_weapon(self._main_martial_art)
        sub_weapon_type = gc.get_martial_art_weapon(self._sub_martial_art)
        logger.debug(
            f"流派 {self._school}: 主武器={main_weapon_type}, 副武器={sub_weapon_type}")

        # group_key → slot_keys 映射
        # 武器不再无差别双投，而是按流派武器类型精确分配
        group_to_slots: dict[str, list[str]] = {
            "head": ["head"],
            "chest": ["chest"],
            "ring": ["ring"],
            "pendant": ["pendant"],
            "leg": ["leg"],
            "wrist": ["wrist"],
        }

        slot_candidates: dict[str, list[dict]] = {
            key: [] for key, _, _ in _SLOT_ORDER
        }

        exclude_mock = self._chk_exclude_mock.isChecked()

        def _is_mock(eq: dict) -> bool:
            fp = eq.get("_fp", "")
            if isinstance(fp, str) and fp.startswith("mock_"):
                return True
            return bool(eq.get("_extra", {}).get("is_mock"))

        # 背包侧的候选来源：真实装备恒取，模拟装备按开关并入
        pools: list[dict] = [inv.bag_items]
        if not exclude_mock:
            pools.append(inv.mock_items)

        # 1. 已穿戴装备：先按流派武器和装备页筛选条件校验
        for slot_key, eq in equipped.items():
            if (isinstance(eq, dict) and slot_key in slot_candidates
                    and not (exclude_mock and _is_mock(eq))
                    and self._candidate_passes(slot_key, eq,
                                               main_weapon_type, sub_weapon_type)):
                slot_candidates[slot_key].append(eq)

        # 2. 背包装备（含按开关并入的模拟装备）：按 group_key 分发到槽位
        for pool in pools:
            if not isinstance(pool, dict):
                continue
            for group_key, items_dict in pool.items():
                if not isinstance(items_dict, dict):
                    continue
                if group_key == "weapon":
                    # 武器按流派类型分配到主/副武器槽
                    for _fp, eq in items_dict.items():
                        if not isinstance(eq, dict):
                            continue
                        eq_type = eq.get("type", "")
                        if (main_weapon_type and eq_type == main_weapon_type
                                and self._candidate_passes("main_weapon", eq,
                                                           main_weapon_type, sub_weapon_type)):
                            slot_candidates["main_weapon"].append(eq)
                        if (sub_weapon_type and eq_type == sub_weapon_type
                                and self._candidate_passes("sub_weapon", eq,
                                                           main_weapon_type, sub_weapon_type)):
                            slot_candidates["sub_weapon"].append(eq)
                else:
                    slots = group_to_slots.get(group_key, [])
                    if not slots:
                        continue
                    for _fp, eq in items_dict.items():
                        if isinstance(eq, dict):
                            for sk in slots:
                                if self._candidate_passes(
                                        sk, eq, main_weapon_type, sub_weapon_type):
                                    slot_candidates[sk].append(eq)

        # Build UI groups（按指纹去重）
        slot_labels = {}
        total_candidates = 0
        available_slots = 0
        for slot_key, display_name, _ in _SLOT_ORDER:
            slot_labels[slot_key] = display_name
            candidates = slot_candidates[slot_key]
            seen: set[str] = set()
            unique: list[dict] = []
            for eq in candidates:
                fp = _equip_fingerprint(eq)
                if fp not in seen:
                    seen.add(fp)
                    unique.append(eq)
            tuning_data = self._combo_tuning.currentData()
            rule_key = tuning_data[0] if tuning_data else ""
            playstyle = tuning_data[1] if tuning_data else ""
            group = _SlotGroup(
                slot_key, display_name, unique, self._school,
                rule_key, playstyle,
            )
            self._slot_groups[slot_key] = group
            total_candidates += len(unique)
            if unique:
                available_slots += 1
            scroll = self._slot_scroll_areas.get(slot_key)
            if scroll:
                # 用容器包裹 group，底部加 stretch 防止内容居中
                container = QWidget()
                container_layout = QVBoxLayout(container)
                container_layout.setContentsMargins(0, 0, 0, 0)
                container_layout.setSpacing(0)
                container_layout.addWidget(group)
                container_layout.addStretch()
                scroll.setWidget(container)

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

    def _candidate_passes(
        self, slot_key: str, equip: dict,
        main_weapon_type: str, sub_weapon_type: str,
    ) -> bool:
        """统一应用方案武学派生的武器类型、等级和词条筛选。"""
        required_weapon = (main_weapon_type if slot_key == "main_weapon"
                           else sub_weapon_type if slot_key == "sub_weapon" else "")
        if required_weapon and equip.get("type", "") != required_weapon:
            return False
        try:
            level = int(equip.get("level") or 0)
        except (TypeError, ValueError):
            level = 0
        if self._level_threshold > 0 and level < self._level_threshold:
            return False
        if self._affix_filter == "dingyin":
            dingyin = equip.get("dingyin")
            return (is_zhige_dingyin(equip)
                    or isinstance(dingyin, dict) and bool(dingyin.get("name")))
        if self._affix_filter == "full_tuning":
            return all(
                isinstance(equip.get(f"affix_{i}"), dict)
                and bool(equip[f"affix_{i}"].get("name"))
                for i in range(1, 6)
            )
        return True

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

        # UI state
        self._btn_search.setVisible(False)
        self._btn_cancel.setVisible(True)
        # 候选池正在被搜索，不允许中途换池
        self._chk_exclude_mock.setEnabled(False)
        self._progress.setVisible(True)
        self._progress_label.setVisible(True)
        self._progress.setMaximum(max(total, 1))
        self._progress.setValue(0)
        self._progress_label.setText(f"0 / {total:,}")
        self._candidate_summary.setText(
            tr("正在比较 {count} 种装备组合，搜索期间仍可取消。")
            .format(count=f"{total:,}"),
        )
        # 切回候选装备 Tab，重置结果 Tab 标题
        self._tab_widget.setCurrentIndex(0)
        self._tab_widget.setTabText(1, tr("最优结果"))

        # Clear old results
        while self._results_inner.count():
            item = self._results_inner.takeAt(0)
            if item is not None:
                w = item.widget()
                if w is not None:
                    w.deleteLater()
        self._result_cards.clear()

        # Launch worker
        full_level = 0
        if self._chk_full_level.isChecked():
            from ...config import get_game_config
            gc = get_game_config()
            season = gc.current_season()
            if season and season.equip_level:
                full_level = season.equip_level
            else:
                configs = gc.get_level_configs()
                full_level = configs[-1].level if configs else 0
        tuning_data = self._combo_tuning.currentData()
        playstyle = tuning_data[1] if tuning_data else ""
        self._worker = _SearchWorker(
            candidates,
            self._school,
            self._scheme,
            self._base_attrs,
            self._chk_pruning.isChecked(),
            full_chengyin=self._chk_full_chengyin.isChecked(),
            full_dingyin=self._chk_full_dingyin.isChecked(),
            full_level=full_level,
            playstyle=playstyle,
        )
        # 使用 QueuedConnection 确保 slot 在 UI 线程执行
        # （signal 从后台线程 emit，但 _SearchSignals 的线程亲和性是 UI 线程）
        self._worker.signals.finished.connect(  # type: ignore[call-arg]
            self._on_finished, Qt.ConnectionType.QueuedConnection)
        self._worker.signals.error.connect(  # type: ignore[call-arg]
            self._on_error, Qt.ConnectionType.QueuedConnection)
        pool = QThreadPool.globalInstance()
        if pool is not None:
            pool.start(self._worker)
        # 启动定时器轮询进度
        self._progress_timer = QTimer(self)
        self._progress_timer.timeout.connect(self._poll_progress)
        self._progress_timer.start(1000)  # 每 1 秒轮询一次

    def _on_cancel(self) -> None:
        if self._worker:
            self._worker.cancel()

    def _poll_progress(self) -> None:
        """定时轮询 worker 的进度计数器。"""
        if not self._worker:
            return
        evaluated = self._worker.evaluated
        total = self._worker.total
        message = self._worker.message
        self._progress.setMaximum(max(total, 1))
        self._progress.setValue(evaluated)
        self._progress_label.setText(
            f"{evaluated:,} / {total:,}" + (f"  {message}" if message else ""))

    @pyqtSlot(list)
    def _on_finished(self, results: list) -> None:
        # 停止进度轮询定时器
        if hasattr(self, '_progress_timer'):
            self._progress_timer.stop()
        self._btn_search.setVisible(True)
        self._btn_cancel.setVisible(False)
        self._chk_exclude_mock.setEnabled(True)
        self._progress.setVisible(False)
        self._progress_label.setVisible(False)
        self._candidate_summary.setText(
            tr("搜索完成，共得到 {count} 个可用结果。")
            .format(count=len(results)),
        )

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

        for i, result in enumerate(results):
            card = _ResultCard(i + 1, result, self._slot_labels)
            card.apply_clicked.connect(self._on_apply_result)
            self._results_inner.addWidget(card)
            self._result_cards.append(card)

    @pyqtSlot(str)
    def _on_error(self, message: str) -> None:
        # 停止进度轮询定时器
        if hasattr(self, '_progress_timer'):
            self._progress_timer.stop()
        self._btn_search.setVisible(True)
        self._btn_cancel.setVisible(False)
        self._chk_exclude_mock.setEnabled(True)
        self._progress.setVisible(False)
        self._progress_label.setVisible(False)
        self._candidate_summary.setText(tr("搜索失败，请检查候选装备后重试。"))
        QMessageBox.critical(self, tr("搜索失败"), message)

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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _equip_fingerprint(equip: dict) -> str:
    """装备指纹：用于去重。"""
    name = equip.get("name", "")
    level = equip.get("level", "")
    quality = equip.get("quality", "")
    affixes = []
    for i in range(1, 6):
        affix = equip.get(f"affix_{i}")
        if affix and isinstance(affix, dict):
            affixes.append(f"{affix.get('name', '')}:{affix.get('value', 0)}")
    dingyin = equip.get("dingyin")
    if dingyin and isinstance(dingyin, dict):
        affixes.append(f"dy:{dingyin.get('name', '')}:{dingyin.get('value', 0)}")
    return f"{name}|{level}|{quality}|{'|'.join(affixes)}"
