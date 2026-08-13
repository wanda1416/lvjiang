"""装备调律验证对话框

装备调律配置对话框顶部按钮入口：纯手工构造装备验证判定器，
改规则后可立即验证。
左侧为调律规则配置（TuningConfigWidget，初值取自插件会话调律配置，
改动不回写 session）；右侧手选 部位 + 品阶 + 词条 1-5（数值默认
承音 94%），点「判定」输出调律潜力结论，词条满 5 条时追加各启用
规则的完整定级。词条名一律为 attributes.yaml 标准字段。
"""
from __future__ import annotations

from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from lvjiang.apps.yysls.equip_parser.constants import WEAPON_TYPES
from lvjiang.apps.yysls.equip_parser.models import Affix, EquipmentData
from lvjiang.apps.yysls.evaluator import (
    get_tuning_judge,
    is_rule_implemented,
    judge_tuning_worthiness,
)
from lvjiang.apps.yysls.game_config import get_game_config

from .tune_config_widget import TuningConfigWidget

# 部位下拉：武器合并为单项 + 首饰 + 防具（共 7 项）；
# 选中「武器」时另出二级下拉选具体武器，避免武器与部位混叠
PART_WEAPON = "武器"
PART_ITEMS: list[str] = [PART_WEAPON, "环", "佩", "冠胄", "胸甲", "胫甲", "腕甲"]

# 调律词条池（词条 2-5，全部位可出，attributes.yaml 标准字段名）
_COMMON_AFFIXES: list[str] = [
    "最大外功攻击", "最小外功攻击",
    "劲", "势", "敏", "体", "御",
    "会意率", "会心率", "精准率",
    *[f"{prefix}{attr}攻击"
      for attr in ("无相", "裂石", "牵丝", "破竹", "鸣金")
      for prefix in ("最大", "最小")],
]

# 初始词条池（词条 1，按部位区分，源：01-equipment-system.md 三.1）
_INITIAL_WEAPON: list[str] = [
    "最大外功攻击", "最小外功攻击",
    "最大无相攻击", "最小无相攻击", "敏", "势",
]
_INITIAL_JEWELRY: list[str] = ["最大外功攻击", "最小外功攻击"]
_INITIAL_HEAD_CHEST: list[str] = [
    "会心率", "会意率", "精准率", "气血最大值", "外功防御",
]
_INITIAL_LEG_WRIST: list[str] = [
    "会心率", "会意率", "劲", "精准率", "体", "御",
    "气血最大值", "外功防御",
]
_INITIAL_AFFIXES: dict[str, list[str]] = {
    **{w: _INITIAL_WEAPON for w in WEAPON_TYPES},
    "环": _INITIAL_JEWELRY, "佩": _INITIAL_JEWELRY,
    "冠胄": _INITIAL_HEAD_CHEST, "胸甲": _INITIAL_HEAD_CHEST,
    "胫甲": _INITIAL_LEG_WRIST, "腕甲": _INITIAL_LEG_WRIST,
}

# 武器 → 专属武学增伤/增效词条
_WEAPON_WUXUE: dict[str, list[str]] = {
    "陌刀": ["陌刀武学增伤"],
    "舞绫鼓": ["舞绫鼓武学增伤"],
    "双刀": ["双刀武学增伤"],
    "绳镖": ["绳镖武学增伤"],
    "横刀": ["横刀武学增伤"],
    "手甲": ["手甲武学增伤"],
    "剑": ["剑武学增伤"],
    "枪": ["枪武学增伤"],
    "扇": ["扇武学增伤", "扇武学增效"],
    "伞": ["伞武学增伤"],
}

# 非武器部位 → 专属神力词条
_PART_EXTRA: dict[str, list[str]] = {
    "环": ["全武学增效"],
    "佩": ["全武学增效"],
    "冠胄": ["单体类奇术增伤"],
    "胸甲": ["单体类奇术增伤"],
    "胫甲": ["对首领单位增伤", "对玩家单位增效"],
    "腕甲": ["对首领单位增伤", "对玩家单位增效"],
}

_NONE_ITEM = "（未选）"
_LEVEL = 110
_AFFIX_ROWS = 5


