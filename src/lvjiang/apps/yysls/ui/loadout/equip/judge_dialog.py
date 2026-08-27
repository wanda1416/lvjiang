"""装备调律验证对话框

装备调律配置对话框顶部按钮入口：纯手工构造装备验证判定器，
改规则后可立即验证。
左侧为调律规则配置（TuningConfigWidget，初值取自 wf_configs 调律配置，
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

from lvjiang.apps.yysls.config import get_game_config
from lvjiang.apps.yysls.core.equip_parser.constants import WEAPON_TYPES
from lvjiang.apps.yysls.core.equip_parser.models import Affix, EquipmentData
from lvjiang.apps.yysls.core.evaluator import (
    get_tuning_judge,
    is_rule_implemented,
    judge_equipment_potential,
    judge_tuning_worthiness,
)
from lvjiang.apps.yysls.core.tuning_rules import (
    RATING_LABELS,
    RATING_RANK,
    get_tuning_group,
)
from lvjiang.apps.yysls.core.tuning_rules.models import FOOD_LABELS

from ......i18n import tr
from ...game_settings.level_combo import LevelCombo
from ...tuning.config_widget import TuningConfigWidget

# 部位下拉：武器合并为单项 + 首饰 + 防具（共 7 项）；
# 选中「武器」时另出二级下拉选具体武器，避免武器与部位混叠
PART_WEAPON = tr("武器")
PART_ITEMS: list[str] = [PART_WEAPON, tr("环"), tr("佩"), tr("冠胄"), tr("胸甲"), tr("胫甲"), tr("腕甲")]

# 调律词条池（词条 2-5，全部位可出，attributes.yaml 标准字段名）
_COMMON_AFFIXES: list[str] = [
    tr("最大外功攻击"), tr("最小外功攻击"),
    tr("劲"), tr("势"), tr("敏"), tr("体"), tr("御"),
    tr("会意率"), tr("会心率"), tr("精准率"),
    *[f"{prefix}{attr}攻击"
      for attr in (tr("无相"), tr("裂石"), tr("牵丝"), tr("破竹"), tr("鸣金"))
      for prefix in (tr("最大"), tr("最小"))],
]

# 初始词条池（词条 1，按部位区分，源：01-equipment-system.md 三.1）
_INITIAL_WEAPON: list[str] = [
    tr("最大外功攻击"), tr("最小外功攻击"),
    tr("最大无相攻击"), tr("最小无相攻击"), tr("敏"), tr("势"),
]
_INITIAL_JEWELRY: list[str] = [tr("最大外功攻击"), tr("最小外功攻击")]
_INITIAL_HEAD_CHEST: list[str] = [
    tr("会心率"), tr("会意率"), tr("精准率"), tr("气血最大值"), tr("外功防御"),
]
_INITIAL_LEG_WRIST: list[str] = [
    tr("会心率"), tr("会意率"), tr("劲"), tr("精准率"), tr("体"), tr("御"),
    tr("气血最大值"), tr("外功防御"),
]
_INITIAL_AFFIXES: dict[str, list[str]] = {
    **{w: _INITIAL_WEAPON for w in WEAPON_TYPES},
    tr("环"): _INITIAL_JEWELRY, tr("佩"): _INITIAL_JEWELRY,
    tr("冠胄"): _INITIAL_HEAD_CHEST, tr("胸甲"): _INITIAL_HEAD_CHEST,
    tr("胫甲"): _INITIAL_LEG_WRIST, tr("腕甲"): _INITIAL_LEG_WRIST,
}

# 武器 → 专属武学增伤/增效词条
_WEAPON_WUXUE: dict[str, list[str]] = {
    tr("陌刀"): [tr("陌刀武学增伤")],
    tr("舞绫鼓"): [tr("舞绫鼓武学增伤")],
    tr("双刀"): [tr("双刀武学增伤")],
    tr("绳镖"): [tr("绳镖武学增伤")],
    tr("横刀"): [tr("横刀武学增伤")],
    tr("手甲"): [tr("手甲武学增伤")],
    tr("剑"): [tr("剑武学增伤")],
    tr("枪"): [tr("枪武学增伤")],
    tr("扇"): [tr("扇武学增伤"), tr("扇武学增效")],
    tr("伞"): [tr("伞武学增伤")],
}

# 非武器部位 → 专属神力词条
_PART_EXTRA: dict[str, list[str]] = {
    tr("环"): [tr("全武学增效")],
    tr("佩"): [tr("全武学增效")],
    tr("冠胄"): [tr("单体类奇术增伤")],
    tr("胸甲"): [tr("单体类奇术增伤")],
    tr("胫甲"): [tr("对首领单位增伤"), tr("对玩家单位增效")],
    tr("腕甲"): [tr("对首领单位增伤"), tr("对玩家单位增效")],
}

_NONE_ITEM = tr("（未选）")
_AFFIX_ROWS = 5

# 模拟模式虚拟库存（假定材料充足，不统计消耗）
_DUMMY_STOCKS: dict[str, int] = {label: 999 for label in FOOD_LABELS}


def _best_rating_key(pot: dict[str, dict],
                     label_to_key: dict[str, str]) -> str | None:
    """从潜力判定结果提取最高评级 key（跳过/不适用规则不参与）"""
    best: str | None = None
    for r in pot.values():
        if r.get("skipped") or r.get("not_applicable"):
            continue
        k = label_to_key.get(r.get("rating", ""))
        if k and (best is None or RATING_RANK[k] > RATING_RANK[best]):
            best = k
    return best


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
        form.addRow(tr("部位："), self.part_combo)

        # 二级选择：部位为「武器」时显示，选具体武器
        self._weapon_label = QLabel(tr("武器："))
        self.weapon_combo = QComboBox()
        self.weapon_combo.addItems(WEAPON_TYPES)
        self.weapon_combo.currentIndexChanged.connect(
            self._rebuild_affix_options)
        form.addRow(self._weapon_label, self.weapon_combo)

        self.quality_combo = QComboBox()
        self.quality_combo.addItem(tr("金色"), "gold")
        self.quality_combo.addItem(tr("紫色"), "purple")
        form.addRow(tr("品阶："), self.quality_combo)

        # 等级选择（从等级配置中选择）
        self._level_combo = LevelCombo(allow_empty=False)
        form.addRow(tr("等级："), self._level_combo)

        # 词条 1-5 行：下拉 + 数值 + 待调出复选框（仅词条 2-5）
        self._affix_combos: list[QComboBox] = []
        self._affix_spins: list[QDoubleSpinBox] = []
        self._tune_checkboxes: list[QCheckBox] = []  # 仅词条 2-5，长度 4
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
            # 词条 2-5 添加"待调出"复选框
            if i > 0:
                chk = QCheckBox(tr("待调出"))
                chk.setToolTip(tr("勾选表示该词条是调律过程中调出的，非扫描时已有"))
                row_layout.addWidget(chk)
                self._tune_checkboxes.append(chk)
            form.addRow(tr("词条{i}：").format(i=i + 1), row)
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
        for chk in self._tune_checkboxes:
            chk.setChecked(False)
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
            level = self._level_combo.get_level()
            caps = get_game_config().get_affix_caps(level, name) if level else None
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
            level = self._level_combo.get_level()
            caps = mgr.get_affix_caps(level, name) if level else None
            unit = caps["unit"] or None if caps else None
            affixes.append(Affix(name=name, value=spin.value(), unit=unit))
        if not affixes:
            return None
        return EquipmentData(
            type=self.current_type(),
            name=tr("测试装备"),
            level=self._level_combo.get_level() or 0,
            quality=self.quality_combo.currentData(),
            affixes=affixes,
        )

    def get_scanned_affixes(self) -> list[Affix]:
        """获取扫描时已有的词条（未勾选"待调出"的词条）"""
        mgr = get_game_config()
        affixes: list[Affix] = []
        # 词条 1 始终是扫描时已有
        combo = self._affix_combos[0]
        spin = self._affix_spins[0]
        name = combo.currentText()
        if name != _NONE_ITEM:
            level = self._level_combo.get_level()
            caps = mgr.get_affix_caps(level, name) if level else None
            unit = caps["unit"] or None if caps else None
            affixes.append(Affix(name=name, value=spin.value(), unit=unit))
        # 词条 2-5：未勾选"待调出"的
        for combo, spin, chk in zip(
                self._affix_combos[1:], self._affix_spins[1:],
                self._tune_checkboxes, strict=False):
            if chk.isChecked():
                continue  # 勾选"待调出"的不算扫描时已有
            name = combo.currentText()
            if name == _NONE_ITEM:
                continue
            level = self._level_combo.get_level()
            caps = mgr.get_affix_caps(level, name) if level else None
            unit = caps["unit"] or None if caps else None
            affixes.append(Affix(name=name, value=spin.value(), unit=unit))
        return affixes

    def get_tune_affixes(self) -> list[Affix]:
        """获取待调出的词条（勾选"待调出"的词条，按顺序）"""
        mgr = get_game_config()
        affixes: list[Affix] = []
        for combo, spin, chk in zip(self._affix_combos[1:], self._affix_spins[1:],
                                     self._tune_checkboxes, strict=False):
            if not chk.isChecked():
                continue
            name = combo.currentText()
            if name == _NONE_ITEM:
                continue
            level = self._level_combo.get_level()
            caps = mgr.get_affix_caps(level, name) if level else None
            unit = caps["unit"] or None if caps else None
            affixes.append(Affix(name=name, value=spin.value(), unit=unit))
        return affixes


class EquipJudgeTestDialog(QDialog):
    """装备识别测试面板（左：流派配置；右：装备编辑 + 判定输出）"""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle(tr("装备调律验证"))
        self.resize(860, 620)
        layout = QHBoxLayout(self)

        # ── 左：流派配置（读 session 初值，不回写）──
        left = QVBoxLayout()
        left.addWidget(QLabel("<b>" + tr("流派配置（仅本次测试，不保存）：") + "</b>"))
        # 可转律开关：取消后潜力判定放弃转律模拟（仅按空槽评估）
        self._chk_transmute = QCheckBox(tr("可转律（取消后不做转律模拟）"))
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
        right.addWidget(QLabel("<b>" + tr("构造装备：") + "</b>"))
        self.editor = EquipAffixEditor()
        right.addWidget(self.editor)
        btn_row = QHBoxLayout()
        self.btn_judge = QPushButton(tr("判定"))
        self.btn_judge.clicked.connect(self._on_judge)
        btn_row.addWidget(self.btn_judge)
        self.btn_simulate = QPushButton(tr("模拟调律"))
        self.btn_simulate.clicked.connect(self._on_simulate)
        btn_row.addWidget(self.btn_simulate)
        right.addLayout(btn_row)
        self.result_text = QTextEdit()
        self.result_text.setReadOnly(True)
        right.addWidget(self.result_text, stretch=1)
        layout.addLayout(right, stretch=1)

    @staticmethod
    def _load_session_tuning() -> tuple[dict, dict]:
        """读取调律 Tab 已保存的规则配置与全局开关作为初值（统一存储）"""
        from ......core.config.wf_configs import get_wf_config
        tc = get_wf_config("auto_tuning")
        return tc.get("rules", {}), tc.get("switches", {})

    def _on_judge(self):
        switches = self._tuning_config.get_switches()
        can_transmute = self._chk_transmute.isChecked()
        configs = {
            k: {**cfg, "switches": switches, "can_transmute": can_transmute}
            for k, cfg in self._tuning_config.get_config().items()
            if cfg.get("enabled")
        }
        if not configs:
            self.result_text.setPlainText(tr("请先在左侧启用至少一个调律规则"))
            return
        equip = self.editor.get_equipment()
        if equip is None:
            self.result_text.setPlainText(tr("请至少选择首词条"))
            return

        worth, logs = judge_tuning_worthiness(
            equip, configs, rule_keys=list(configs))
        lines = [
            tr("【调律潜力】") + (tr("值得调律") if worth else tr("不值得调律")),
            *logs,
        ]

        # 词条满 5 条：追加各启用且已实现流派的完整定级
        if len(equip.affixes) == _AFFIX_ROWS:
            lines.append("")
            lines.append(tr("【完整定级】"))
            for key, cfg in configs.items():
                if not is_rule_implemented(key):
                    continue
                judge = get_tuning_judge(key, cfg)
                res = judge.judge(equip)
                if res.not_applicable:
                    tag = tr("不适用")
                elif res.skipped:
                    tag = tr("跳过")
                else:
                    tag = res.rating.value
                lines.append(
                    f"{judge.rule_name}: {tag}（{'；'.join(res.reasons)}）")
        self.result_text.setPlainText("\n".join(lines))

    # ─── 模拟调律 ────────────────────────────────────────────

    def _on_simulate(self):
        """模拟完整单件调律流程，输出紧凑日志"""
        switches = self._tuning_config.get_switches()
        can_transmute = self._chk_transmute.isChecked()
        configs = {
            k: {**cfg, "switches": switches, "can_transmute": can_transmute}
            for k, cfg in self._tuning_config.get_config().items()
            if cfg.get("enabled")
        }
        if not configs:
            self.result_text.setPlainText(tr("请先在左侧启用至少一个调律规则"))
            return

        scanned = self.editor.get_scanned_affixes()
        tune_affixes = self.editor.get_tune_affixes()
        if not scanned:
            self.result_text.setPlainText(tr("请至少选择首词条"))
            return
        if not tune_affixes:
            self.result_text.setPlainText(
                tr("请勾选至少一个「待调出」词条进行模拟"))
            return

        # 加载基础规则组
        from ......core.config.wf_configs import get_wf_config
        group_key = get_wf_config("auto_tuning").get("base_group", "default")
        group = get_tuning_group(group_key)
        if group is None:
            self.result_text.setPlainText(tr("基础规则组 {key} 不存在").format(key=repr(group_key)))
            return

        mgr = get_game_config()
        level = self.editor._level_combo.get_level()
        quality = self.editor.quality_combo.currentData()

        # 构造初始装备（仅扫描已有词条）
        equip = EquipmentData(
            type=self.editor.current_type(),
            name=tr("测试装备"),
            level=level or 0,
            quality=quality,
            affixes=list(scanned),
        )
        equip_name = equip.name or equip.type
        equip_part = equip.part
        equip_quality = equip.quality

        # 首词条 cap_pct = 承音值 / 等级最大值 × 100
        first = equip.affixes[0]
        first_caps = mgr.get_affix_caps(level, first.name) if level else None
        cap_pct = int(first.value / first_caps["cap"] * 100) \
            if first_caps and first_caps.get("cap") else None

        log: list[str] = []
        log.append(f"══ {equip_name} {equip_part} {equip_quality} "
                   f"首词条{first.name} {cap_pct}% ══")
        log.append(f"扫描词条: {', '.join(a.name for a in scanned)}")
        log.append(f"待调出: {', '.join(a.name for a in tune_affixes)}")
        log.append(f"规则组: {group.name}")

        # 等级门槛
        if level and level < group.scan.min_level:
            log.append(f"等级{level} < 门槛{group.scan.min_level} → 跳过")
            self.result_text.setPlainText("\n".join(log))
            return

        # 评级提供者（行为规则用）
        rule_keys = list(configs.keys())

        def rating_of(scope: str, keys: list[str],
                      fao: bool = False) -> str:
            target = equip
            if fao and len(equip.affixes) > 1:
                target = EquipmentData(
                    type=equip.type, name=equip.name,
                    level=equip.level, quality=equip.quality,
                    affixes=equip.affixes[:1],
                    extra_data={**equip.extra_data, "affix_count": 1})
            results = judge_equipment_potential(target, configs, rule_keys)
            best: str | None = None
            for r in results.values():
                if r.get("skipped") or r.get("not_applicable"):
                    continue
                k = label_to_key.get(r.get("rating", ""))
                if k and (best is None or RATING_RANK[k] > RATING_RANK[best]):
                    best = k
            return best or "junk"

        # 初始评级
        label_to_key = {v: k for k, v in RATING_LABELS.items()}
        pot = judge_equipment_potential(equip, configs, rule_keys)
        expect_key = _best_rating_key(pot, label_to_key)
        expect_label = RATING_LABELS.get(expect_key, expect_key or "?")
        entry = group.scan.entry_min_rating
        entry_label = RATING_LABELS.get(entry, entry)
        log.append(f"初始评级: {expect_label}（门槛≥{entry_label}）")

        # 门槛检查
        passes = (expect_key is not None
                  and RATING_RANK.get(expect_key, -1)
                  >= RATING_RANK.get(entry, 0))

        if not passes:
            log.append(tr("未达门槛 → 扫描处理"))
            if group.scan.enabled:
                action, why = group.scan.decide(
                    equip_part, equip_quality, float(cap_pct)
                    if cap_pct is not None else None,
                    rating_of, [a.name for a in equip.affixes])
                if action == "tune_full_recycle":
                    log.append(f"  {why} → 调满后回收")
                    self.result_text.setPlainText("\n".join(log))
                    return
                if action == "tune_this":
                    log.append(f"  {why} → 强制调律")
                else:
                    log.append(f"  {why}")
                    self.result_text.setPlainText("\n".join(log))
                    return
            else:
                log.append(tr("  扫描处理未启用 → 跳过"))
                self.result_text.setPlainText("\n".join(log))
                return

        # 值得调律 → 模拟调律循环
        log.append(tr("── 调律开始 ──"))
        affix_count = len(equip.affixes)
        full_recycle = False

        for i, new_affix in enumerate(tune_affixes):
            rnd = i + 1
            # 狗粮决策（只取决于首词条 pct + 当前期望评级 + 品阶）
            food = group.materials.decide_food(
                cap_pct, expect_key, equip_quality, _DUMMY_STOCKS)
            food_tag = (food.food if food.action == "feed"
                        else tr("无") if food.action == "none"
                        else food.action)
            # 添加词条
            equip.affixes.append(new_affix)
            affix_count += 1
            full = affix_count >= _AFFIX_ROWS

            # 重新评级
            pot = judge_equipment_potential(equip, configs, rule_keys)
            expect_key = _best_rating_key(pot, label_to_key)
            expect_label = RATING_LABELS.get(expect_key, expect_key or "?")
            # 本轮日志必须显示新增词条后的评级。旧实现写在 append 之前，
            # 导致 Rn 实际展示 R(n-1) 的潜力，满词条 R4 尤其会误报顶级。
            log.append(
                f"R{rnd} +{new_affix.name} 狗粮:{food_tag}"
                f" 评:{expect_label}")

            # 结束处理
            if group.tune.enabled:
                action, why = group.tune.decide(
                    equip_part, equip_quality,
                    float(cap_pct) if cap_pct is not None else None,
                    rating_of, full, [a.name for a in equip.affixes])
            else:
                action = "skip" if full else "continue"
                why = tr("结束处理未启用")

            if action == "continue":
                if full:
                    log.append(f"  {why} → 词条满，保留")
                    break
            elif action == "reset":
                log.append(f"  {why} → 重置")
                break
            elif action == "recycle":
                log.append(f"  {why} → 回收")
                break
            elif action == "skip":
                log.append(f"  {why} → 跳过")
                break
            elif action == "tune_full_recycle":
                log.append(f"  {why} → 调满后回收")
                full_recycle = True
                break
            else:
                log.append(f"  {why} → 未知动作 {action!r}")

        # log[-1] 由上面几处 f"...→ 保留/重置/回收/跳过" 拼出来，那些都是
        # 直接写死的中文（非 tr()），这里比对也要用裸中文，否则英文界面下
        # 永远匹配不上，会多打印一行多余的「调律结束」分隔线。
        if not full_recycle and affix_count < _AFFIX_ROWS and not log[-1].endswith(
                ("保留", "重置", "回收", "跳过")):
            log.append(f"── 调律结束（{affix_count}/{_AFFIX_ROWS}）──")

        self.result_text.setPlainText("\n".join(log))
