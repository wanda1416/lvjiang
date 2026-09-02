"""玩法配置面板。

玩法决定**调律方向**——要什么增伤、定什么音；流派只决定毕业率计算。
混搭因此是「有玩法、无流派」：能调律，算不了毕业率，这是自然降级而不是异常。

玩法以前内嵌在每个调律规则里，同一个「纯唐」在多个规则文件中各写一遍
（实测 14 个玩法、跨文件零差异的重复），玩法因此没有唯一归属——用户是纯唐
还是双切只能从「他勾了哪条规则的哪个玩法」反推。提到这里之后规则只引用。
"""
from __future__ import annotations

from loguru import logger
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QMessageBox,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from lvjiang.ui.button_styles import apply_button_style

from .....i18n import tr
from ...config import get_game_config
from ..layout_helpers import config_field_card, configure_navigation_list

_ATTRS_REL = "yysls/game_config.yaml"
_GENERIC = "通用"
_CUSTOM_SCHOOL = ""
_ALL_SKILL_REQUIREMENTS = ("需要", "不需要")
_QISHU_REQUIREMENTS = ("不需要", "群体", "单体")
_UNIT_REQUIREMENTS = ("不需要", "首领", "玩家")


class PlaystylePanel(QWidget):
    """左侧玩法名，右侧属性 / 两个武学 / 增伤要求 / 输出与防御定音。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._loading = False
        self._data: dict = {}
        self._build_ui()
        self._load_data()

    # ── 构建 ──

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        layout.addWidget(splitter)

        # 左侧：玩法列表（与词组配置/装备配置同款导航栏）
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.addWidget(QLabel(tr("玩法类型")))
        self._list = QListWidget()
        configure_navigation_list(self._list, minimum_width=200)
        self._list.currentTextChanged.connect(self._on_selected)
        left_layout.addWidget(self._list)

        row = QHBoxLayout()
        self._btn_add = QPushButton(tr("+ 玩法"))
        self._btn_add.clicked.connect(self._on_add)
        row.addWidget(self._btn_add)
        self._btn_del = QPushButton(tr("- 玩法"))
        self._btn_del.clicked.connect(self._on_delete)
        row.addWidget(self._btn_del)
        apply_button_style(self._btn_add)
        apply_button_style(self._btn_del, variant="danger")
        left_layout.addLayout(row)
        splitter.addWidget(left_widget)

        # 右侧：每项配置单独成区。玩法字段较多，普通 QFormLayout 会全部
        # 缩在左上角，也很难快速区分两个武学及各自的增伤要求。
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(8)
        self._combo_school = QComboBox()
        self._combo_art_a = QComboBox()
        self._combo_art_b = QComboBox()
        self._combo_attr = QComboBox()
        self._combo_damage_a = QComboBox()
        self._combo_damage_b = QComboBox()
        self._combo_output = QComboBox()
        self._combo_defense = QComboBox()
        self._combo_all_skill = QComboBox()
        self._combo_qishu = QComboBox()
        self._combo_unit = QComboBox()
        self._combo_all_skill.addItems(list(_ALL_SKILL_REQUIREMENTS))
        self._combo_qishu.addItems(list(_QISHU_REQUIREMENTS))
        self._combo_unit.addItems(list(_UNIT_REQUIREMENTS))
        # 沿用「主/副」这两个用户熟悉的叫法，但**语义上不绑定顺序**：纯唐和
        # 双切的武学对完全相同，区别只在增伤要求落在哪一边，所以按武学查玩法
        # 是无序匹配（get_playstyles_for_arts），两个都会列出来由用户挑。
        for label, editor in (
            (tr("流派"), self._combo_school),
            (tr("属性"), self._combo_attr),
            (tr("主武学"), self._combo_art_a),
            (tr("主武学增伤要求"), self._combo_damage_a),
            (tr("副武学"), self._combo_art_b),
            (tr("副武学增伤要求"), self._combo_damage_b),
            (tr("输出装备定音"), self._combo_output),
            (tr("防御装备定音"), self._combo_defense),
            (tr("全武学增伤要求"), self._combo_all_skill),
            (tr("奇术增伤要求"), self._combo_qishu),
            (tr("对单位增伤要求"), self._combo_unit),
        ):
            right_layout.addWidget(config_field_card(label, editor))
        metadata_hint = QLabel(tr(
            "以上三项仅作玩法说明，不参与评级、自动调律或毕业率计算"))
        metadata_hint.setWordWrap(True)
        metadata_hint.setContentsMargins(14, 2, 14, 0)
        metadata_hint.setStyleSheet("color: palette(mid); font-size: 11px;")
        right_layout.addWidget(metadata_hint)
        self._hint = QLabel()
        self._hint.setWordWrap(True)
        self._hint.setContentsMargins(14, 2, 14, 0)
        self._hint.setStyleSheet("color: palette(mid); font-size: 11px;")
        right_layout.addWidget(self._hint)
        right_layout.addStretch()
        splitter.addWidget(right_widget)

        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([220, 650])

        self._combo_school.currentIndexChanged.connect(
            self._on_school_changed)
        for combo in (self._combo_attr, self._combo_damage_a,
                      self._combo_damage_b, self._combo_output,
                      self._combo_defense, self._combo_all_skill,
                      self._combo_qishu, self._combo_unit):
            combo.currentTextChanged.connect(self._on_field_changed)
        for combo in (self._combo_art_a, self._combo_art_b):
            combo.currentTextChanged.connect(self._on_arts_changed)

    def _editors(self) -> tuple[QComboBox, ...]:
        return (self._combo_school, self._combo_art_a, self._combo_art_b,
                self._combo_attr,
                self._combo_damage_a, self._combo_damage_b,
                self._combo_output, self._combo_defense,
                self._combo_all_skill, self._combo_qishu, self._combo_unit)

    # ── 数据 ──

    def _load_data(self) -> None:
        from lvjiang.core.config.resolver import get_resolver
        try:
            self._data = get_resolver().load_merged(_ATTRS_REL)
        except Exception as exc:  # noqa: BLE001
            logger.error(f"加载配置失败: {exc}")
            self._data = {}
        self._reload()

    def _entries(self) -> list[dict]:
        return [e for e in (self._data.get("playstyles") or [])
                if isinstance(e, dict) and e.get("name")]

    def _save_data(self) -> None:
        from lvjiang.core.config.resolver import get_resolver
        try:
            get_resolver().save_merged(_ATTRS_REL, self._data)
            get_game_config().reload()
        except Exception as exc:  # noqa: BLE001
            logger.error(f"保存配置失败: {exc}")

    def _arts(self) -> list[str]:
        raw = [e for e in (self._data.get("martial_arts") or [])
               if isinstance(e, dict) and e.get("name")]
        raw.sort(key=lambda e: (str(e.get("attr") or ""),
                                str(e.get("weapon") or ""), str(e["name"])))
        return [str(e["name"]) for e in raw]

    def _art_of(self, name: str) -> dict:
        return next((e for e in (self._data.get("martial_arts") or [])
                     if isinstance(e, dict) and e.get("name") == name), {})

    def _reload(self) -> None:
        gc = get_game_config()
        self._loading = True
        arts = [""] + self._arts()
        for combo in (self._combo_art_a, self._combo_art_b):
            combo.clear()
            combo.addItems(arts)
        self._combo_school.clear()
        self._combo_school.addItem(tr("自定义"), _CUSTOM_SCHOOL)
        for school in gc.get_schools():
            self._combo_school.addItem(school, school)
        self._combo_attr.clear()
        self._combo_attr.addItems([_GENERIC, "鸣金", "裂石", "破竹", "牵丝"])
        self._combo_output.clear()
        self._combo_output.addItems(
            [""] + sorted(gc.get_affix_names_in_category("外功增益")
                          + gc.get_affix_names_in_category("属攻增益")))
        current_item = self._list.currentItem()
        keep = current_item.text() if current_item else ""
        self._list.clear()
        self._list.addItems([e["name"] for e in self._entries()])
        self._loading = False
        if keep:
            found = self._list.findItems(keep, Qt.MatchFlag.MatchExactly)
            if found:
                self._list.setCurrentItem(found[0])
                return
        if self._list.count():
            self._list.setCurrentRow(0)

    def _on_selected(self, name: str) -> None:
        cfg = next((e for e in self._entries() if e["name"] == name), {})
        school = str(cfg.get("school") or "")
        school_cfg = (self._data.get("schools") or {}).get(school) or {}
        if school:
            arts = [
                str((school_cfg.get("main") or {}).get("martial_art") or ""),
                str((school_cfg.get("sub") or {}).get("martial_art") or ""),
            ]
            attr = str(school_cfg.get("attr") or _GENERIC)
        else:
            arts = list(cfg.get("arts") or [])
            attr = str(cfg.get("attr") or _GENERIC)
        self._loading = True
        school_index = self._combo_school.findData(school)
        self._combo_school.setCurrentIndex(max(school_index, 0))
        self._combo_art_a.setCurrentText(arts[0] if arts else "")
        self._combo_art_b.setCurrentText(arts[1] if len(arts) > 1 else "")
        self._combo_attr.setCurrentText(attr)
        self._loading = False
        self._sync_derived(cfg)

    def _school_config(self, school: str | None = None) -> dict:
        name = (str(school) if school is not None
                else str(self._combo_school.currentData() or ""))
        return (self._data.get("schools") or {}).get(name) or {}

    def _on_school_changed(self, _index: int) -> None:
        """绑定流派时用流派的权威属性/武学回填并锁定。"""
        if self._loading:
            return
        school = str(self._combo_school.currentData() or "")
        cfg = self._school_config(school)
        loading, self._loading = self._loading, True
        if school:
            self._combo_attr.setCurrentText(str(cfg.get("attr") or _GENERIC))
            self._combo_art_a.setCurrentText(str(
                (cfg.get("main") or {}).get("martial_art") or ""))
            self._combo_art_b.setCurrentText(str(
                (cfg.get("sub") or {}).get("martial_art") or ""))
        self._loading = loading
        self._sync_derived()
        self._on_field_changed("")

    def _on_arts_changed(self, _text: str) -> None:
        if self._loading:
            return
        self._sync_derived()
        self._on_field_changed("")

    def _sync_derived(self, cfg: dict | None = None) -> None:
        """按武学收敛增伤，按绑定流派收敛防御定音。

        增伤要求跟武器走（横刀武学增伤）。指定技能增效在游戏配置中以流派名
        分组；绑定流派时直接取该组，自定义玩法则展示全部。不能按武学名前缀
        猜测，因为醉拳存在「悬身断水·浓醺」等不以武学名开头的合法词条。
        """
        if cfg is None:
            item = self._list.currentItem()
            cfg = next((e for e in self._entries()
                        if item and e["name"] == item.text()), {})
        gc = get_game_config()
        loading, self._loading = self._loading, True
        picked = [self._combo_art_a.currentText().strip(),
                  self._combo_art_b.currentText().strip()]
        for combo, art, saved in (
            (self._combo_damage_a, picked[0], cfg.get("main_damage", "")),
            (self._combo_damage_b, picked[1], cfg.get("sub_damage", "")),
        ):
            weapon = self._art_of(art).get("weapon", "")
            affix = gc.get_weapon_wuxue_affix(weapon) if weapon else ""
            combo.clear()
            combo.addItems([""] + ([affix] if affix else []))
            combo.setCurrentText(saved)

        school = str(self._combo_school.currentData() or "")
        skills = sorted(
            gc.get_affix_names_in_group("指定技能增效", school)
            if school else
            gc.get_affix_names_in_category("指定技能增效"))
        self._combo_defense.clear()
        self._combo_defense.addItems([""] + skills)
        saved_defense = str(cfg.get("defense_dingyin") or "")
        if not school and saved_defense and saved_defense not in skills:
            self._combo_defense.addItem(saved_defense)
        self._combo_defense.setCurrentText(saved_defense)
        self._combo_output.setCurrentText(cfg.get("output_dingyin", ""))
        self._combo_all_skill.setCurrentText(str(
            cfg.get("all_skill_requirement") or "需要"))
        self._combo_qishu.setCurrentText(str(
            cfg.get("qishu_requirement") or "不需要"))
        self._combo_unit.setCurrentText(str(
            cfg.get("unit_requirement") or "不需要"))

        bound = bool(school)
        self._combo_art_a.setEnabled(not bound)
        self._combo_art_b.setEnabled(not bound)
        self._combo_attr.setEnabled(not bound)
        if bound:
            self._hint.setText(tr("属性与主副武学由绑定流派提供，已锁定"))
        else:
            self._hint.setText(tr(
                "自定义玩法可自由选择属性与武学，并显示全部防御定音"))
        self._loading = loading

    def _on_field_changed(self, _text: str) -> None:
        if self._loading:
            return
        item = self._list.currentItem()
        if item is None:
            return
        arts = [c.currentText().strip()
                for c in (self._combo_art_a, self._combo_art_b)]
        school = str(self._combo_school.currentData() or "")
        entries = self._entries()
        for e in entries:
            if e["name"] == item.text():
                e.update({
                    "school": school,
                    "arts": [a for a in arts if a],
                    "attr": self._combo_attr.currentText(),
                    "main_weapon": self._art_of(arts[0]).get("weapon", ""),
                    "sub_weapon": self._art_of(arts[1]).get("weapon", ""),
                    "main_damage": self._combo_damage_a.currentText(),
                    "sub_damage": self._combo_damage_b.currentText(),
                    "output_dingyin": self._combo_output.currentText(),
                    "defense_dingyin": self._combo_defense.currentText(),
                    "all_skill_requirement":
                        self._combo_all_skill.currentText(),
                    "qishu_requirement": self._combo_qishu.currentText(),
                    "unit_requirement": self._combo_unit.currentText(),
                })
                break
        self._data["playstyles"] = entries
        self._save_data()

    def _on_add(self) -> None:
        name, ok = QInputDialog.getText(self, tr("新增玩法"), tr("玩法名称:"))
        name = (name or "").strip()
        if not ok or not name:
            return
        if any(e["name"] == name for e in self._entries()):
            QMessageBox.warning(self, tr("新增玩法"), tr("该玩法已存在"))
            return
        self._data["playstyles"] = self._entries() + [
            {"name": name, "school": _CUSTOM_SCHOOL,
             "attr": _GENERIC, "arts": [],
             "all_skill_requirement": "需要",
             "qishu_requirement": "不需要",
             "unit_requirement": "不需要"}]
        self._save_data()
        self._reload()

    def _on_delete(self) -> None:
        item = self._list.currentItem()
        if item is None:
            return
        name = item.text()
        used = self._rules_referencing(name)
        if used:
            # 删了会让规则的引用悬空——那正是这次拆分要消灭的东西
            QMessageBox.warning(
                self, tr("无法删除"),
                tr("以下调律规则仍在引用该玩法：{rules}").format(
                    rules="、".join(used)))
            return
        self._data["playstyles"] = [
            e for e in self._entries() if e["name"] != name]
        self._save_data()
        self._reload()

    @staticmethod
    def _rules_referencing(name: str) -> list[str]:
        from ...core.evaluator.registry import get_tuning_rules

        return sorted(rule.name for rule in get_tuning_rules().values()
                      if name in rule.playstyles)