class EquipAffixEditor(QWidget):
    """手工装备编辑器：部位（+武器二级选择）+ 品阶 + 词条 1-5

    - 部位下拉仅 7 项（武器/环/佩/冠胄/胸甲/胫甲/腕甲），
      选「武器」时出现二级下拉选具体武器；
    - 词条 1 为初始词条，候选仅为各部位初始词条池（增伤类神力
      词条不会是首词条）；词条 2-5 为调律词条，候选为通用调律池
      + 对应部位神力；
    - 部位变更 → 重建全部词条候选并清空已选与数值；
    - 选中词条 → 数值自动填该等级承音值（cap×94%），可手改；
    - 词条 2-5 互不重复（词条 1 不受限，允许冠胄「会心率×2」）。
    """

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._updating = False  # 程序性刷新期间屏蔽联动
        form = QFormLayout(self)
        form.setContentsMargins(0, 0, 0, 0)

        self.part_combo = QComboBox()
        self.part_combo.addItems(PART_ITEMS)
        self.part_combo.currentIndexChanged.connect(self._on_part_changed)
        form.addRow("部位：", self.part_combo)

        # 二级选择：部位为「武器」时显示，选具体武器
        self._weapon_label = QLabel("武器：")
        self.weapon_combo = QComboBox()
        self.weapon_combo.addItems(WEAPON_TYPES)
        self.weapon_combo.currentIndexChanged.connect(
            self._rebuild_affix_options)
        form.addRow(self._weapon_label, self.weapon_combo)

        self.quality_combo = QComboBox()
        self.quality_combo.addItem("金色", "gold")
        self.quality_combo.addItem("紫色", "purple")
        form.addRow("品阶：", self.quality_combo)

        # 词条 1-5 行：下拉 + 数值
        self._affix_combos: list[QComboBox] = []
        self._affix_spins: list[QDoubleSpinBox] = []
        for i in range(_AFFIX_ROWS):
            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            combo = QComboBox()
            combo.currentIndexChanged.connect(
                lambda _idx, r=i: self._on_affix_selected(r))
            spin = QDoubleSpinBox()
            spin.setRange(0, 99999)
            spin.setDecimals(2)
            row_layout.addWidget(combo, stretch=1)
            row_layout.addWidget(spin)
            form.addRow(f"词条{i + 1}：", row)
            self._affix_combos.append(combo)
            self._affix_spins.append(spin)

        self._on_part_changed()

    # ─── 候选池 ──────────────────────────────────────────────

    def current_type(self) -> str:
        """当前装备类型：部位为武器时取二级下拉的具体武器"""
        part = self.part_combo.currentText()
        return (self.weapon_combo.currentText()
                if part == PART_WEAPON else part)

    def _on_part_changed(self):
        """部位变更：切换二级武器下拉可见性并重建词条候选"""
        is_weapon = self.part_combo.currentText() == PART_WEAPON
        self._weapon_label.setVisible(is_weapon)
        self.weapon_combo.setVisible(is_weapon)
        self._rebuild_affix_options()

    def _divine_affixes(self) -> list[str]:
        """当前部位的专属神力词条（仅调律可得，不会是首词条）"""
        equip_type = self.current_type()
        return (_WEAPON_WUXUE.get(equip_type)
                or _PART_EXTRA.get(equip_type) or [])

    def _initial_candidates(self) -> list[str]:
        """词条 1 候选：仅部位初始词条池"""
        return list(_INITIAL_AFFIXES.get(self.current_type(), []))

    def _tuning_candidates(self) -> list[str]:
        """词条 2-5 候选：通用调律池 + 部位神力"""
        return [*_COMMON_AFFIXES, *self._divine_affixes()]

    def _rebuild_affix_options(self):
        """部位变更：重建全部词条候选并清空已选与数值"""
        self._updating = True
        initial = self._initial_candidates()
        tuning = self._tuning_candidates()
        for i, (combo, spin) in enumerate(
                zip(self._affix_combos, self._affix_spins, strict=False)):
            combo.clear()
            combo.addItem(_NONE_ITEM)
            combo.addItems(initial if i == 0 else tuning)
            spin.setValue(0)
        self._updating = False

    def _refresh_dedup(self):
        """词条 2-5 去重：排除其他 2-5 行已选名，保留自身当前选中"""
        self._updating = True
        names = self._tuning_candidates()
        selected = [c.currentText() for c in self._affix_combos]
        for i in range(1, _AFFIX_ROWS):
            combo = self._affix_combos[i]
            own = selected[i]
            taken = {selected[j] for j in range(1, _AFFIX_ROWS)
                     if j != i and selected[j] != _NONE_ITEM}
            combo.clear()
            combo.addItem(_NONE_ITEM)
            combo.addItems([n for n in names if n not in taken])
            idx = combo.findText(own)
            combo.setCurrentIndex(idx if idx >= 0 else 0)
        self._updating = False

    def _on_affix_selected(self, row: int):
        """选中词条：自动填承音值（94% cap）并刷新 2-5 去重"""
        if self._updating:
            return
        name = self._affix_combos[row].currentText()
        if name != _NONE_ITEM:
            caps = get_game_config().get_affix_caps(_LEVEL, name)
            if caps is not None:
                self._affix_spins[row].setValue(caps["chengyin"])
        else:
            self._affix_spins[row].setValue(0)
        self._refresh_dedup()

    # ─── 装备构造 ────────────────────────────────────────────

    def get_equipment(self) -> EquipmentData | None:
        """按当前选择构造装备（无任何词条时返回 None）"""
        mgr = get_game_config()
        affixes: list[Affix] = []
        for combo, spin in zip(self._affix_combos, self._affix_spins,
                               strict=False):
            name = combo.currentText()
            if name == _NONE_ITEM:
                continue
            caps = mgr.get_affix_caps(_LEVEL, name)
            unit = caps["unit"] or None if caps else None
            affixes.append(Affix(name=name, value=spin.value(), unit=unit))
        if not affixes:
            return None
        return EquipmentData(
            type=self.current_type(),
            name="测试装备",
            level=_LEVEL,
            quality=self.quality_combo.currentData(),
            affixes=affixes,
        )


