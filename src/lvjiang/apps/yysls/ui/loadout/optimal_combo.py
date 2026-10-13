"""最优毕业率装备组合搜索对话框。

从当前用户的 equipped + bag_items 中收集候选装备，
按槽位分组展示，用户勾选后暴力穷举 + 支配剪枝搜索最优组合。
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from loguru import logger
from PyQt6.QtCore import (
    Qt,
    pyqtSignal,
)
from PyQt6.QtWidgets import (
    QAbstractScrollArea,
    QCheckBox,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLayout,
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
)
from lvjiang.ui.combo_box import AutoWidthComboBox
from lvjiang.ui.layout_helpers import fit_combo_to_contents

from .....i18n import tr
from .....ui.presentation import (
    style_document_tabs,
)
from ...core.combat.combat_attrs import (
    CombatAttributes,
    GraduationAttrContext,
)
from ...core.combat.equipment_sets import LEFT_SET_SLOTS
from ...core.graduation.assumptions import Assumptions
from ...core.graduation.candidate_pool import (
    CandidateFilter,
    collect_candidates,
)
from ...core.graduation.context import gongjue_attrs
from ...core.graduation.optimal_combo import (
    OPTIMAL_RESULT_LIMIT,
    build_candidate_variants,
)
from ..domain_labels import domain_label
from ..events import EQUIPMENT_CHANGED, get_event_hub
from .background import JobContext, JobController, JobProgress
from .optimal_widgets import (
    _CHANGE_NEW as _CHANGE_NEW,
)
from .optimal_widgets import (
    _CHANGE_NONE as _CHANGE_NONE,
)
from .optimal_widgets import (
    _CHANGE_SAME as _CHANGE_SAME,
)
from .optimal_widgets import (
    _CHANGE_SWAP as _CHANGE_SWAP,
)
from .optimal_widgets import (
    _DEFAULT_MIN_RATING as _DEFAULT_MIN_RATING,
)
from .optimal_widgets import (
    _MIN_RATING_CHOICES as _MIN_RATING_CHOICES,
)
from .optimal_widgets import (
    _QUALITY_COLORS as _QUALITY_COLORS,
)
from .optimal_widgets import (
    _SLOT_ORDER as _SLOT_ORDER,
)
from .optimal_widgets import (
    _STRIP_ROW_HEIGHT as _STRIP_ROW_HEIGHT,
)
from .optimal_widgets import (
    _VISIBLE_CANDIDATE_ROWS as _VISIBLE_CANDIDATE_ROWS,
)
from .optimal_widgets import (
    _CandidateRow as _CandidateRow,
)
from .optimal_widgets import (
    _change_pill as _change_pill,
)
from .optimal_widgets import (
    _changed_slots as _changed_slots,
)
from .optimal_widgets import (
    _ClickableLabel as _ClickableLabel,
)
from .optimal_widgets import (
    _ClickableLineEdit as _ClickableLineEdit,
)
from .optimal_widgets import (
    _equip_label as _equip_label,
)
from .optimal_widgets import (
    _equip_tooltip as _equip_tooltip,
)
from .optimal_widgets import (
    _EquipDetailPopup as _EquipDetailPopup,
)
from .optimal_widgets import (
    _MultiSelectMenu as _MultiSelectMenu,
)
from .optimal_widgets import (
    _PlaystylePickerDialog as _PlaystylePickerDialog,
)
from .optimal_widgets import (
    _ResultCard as _ResultCard,
)
from .optimal_widgets import (
    _slot_change as _slot_change,
)
from .optimal_widgets import (
    _SlotDetailPanel as _SlotDetailPanel,
)
from .optimal_widgets import (
    _SlotGroup as _SlotGroup,
)
from .widgets import (
    HypothesisViewToggle,
)


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








# 8 个装备槽位的显示顺序与分组映射
#: (slot_key, display_name, bag_filter_type)；唯一定义见 config.equipment_slots


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
    graduation_context: GraduationAttrContext,
    season_chengyin: bool = False,
    equipment_set: str = "",
) -> Callable[[JobContext], list[dict[str, Any]]]:
    """构造交给 JobController 的搜索函数：逐弓玦场景搜索并合并结果。"""

    def run(ctx: JobContext) -> list[dict[str, Any]]:
        from ...core.graduation import get_graduation_calculator
        from ...core.graduation.optimal_combo import search_optimal_combo

        calc = get_graduation_calculator(school, scheme, season_level)
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
                graduation_context=graduation_context,
                season_level=season_level,
                season_chengyin=season_chengyin,
                equipment_set=equipment_set,
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













# ── 与备战方案的差异标注 ─────────────────────────────────

#: 计算假设的强调色：和装备卡片上原来那行假设文字保持同一种琥珀色。
#: 部位相对当前备战方案的变化。
#: 组合详情状态带每行高度（px）；两行固定，空行也占位













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
        gongjue_level: int = 0,
        playstyle: str = "",
        main_martial_art: str = "",
        sub_martial_art: str = "",
        world_level: int = 0,
        graduation_context: GraduationAttrContext | None = None,
        parent: QWidget | None = None,
        *,
        assumptions_provider: Callable[[], Assumptions] | None = None,
        analysis_settings: dict[str, Any] | None = None,
        settings_changed: Callable[[dict[str, Any]], None] | None = None,
        equipment_set: str = "",
    ) -> None:
        super().__init__(parent)
        self._host = host
        self._assumptions_provider = assumptions_provider or Assumptions
        self._settings_changed = settings_changed
        self._restoring_settings = False
        self._results: list[dict[str, Any]] = []
        self._school = school
        from ...config import get_game_config
        self._default_equipment_set = (
            equipment_set
            or get_game_config().recommended_equipment_set(school)
        )
        self._scheme = scheme
        # base_attrs 不含弓玦，弓玦属性按需计算
        self._base_attrs_raw = base_attrs
        self._current_gongjue = gongjue
        self._gongjue_level = gongjue_level
        self._playstyle = playstyle
        self._main_martial_art = main_martial_art
        self._sub_martial_art = sub_martial_art
        self._world_level = world_level
        self._graduation_context = (
            graduation_context
            or GraduationAttrContext.from_school(
                school, world_level=world_level or None)
        )
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
        self._combo_min_rating = AutoWidthComboBox()
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

        equipment_set_label = QLabel(tr("攻具套装"))
        equipment_set_label.setProperty("tone", "muted")
        compute_row.addWidget(equipment_set_label)
        self._combo_equipment_set = AutoWidthComboBox()
        from ...config import get_game_config
        for key, entry in get_game_config().get_equipment_sets("left").items():
            self._combo_equipment_set.addItem(
                str(entry.get("name") or key), key)
        set_index = self._combo_equipment_set.findData(
            self._default_equipment_set)
        if set_index >= 0:
            self._combo_equipment_set.setCurrentIndex(set_index)
        fit_combo_to_contents(self._combo_equipment_set, minimum=96)
        self._combo_equipment_set.currentIndexChanged.connect(
            self._on_equipment_set_changed)
        compute_row.addWidget(self._combo_equipment_set)

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
            # 候选数量不参与外层 2×4 网格的尺寸计算：八个区域始终等高，
            # 高度上限在 _cap_candidate_area_height 里按行数统一给定，
            # 每个区域内部再按需滚动。
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
        # 两行候选区平分可用高度，但都受 _cap_candidate_area_height 给的高度
        # 上限约束：窗口再高，单个部位也只展示 10 行候选，其余交给本区滚动条。
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
        detail_header = QHBoxLayout()
        self._detail_hint = QLabel(tr("在「最优结果」里点某一条的「查看该组合」"))
        self._detail_hint.setProperty("tone", "muted")
        self._detail_hint.setStyleSheet("font-size: 12px;")
        detail_header.addWidget(self._detail_hint, 1)
        self._detail_hypothesis_toggle = HypothesisViewToggle()
        self._detail_hypothesis_toggle.toggled.connect(
            self._on_detail_hypothesis_toggled)
        detail_header.addWidget(self._detail_hypothesis_toggle)
        detail_layout.addLayout(detail_header)
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
        from ...config import get_game_config

        projected: dict[str, dict] = {}
        season_level = self._world_level or get_game_config().current_equip_level()
        for slot_key, equip in equipped.items():
            if not isinstance(equip, dict):
                continue
            labels = per_slot.get(slot_key) or [] if isinstance(per_slot, dict) else []
            use_season_branch = "同等级承音假设" in labels
            variants = build_candidate_variants(
                {slot_key: [equip]}, assumptions=base,
                season_level=season_level,
                season_chengyin=use_season_branch,
            )[slot_key]
            selected = next(
                (variant for variant in variants
                 if ("同等级承音假设" in variant.assumptions)
                 == use_season_branch),
                variants[0],
            )
            projected[slot_key] = selected.virtual
        self._apply_result_equipment_set(projected, result)
        return projected

    @staticmethod
    def _apply_result_equipment_set(equipped: dict, result: dict) -> dict:
        """把搜索时选定的攻具套装只投影到结果副本。"""
        equipment_set = str(result.get("equipment_set") or "")
        if not equipment_set:
            return equipped
        for slot_key in LEFT_SET_SLOTS:
            equip = equipped.get(slot_key)
            if isinstance(equip, dict):
                equip["equipment_set"] = equipment_set
        return equipped

    def _on_show_attrs(self, result: dict, *, activate: bool = True) -> None:
        """把这套组合的战斗属性铺到「战斗属性」页。"""
        if self._attrs_preview is None:
            return
        gongjue = str(result.get("gongjue") or "")
        self._attrs_preview.show_preview(
            self._preview_equipped_for(result), gongjue=gongjue,
            world_level=self._world_level,
            gongjue_level=self._gongjue_level)
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

    def _on_equipment_set_changed(self, _index: int) -> None:
        if self._restoring_settings:
            return
        self._persist_analysis_settings()
        self.mark_stale()

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
            "equipment_set": str(
                self._combo_equipment_set.currentData() or ""),
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
            self._combo_equipment_set,
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
            equipment_set_index = self._combo_equipment_set.findData(
                str(settings.get("equipment_set") or ""))
            if equipment_set_index >= 0:
                self._combo_equipment_set.setCurrentIndex(equipment_set_index)
        finally:
            for control in controls:
                control.blockSignals(False)
            self._restoring_settings = False
        enabled = self._chk_apply_tuning.isChecked()
        self._edit_tuning.setEnabled(enabled)
        self._combo_min_rating.setEnabled(enabled)
        self._refresh_tuning_display()

    def _cap_candidate_area_height(self) -> None:
        """把每个部位候选区的高度上限压到「10 行候选」。

        候选数量不参与对话框高度计算：某个部位候选再多，最多也只占这 10 行，
        其余由该区域自己的滚动条承担。上限取八个部位的统一值，保证网格里
        八个区域始终等高。
        """
        heights = [
            group.visible_height()
            for group in self._slot_groups.values() if group.rows
        ]
        if not heights:
            return
        cap = max(heights)
        for scroll in self._slot_scroll_areas.values():
            scroll.setMaximumHeight(cap)
        logger.debug(
            f"候选区高度上限: {cap}px（{_VISIBLE_CANDIDATE_ROWS} 行候选）")

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

        self._cap_candidate_area_height()
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

        equipment_set = str(self._combo_equipment_set.currentData() or "")
        if not equipment_set:
            QMessageBox.warning(self, tr("无法搜索"), tr("请选择攻具套装"))
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
        season_level = self._world_level or gc.current_equip_level()
        scenarios = [
            (name, self._base_attrs_raw + gongjue_attrs(
                name, gc, world_level=season_level,
                gongjue_level=self._gongjue_level))
            for name in gongjues
        ]
        # 假设在点击时定格：搜索期间改动假设栏不影响本次结果。赛季承音
        # 独立作为本页的搜索空间选项传入，不进入共享假设或备战方案静态值。
        assumptions = self._assumptions_provider().with_playstyle(self._playstyle)
        season_chengyin = self._chk_season_chengyin.isChecked()
        self._searched_assumptions = assumptions
        self._jobs.start(_search_job(
            candidates,
            self._school,
            self._scheme,
            scenarios,
            self._chk_pruning.isChecked(),
            assumptions,
            season_level,
            self._graduation_context,
            season_chengyin,
            equipment_set,
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
            self._combo_equipment_set,
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

        默认显示原始装备；开启假设视图后显示搜索时用于算分的内存副本。
        为了兼容直接调用本方法的旧代码，不带结果元数据时将参数本身视为
        equipped。
        """
        self._detail_result = result
        original_equipped = result.get("equipped", result)
        if result.get("equipment_set") and isinstance(original_equipped, dict):
            original_equipped = {
                slot_key: dict(equip) if isinstance(equip, dict) else equip
                for slot_key, equip in original_equipped.items()
            }
            self._apply_result_equipment_set(original_equipped, result)
        equipped = original_equipped
        if self._detail_hypothesis_toggle.isChecked():
            if "equipped" in result:
                equipped = self._preview_equipped_for(result)
            else:
                base = self._assumptions_provider().with_playstyle(
                    self._playstyle)
                equipped = base.project(original_equipped)
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
        filled = sum(
            1 for eq in original_equipped.values() if isinstance(eq, dict))
        gongjue = str(result.get("gongjue") or tr("无"))
        changed = _changed_slots(original_equipped, current_equipped)
        if changed:
            change_text = tr("需更换 {count} 件：{names}").format(
                count=len(changed),
                names="、".join(self._slot_labels.get(k, k) for k in changed),
            )
        else:
            change_text = tr("与当前备战方案一致，无需更换")
        view_text = (
            tr("卡片显示应用假设后的内存副本")
            if self._detail_hypothesis_toggle.isChecked()
            else tr("卡片显示原始装备数值，计算假设标注在卡片上方")
        )
        self._detail_hint.setText(
            tr("弓玦套装：{gongjue}　共 {n} 件　·　{change}　·　{view}")
            .format(gongjue=gongjue, n=filled, change=change_text,
                    view=view_text))
        self._tab_widget.setTabText(2, self._ranked_title(tr("组合详情"), result))
        if activate:
            self._tab_widget.setCurrentIndex(2)

    def _on_detail_hypothesis_toggled(self, _enabled: bool) -> None:
        result = getattr(self, "_detail_result", None)
        if isinstance(result, dict):
            self._on_show_detail(result, activate=False)

    @staticmethod
    def _ranked_title(title: str, result: dict) -> str:
        """页签标题带上方案编号：「组合详情 (#2)」。"""
        rank = result.get("rank")
        return f"{title} (#{rank})" if rank else title

    def _on_apply_result(self, result: dict) -> None:
        """将搜索结果的装备组合写入 session（按槽位合并，不覆盖未参与槽位）。"""
        user_name = self._host.active_user_name()
        if not user_name:
            QMessageBox.warning(self, tr("提示"), tr("没有激活的用户"))
            return

        try:
            from ...core.combat.equipment import EquipmentInventory
            inv = EquipmentInventory(user_name)
            inv.apply_combos(
                result.get("equipped", {}),
                equipment_set=str(result.get("equipment_set") or ""),
            )

            # Notify host
            get_event_hub(self._host).publish(EQUIPMENT_CHANGED, user_name)
            QMessageBox.information(
                self, tr("已应用"),
                tr("最优组合已应用到装备栏"))
        except Exception as e:
            logger.error(f"应用组合失败: {e}")
            QMessageBox.critical(self, tr("应用失败"), str(e))
