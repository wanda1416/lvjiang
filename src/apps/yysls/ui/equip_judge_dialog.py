"""装备识别测试对话框

装备调律规则对话框顶部按钮入口：纯手工构造装备验证判定器，
改规则后可立即验证。
左侧为流派配置（SchoolConfigWidget，初值取自插件会话调律配置，
改动不回写 session）；右侧手选 部位 + 品阶 + 词条 1-5（数值默认
承音 94%），点「判定」输出调律潜力结论，词条满 5 条时追加各启用
流派的完整定级。词条名一律为 attributes.yaml 标准字段。
"""
from __future__ import annotations

from PyQt6.QtWidgets import (
    QDialog, QWidget, QHBoxLayout, QVBoxLayout, QFormLayout, QLabel,
    QComboBox, QDoubleSpinBox, QPushButton, QTextEdit, QScrollArea,
)

from src.apps.yysls.equip_parser.constants import WEAPON_TYPES
from src.apps.yysls.equip_parser.models import Affix, EquipmentData
from src.apps.yysls.evaluator import (
    get_attr_rule_manager, get_school_judge, is_school_implemented,
    judge_tuning_worthiness,
)
from .school_config_widget import SchoolConfigWidget


# 部位下拉：10 武器 + 首饰 + 防具（共 16 项）
EQUIP_TYPES: list[str] = [*WEAPON_TYPES, "环", "佩", "冠胄", "胸甲", "胫甲", "腕甲"]

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
    """手工装备编辑器：部位 + 品阶 + 词条 1-5

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
        self.part_combo.addItems(EQUIP_TYPES)
        self.part_combo.currentIndexChanged.connect(self._rebuild_affix_options)
        form.addRow("部位：", self.part_combo)

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

        self._rebuild_affix_options()

    # ─── 候选池 ──────────────────────────────────────────────

    def _divine_affixes(self) -> list[str]:
        """当前部位的专属神力词条（仅调律可得，不会是首词条）"""
        part = self.part_combo.currentText()
        return _WEAPON_WUXUE.get(part) or _PART_EXTRA.get(part) or []

    def _initial_candidates(self) -> list[str]:
        """词条 1 候选：仅部位初始词条池"""
        return list(_INITIAL_AFFIXES.get(self.part_combo.currentText(), []))

    def _tuning_candidates(self) -> list[str]:
        """词条 2-5 候选：通用调律池 + 部位神力"""
        return [*_COMMON_AFFIXES, *self._divine_affixes()]

    def _rebuild_affix_options(self):
        """部位变更：重建全部词条候选并清空已选与数值"""
        self._updating = True
        initial = self._initial_candidates()
        tuning = self._tuning_candidates()
        for i, (combo, spin) in enumerate(
                zip(self._affix_combos, self._affix_spins)):
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
            caps = get_attr_rule_manager().get_affix_caps(_LEVEL, name)
            if caps is not None:
                self._affix_spins[row].setValue(caps["chengyin"])
        else:
            self._affix_spins[row].setValue(0)
        self._refresh_dedup()

    # ─── 装备构造 ────────────────────────────────────────────

    def get_equipment(self) -> EquipmentData | None:
        """按当前选择构造装备（无任何词条时返回 None）"""
        mgr = get_attr_rule_manager()
        affixes: list[Affix] = []
        for combo, spin in zip(self._affix_combos, self._affix_spins):
            name = combo.currentText()
            if name == _NONE_ITEM:
                continue
            caps = mgr.get_affix_caps(_LEVEL, name)
            unit = caps["unit"] or None if caps else None
            affixes.append(Affix(name=name, value=spin.value(), unit=unit))
        if not affixes:
            return None
        return EquipmentData(
            type=self.part_combo.currentText(),
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
        self._school_config = SchoolConfigWidget()
        schools_cfg, keep_pvp = self._load_session_tuning()
        self._school_config.set_config(schools_cfg)
        self._school_config.set_keep_pvp(keep_pvp)
        school_scroll = QScrollArea()
        school_scroll.setWidgetResizable(True)
        school_scroll.setWidget(self._school_config)
        left.addWidget(school_scroll)
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
    def _load_session_tuning() -> tuple[dict, bool]:
        """读取调律 Tab 已保存的流派配置与全局 PVP 开关作为初值（插件会话）"""
        from ..session import get_plugin_session
        section = get_plugin_session().get_section("tuning")
        keep_pvp = bool(section.get("keep_pvp", False))
        raw = section.get("schools")
        if isinstance(raw, dict):
            return raw, keep_pvp
        return {}, keep_pvp

    def _on_judge(self):
        keep_pvp = self._school_config.get_keep_pvp()
        configs = {
            k: {**cfg, "keep_pvp": keep_pvp}
            for k, cfg in self._school_config.get_config().items()
            if cfg.get("enabled")
        }
        if not configs:
            self.result_text.setPlainText("请先在左侧启用至少一个流派")
            return
        equip = self.editor.get_equipment()
        if equip is None:
            self.result_text.setPlainText("请至少选择首词条")
            return

        worth, logs = judge_tuning_worthiness(
            equip, configs, schools=list(configs))
        lines = [
            "【调律潜力】" + ("值得调律" if worth else "不值得调律"),
            *logs,
        ]

        # 词条满 5 条：追加各启用且已实现流派的完整定级
        if len(equip.affixes) == _AFFIX_ROWS:
            lines.append("")
            lines.append("【完整定级】")
            for key, cfg in configs.items():
                if not is_school_implemented(key):
                    continue
                judge = get_school_judge(key, cfg)
                res = judge.judge(equip)
                if res.not_applicable:
                    tag = "不适用"
                elif res.skipped:
                    tag = "跳过"
                else:
                    tag = res.rating.value
                lines.append(
                    f"{judge.school_name}: {tag}（{'；'.join(res.reasons)}）")
        self.result_text.setPlainText("\n".join(lines))