class EquipJudgeTestDialog(QDialog):
    """装备识别测试面板（左：流派配置；右：装备编辑 + 判定输出）"""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("装备调律验证")
        self.resize(860, 620)
        layout = QHBoxLayout(self)

        # ── 左：流派配置（读 session 初值，不回写）──
        left = QVBoxLayout()
        left.addWidget(QLabel("<b>流派配置（仅本次测试，不保存）：</b>"))
        # 可转律开关：取消后潜力判定放弃转律模拟（仅按空槽评估）
        self._chk_transmute = QCheckBox("可转律（取消后不做转律模拟）")
        self._chk_transmute.setChecked(True)
        left.addWidget(self._chk_transmute)
        self._tuning_config = TuningConfigWidget()
        rules_cfg, switches = self._load_session_tuning()
        self._tuning_config.set_config(rules_cfg)
        self._tuning_config.set_switches(switches)
        rules_scroll = QScrollArea()
        rules_scroll.setWidgetResizable(True)
        rules_scroll.setWidget(self._tuning_config)
        left.addWidget(rules_scroll)
        layout.addLayout(left, stretch=1)

        # ── 右：装备编辑 + 判定 ──
        right = QVBoxLayout()
        right.addWidget(QLabel("<b>构造装备：</b>"))
        self.editor = EquipAffixEditor()
        right.addWidget(self.editor)
        self.btn_judge = QPushButton("判定")
        self.btn_judge.clicked.connect(self._on_judge)
        right.addWidget(self.btn_judge)
        self.result_text = QTextEdit()
        self.result_text.setReadOnly(True)
        right.addWidget(self.result_text, stretch=1)
        layout.addLayout(right, stretch=1)

    @staticmethod
    def _load_session_tuning() -> tuple[dict, dict]:
        """读取调律 Tab 已保存的规则配置与全局开关作为初值（插件会话）"""
        from ..tune_config import TuneConfig
        tc = TuneConfig.load()
        return tc.rules, tc.switches

    def _on_judge(self):
        switches = self._tuning_config.get_switches()
        can_transmute = self._chk_transmute.isChecked()
        configs = {
            k: {**cfg, "switches": switches, "can_transmute": can_transmute}
            for k, cfg in self._tuning_config.get_config().items()
            if cfg.get("enabled")
        }
        if not configs:
            self.result_text.setPlainText("请先在左侧启用至少一个调律规则")
            return
        equip = self.editor.get_equipment()
        if equip is None:
            self.result_text.setPlainText("请至少选择首词条")
            return

        worth, logs = judge_tuning_worthiness(
            equip, configs, rule_keys=list(configs))
        lines = [
            "【调律潜力】" + ("值得调律" if worth else "不值得调律"),
            *logs,
        ]

        # 词条满 5 条：追加各启用且已实现流派的完整定级
        if len(equip.affixes) == _AFFIX_ROWS:
            lines.append("")
            lines.append("【完整定级】")
            for key, cfg in configs.items():
                if not is_rule_implemented(key):
                    continue
                judge = get_tuning_judge(key, cfg)
                res = judge.judge(equip)
                if res.not_applicable:
                    tag = "不适用"
                elif res.skipped:
                    tag = "跳过"
                else:
                    tag = res.rating.value
                lines.append(
                    f"{judge.rule_name}: {tag}（{'；'.join(res.reasons)}）")
        self.result_text.setPlainText("\n".join(lines))
