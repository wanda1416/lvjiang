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
    pyqtSignal,
    pyqtSlot,
)
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFrame,
    QGridLayout,
    QGroupBox,
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

from ....i18n import tr
from ..core.combat.combat_attrs import (
    CombatAttributes,
)

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

_CARD_STYLE = """
    QFrame#optimalCard {
        background-color: palette(base);
        border: 1px solid palette(midlight);
        border-radius: 6px;
    }
"""


# ---------------------------------------------------------------------------
# Worker signals + runnable
# ---------------------------------------------------------------------------

class _SearchSignals(QObject):
    progress = pyqtSignal(int, int, str)  # evaluated, total, message
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
        self.signals = _SearchSignals()
        self._cancel_event = threading.Event()

    def cancel(self) -> None:
        self._cancel_event.set()

    @pyqtSlot()
    def run(self) -> None:
        try:
            from ..core.graduation import get_graduation_calculator
            from ..core.graduation.optimal_combo import search_optimal_combo

            calc = get_graduation_calculator(self.school, self.scheme)
            if calc is None:
                self.signals.error.emit(tr("未找到对应流派的毕业率方案"))
                return

            results = search_optimal_combo(
                self.candidates,
                calc,
                self.base_attrs,
                use_dominance_pruning=self.use_dominance_pruning,
                progress_cb=lambda ev, tot, msg: self.signals.progress.emit(ev, tot, msg),
                cancel_flag=self._cancel_event.is_set,
                full_chengyin=self.full_chengyin,
                full_dingyin=self.full_dingyin,
                full_level=self.full_level,
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
    if isinstance(dingyin, dict) and dingyin.get("name"):
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
        layout.setContentsMargins(4, 1, 4, 1)
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


class _SlotGroup(QGroupBox):
    """单槽位候选区：标题 + 候选行列表。"""

    def __init__(
        self, slot_key: str, display_name: str,
        equips: list[dict], school: str,
        tuning_rule: str = "", playstyle: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(display_name, parent)
        self.slot_key = slot_key
        self.setStyleSheet(
            "QGroupBox { font-weight: 600; font-size: 13px; "
            "border: 1px solid palette(midlight); border-radius: 4px; "
            "margin-top: 8px; padding-top: 12px; }"
            "QGroupBox::title { subcontrol-origin: margin; left: 8px; "
            "padding: 0 4px; }"
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(1)

        from ..core.graduation.combo_rules import judge_tuning_candidate
        def rating_of(equip: dict) -> tuple[int, str]:
            if not tuning_rule or not playstyle:
                return 0, "-"
            result = judge_tuning_candidate(equip, tuning_rule, playstyle)
            if result.not_applicable:
                return 0, tr("不适用")
            if result.skipped:
                return 0, tr("跳过")
            ranks = {tr("垃圾"): 0, tr("一般"): 1,
                     tr("优秀"): 2, tr("顶级"): 3}
            return ranks.get(result.rating.value, 0), result.rating.value
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
        self.setObjectName("optimalCard")
        self.setStyleSheet(_CARD_STYLE)
        self.result = result

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 6, 10, 6)
        layout.setSpacing(4)

        # Top row: rank + rate + DPS + apply button
        top = QHBoxLayout()
        top.setSpacing(12)

        rank_label = QLabel(f"#{rank}")
        rank_label.setStyleSheet("font-weight: 700; font-size: 15px;")
        top.addWidget(rank_label)

        rate = result.get("rate", 0)
        rate_label = QLabel(f"{rate * 100:.2f}%")
        rate_label.setStyleSheet(
            "font-weight: 700; font-size: 15px; color: #D97706;")
        top.addWidget(rate_label)

        dps = result.get("dps", 0)
        dps_label = QLabel(f"DPS {dps:,.0f}")
        dps_label.setStyleSheet("font-size: 13px; color: palette(mid);")
        top.addWidget(dps_label)

        top.addStretch()

        apply_btn = QPushButton(tr("应用此组合"))
        apply_btn.setFixedHeight(28)
        apply_btn.setStyleSheet(
            "QPushButton { background: #4CAF50; color: white; "
            "border: none; border-radius: 4px; padding: 4px 12px; "
            "font-weight: 600; }"
            "QPushButton:hover { background: #43A047; }"
        )
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
        summary.setStyleSheet("font-size: 12px; color: palette(text);")
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
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._host = host
        self._school = school
        self._scheme = scheme
        # base_attrs 不含弓玦，弓玦属性按需计算
        self._base_attrs_raw = base_attrs
        self._current_gongjue = gongjue
        self._base_attrs = base_attrs + self._compute_gongjue_attrs(gongjue)
        self._level_threshold = level_threshold
        self._affix_filter = affix_filter
        self._worker: _SearchWorker | None = None
        self._slot_groups: dict[str, _SlotGroup] = {}
        self._result_cards: list[_ResultCard] = []

        self.setWindowTitle(tr("最优毕业率组合搜索"))
        self.setMinimumSize(800, 550)
        self.resize(900, 650)
        self._setup_ui()
        self._load_candidates()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        # Header: school + scheme info
        header = QLabel(
            f"{tr('流派')}: {self._school}  |  "
            f"{tr('方案')}: {self._scheme}"
        )
        header.setStyleSheet(
            "font-size: 13px; color: palette(mid); padding: 4px 0;")
        layout.addWidget(header)

        # Options row
        options = QHBoxLayout()
        self._chk_pruning = QCheckBox(tr("智能筛选"))
        self._chk_pruning.setChecked(True)
        self._chk_pruning.setToolTip(
            tr("自动淘汰被其他候选完全压制的装备，缩减搜索空间"))
        options.addWidget(self._chk_pruning)
        self._chk_exclude_mock = QCheckBox(tr("排除模拟"))
        self._chk_exclude_mock.setChecked(True)
        self._chk_exclude_mock.setToolTip(
            tr("搜索时排除模拟装备，仅使用真实背包和已穿戴装备"))
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
        layout.addLayout(options)

        # Tuning row
        tuning_row = QHBoxLayout()
        tuning_row.addWidget(QLabel(tr("弓玦套装：")))
        self._combo_gongjue = QComboBox()
        self._combo_gongjue.addItem(tr("无"), "")
        for gj_type in ["会意", "精准", "会心"]:
            self._combo_gongjue.addItem(gj_type, gj_type)
        # 设置默认选中
        idx = self._combo_gongjue.findData(self._current_gongjue)
        if idx >= 0:
            self._combo_gongjue.setCurrentIndex(idx)
        self._combo_gongjue.currentIndexChanged.connect(
            self._on_gongjue_changed)
        tuning_row.addWidget(self._combo_gongjue)
        tuning_row.addSpacing(16)
        tuning_row.addWidget(QLabel(tr("调律规则：")))
        self._combo_tuning = QComboBox()
        self._combo_tuning.addItem(tr("不应用规则"), None)
        self._combo_tuning.currentIndexChanged.connect(
            self._on_tuning_changed)
        tuning_row.addWidget(self._combo_tuning, 1)
        tuning_row.addStretch()
        layout.addLayout(tuning_row)
        self._load_tuning_options()

        # Search button + progress
        action_row = QHBoxLayout()
        self._btn_search = QPushButton(tr("开始搜索"))
        self._btn_search.setFixedHeight(32)
        self._btn_search.setStyleSheet(
            "QPushButton { background: #1976D2; color: white; "
            "border: none; border-radius: 4px; padding: 6px 20px; "
            "font-weight: 600; font-size: 14px; }"
            "QPushButton:hover { background: #1565C0; }"
            "QPushButton:disabled { background: palette(mid); }"
        )
        self._btn_search.clicked.connect(self._on_search)
        action_row.addWidget(self._btn_search)

        self._btn_cancel = QPushButton(tr("取消"))
        self._btn_cancel.setFixedHeight(32)
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

        action_row.addStretch()
        layout.addLayout(action_row)

        # Tab widget: 候选装备 / 最优结果
        self._tab_widget = QTabWidget()

        # Tab 1: 候选装备 (4×2 grid)
        candidates_tab = QWidget()
        candidates_layout = QVBoxLayout(candidates_tab)
        candidates_layout.setContentsMargins(4, 4, 4, 4)
        grid = QGridLayout()
        grid.setSpacing(6)
        self._slot_scroll_areas: dict[str, QScrollArea] = {}
        for idx, (slot_key, _display_name, _ft) in enumerate(_SLOT_ORDER):
            row = idx // 4
            col = idx % 4
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setHorizontalScrollBarPolicy(
                Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            scroll.setStyleSheet(
                "QScrollArea { border: 1px solid palette(midlight); "
                "border-radius: 4px; }")
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
        results_layout.setContentsMargins(4, 4, 4, 4)
        self._results_inner = QVBoxLayout()
        self._results_inner.setSpacing(4)
        results_layout.addLayout(self._results_inner)
        self._tab_widget.addTab(results_tab, tr("最优结果"))

        layout.addWidget(self._tab_widget, stretch=1)

    def _compute_gongjue_attrs(self, gongjue_type: str) -> CombatAttributes:
        """计算弓玦属性：当前赛季最大等级三率词条上限的一半。"""
        if not gongjue_type:
            return CombatAttributes()
        try:
            from ..config import get_game_config
            from ..core.combat.combat_attrs import compute_gongjue_attrs
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
        from ..config import get_game_config
        from ..core.evaluator import get_tuning_rules

        school_cfg = get_game_config().get_schools().get(self._school, {})
        main_weapon = (school_cfg.get("main") or {}).get("weapon", "")
        sub_weapon = (school_cfg.get("sub") or {}).get("weapon", "")
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
        from ..core.graduation.combo_rules import judge_tuning_candidate
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

    def _load_candidates(self) -> None:
        """从 session 加载候选装备并按槽位分组。

        数据来源：equipped（已穿戴）+ bag_items（背包）。
        武器类型过滤：主武器只保留与流派 main.weapon 同类型的武器，
        副武器只保留与流派 sub.weapon 同类型的武器。
        """
        user_name = self._host.active_user_name()
        if not user_name:
            return

        try:
            from ..core.combat.equipment import EquipmentInventory
            inv = EquipmentInventory(user_name)
        except Exception as e:
            logger.error(f"加载装备数据失败: {e}")
            return

        equipped = inv.equipped
        bag_items = inv.bag_items

        # 读取流派配置的主/副武器类型
        from ..config import get_game_config
        gc = get_game_config()
        school_cfg = gc.get_schools().get(self._school, {})
        main_weapon_type = (school_cfg.get("main") or {}).get("weapon", "")
        sub_weapon_type = (school_cfg.get("sub") or {}).get("weapon", "")
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

        # 1. 已穿戴装备：先按流派武器和装备页筛选条件校验
        for slot_key, eq in equipped.items():
            if (isinstance(eq, dict) and slot_key in slot_candidates
                    and not (exclude_mock and _is_mock(eq))
                    and self._candidate_passes(slot_key, eq,
                                               main_weapon_type, sub_weapon_type)):
                slot_candidates[slot_key].append(eq)

        # 2. 背包装备：按 group_key 分发到槽位
        if isinstance(bag_items, dict):
            for group_key, items_dict in bag_items.items():
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

    def _candidate_passes(
        self, slot_key: str, equip: dict,
        main_weapon_type: str, sub_weapon_type: str,
    ) -> bool:
        """统一应用流派武器、等级和词条筛选。"""
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
            return isinstance(dingyin, dict) and bool(dingyin.get("name"))
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
        self._progress.setVisible(True)
        self._progress_label.setVisible(True)
        self._progress.setMaximum(max(total, 1))
        self._progress.setValue(0)
        self._progress_label.setText(f"0 / {total:,}")
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
            from ..config import get_game_config
            gc = get_game_config()
            season = gc.current_season()
            if season and season.equip_level:
                full_level = season.equip_level
            else:
                configs = gc.get_level_configs()
                full_level = configs[-1].level if configs else 0
        self._worker = _SearchWorker(
            candidates,
            self._school,
            self._scheme,
            self._base_attrs,
            self._chk_pruning.isChecked(),
            full_chengyin=self._chk_full_chengyin.isChecked(),
            full_dingyin=self._chk_full_dingyin.isChecked(),
            full_level=full_level,
        )
        self._worker.signals.progress.connect(self._on_progress)
        self._worker.signals.finished.connect(self._on_finished)
        self._worker.signals.error.connect(self._on_error)
        pool = QThreadPool.globalInstance()
        if pool is not None:
            pool.start(self._worker)

    def _on_cancel(self) -> None:
        if self._worker:
            self._worker.cancel()

    @pyqtSlot(int, int, str)
    def _on_progress(self, evaluated: int, total: int, message: str) -> None:
        self._progress.setValue(evaluated)
        self._progress_label.setText(
            f"{evaluated:,} / {total:,}" + (f"  {message}" if message else ""))

    @pyqtSlot(list)
    def _on_finished(self, results: list) -> None:
        self._btn_search.setVisible(True)
        self._btn_cancel.setVisible(False)
        self._progress.setVisible(False)
        self._progress_label.setVisible(False)

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
        self._btn_search.setVisible(True)
        self._btn_cancel.setVisible(False)
        self._progress.setVisible(False)
        self._progress_label.setVisible(False)
        QMessageBox.critical(self, tr("搜索失败"), message)

    def _on_apply_result(self, equipped: dict) -> None:
        """将搜索结果的装备组合写入 session（按槽位合并，不覆盖未参与槽位）。"""
        user_name = self._host.active_user_name()
        if not user_name:
            QMessageBox.warning(self, tr("提示"), tr("没有激活的用户"))
            return

        try:
            from ..core.combat.equipment import EquipmentInventory
            inv = EquipmentInventory(user_name)
            inv.apply_combos(equipped)

            # Notify host
            self._host.equipment_changed.emit()
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
