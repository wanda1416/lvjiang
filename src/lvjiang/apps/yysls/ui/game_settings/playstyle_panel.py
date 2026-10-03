"""玩法配置面板。

玩法决定**调律方向**——要什么增伤、定什么音；流派只决定毕业率计算。
混搭因此是「有玩法、无流派」：能调律，算不了毕业率，这是自然降级而不是异常。

玩法以前内嵌在每个调律规则里，同一个「纯唐」在多个规则文件中各写一遍
（实测 14 个玩法、跨文件零差异的重复），玩法因此没有唯一归属——用户是纯唐
还是双切只能从「他勾了哪条规则的哪个玩法」反推。提到这里之后规则只引用。
"""
from __future__ import annotations

from loguru import logger
from PyQt6.QtCore import QSize, Qt
from PyQt6.QtWidgets import (
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLayout,
    QListWidget,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from lvjiang.ui.button_styles import apply_button_style
from lvjiang.ui.layout_helpers import configure_navigation_list, fit_combo_to_contents
from lvjiang.ui.tag_input import TagInputWidget

from .....i18n import tr
from ...config import get_game_config

_ATTRS_REL = "yysls/game_config"
_GENERIC = "通用"
_CUSTOM_SCHOOL = ""
_ALL_SKILL_REQUIREMENTS = ("需要", "不需要")
_QISHU_REQUIREMENTS = ("不需要", "群体", "单体")
_UNIT_REQUIREMENTS = ("不需要", "首领", "玩家")


class _DefinitionFields(QWidget):
    """玩法基础字段：宽屏双列，窄屏按字段顺序退回单列。"""

    def __init__(self, fields: list[tuple[str, QComboBox]]) -> None:
        super().__init__()
        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setHorizontalSpacing(16)
        self._grid.setVerticalSpacing(6)
        # 双列布局的最小宽度不能锁住整个页面，否则无法缩窄到单列。
        self._grid.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._columns = 0
        self._fields: list[QWidget] = []
        self._combos = [combo for _, combo in fields]
        labels = [QLabel(text) for text, _ in fields]
        label_width = max(label.sizeHint().width() for label in labels)
        for label, (_, combo) in zip(labels, fields, strict=True):
            field = QWidget(self)
            row = QHBoxLayout(field)
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(8)
            label.setFixedWidth(label_width)
            label.setBuddy(combo)
            row.addWidget(label)
            combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            row.addWidget(combo, 1)
            self._fields.append(field)
        self.refresh_widths()

    def refresh_widths(self) -> None:
        for combo in self._combos:
            fit_combo_to_contents(combo, minimum=120)
        self._reflow(force=True)

    def minimumSizeHint(self):
        return QSize(
            max(field.minimumSizeHint().width() for field in self._fields),
            self._grid.sizeHint().height(),
        )

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._reflow()

    def _reflow(self, *, force: bool = False) -> None:
        field_width = max(field.minimumSizeHint().width() for field in self._fields)
        columns = 2 if self.width() >= field_width * 2 + 16 else 1
        if not force and columns == self._columns:
            return
        self._columns = columns
        while self._grid.count():
            self._grid.takeAt(0)
        for index, field in enumerate(self._fields):
            self._grid.addWidget(field, index // columns, index % columns)
        self._grid.setColumnStretch(0, 1)
        self._grid.setColumnStretch(1, int(columns == 2))
        self.updateGeometry()


class PlaystylePanel(QWidget):
    """左侧玩法名，右侧属性 / 两个武学 / 增伤要求 / 攻具与防具定音。"""

    def __init__(self, parent=None, *, data: dict | None = None, on_changed=None):
        super().__init__(parent)
        self._loading = False
        self._data: dict = data if data is not None else {}
        self._external_data = data is not None
        self._on_changed = on_changed
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

        # 基础定义集中在顶部；后续出装搭配可直接接在同页下方。
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(8)
        self._definition_toggle = QToolButton()
        self._definition_toggle.setText(tr("基础定义"))
        self._definition_toggle.setCheckable(True)
        self._definition_toggle.setChecked(True)
        self._definition_toggle.setAutoRaise(True)
        self._definition_toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self._definition_toggle.setArrowType(Qt.ArrowType.DownArrow)
        self._summary = QLabel()
        self._summary.setWordWrap(True)
        self._summary.setVisible(False)
        header = QHBoxLayout()
        header.addWidget(self._definition_toggle)
        header.addWidget(self._summary, 1)
        header.addStretch()
        right_layout.addLayout(header)
        self._definition_content = QWidget()
        definition_layout = QVBoxLayout(self._definition_content)
        definition_layout.setContentsMargins(8, 0, 8, 8)
        definition_layout.setSpacing(6)
        right_layout.addWidget(self._definition_content)
        self._definition_toggle.toggled.connect(self._toggle_definition)
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
        self._definition_fields = _DefinitionFields([
            (tr("流派"), self._combo_school),
            (tr("属性"), self._combo_attr),
            (tr("主武学"), self._combo_art_a),
            (tr("主武学增伤"), self._combo_damage_a),
            (tr("副武学"), self._combo_art_b),
            (tr("副武学增伤"), self._combo_damage_b),
            (tr("攻具定音"), self._combo_output),
            (tr("防具定音"), self._combo_defense),
            (tr("全武学增伤"), self._combo_all_skill),
            (tr("奇术增伤"), self._combo_qishu),
            (tr("对单位增伤"), self._combo_unit),
        ])
        definition_layout.addWidget(self._definition_fields)
        metadata_hint = QLabel(tr("全武学、奇术、对单位增伤仅作玩法说明 ⓘ"))
        metadata_hint.setWordWrap(True)
        metadata_hint.setToolTip(tr(
            "以上三项仅作玩法说明，不参与评级、自动调律或毕业率计算"))
        metadata_hint.setStyleSheet("color: palette(mid); font-size: 11px;")
        definition_layout.addWidget(metadata_hint)
        # 关键字是**参与匹配**的，必须排在上面那句「仅作玩法说明」之后，
        # 否则会被它一并否定掉。
        self._keywords = TagInputWidget([])
        keyword_label = QLabel(tr("匹配关键字"))
        keyword_row = QHBoxLayout()
        keyword_row.setSpacing(8)
        keyword_row.addWidget(keyword_label)
        keyword_row.addWidget(self._keywords, 1)
        definition_layout.addLayout(keyword_row)
        keyword_hint = tr(
            "扫描全部备战方案时，方案名直接含玩法名优先；否则命中关键字的玩法"
            "胜出，多个命中取最长关键字。按 Enter 添加")
        keyword_label.setToolTip(keyword_hint)
        self._keywords.setToolTip(keyword_hint)
        self._hint = QLabel()
        self._hint.setWordWrap(True)
        self._hint.setStyleSheet("color: palette(mid); font-size: 11px;")
        definition_layout.addWidget(self._hint)
        from ..loadout.build_calculator import BuildListPanel
        self._builds_panel = BuildListPanel(self)
        right_layout.addWidget(self._builds_panel)
        right_layout.addStretch()
        scroll.setWidget(right_widget)
        splitter.addWidget(scroll)

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
        self._keywords.tags_changed.connect(lambda: self._on_field_changed(""))

    def _toggle_definition(self, expanded: bool) -> None:
        self._definition_content.setVisible(expanded)
        self._summary.setVisible(not expanded)
        self._definition_toggle.setArrowType(
            Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow)

    @staticmethod
    def _set_affix_options(
        combo: QComboBox, names: list[str], empty_label: str, saved: str = "",
    ) -> None:
        """显示明确的无要求选项；持久化仍用空串，不写入展示文案。"""
        combo.clear()
        combo.addItem(empty_label, "")
        for name in names:
            combo.addItem(name, name)
        combo.setCurrentIndex(max(0, combo.findData(saved)))

    def _editors(self) -> tuple[QComboBox, ...]:
        return (self._combo_school, self._combo_art_a, self._combo_art_b,
                self._combo_attr,
                self._combo_damage_a, self._combo_damage_b,
                self._combo_output, self._combo_defense,
                self._combo_all_skill, self._combo_qishu, self._combo_unit)

    # ── 数据 ──

    def _load_data(self) -> None:
        if not self._external_data:
            try:
                from ...config.game_config_files import load_game_config
                self._data = load_game_config()
            except Exception as exc:  # noqa: BLE001
                logger.error(f"加载配置失败: {exc}")
                self._data = {}
        self._reload()

    def _entries(self) -> list[dict]:
        return [e for e in (self._data.get("playstyles") or [])
                if isinstance(e, dict) and e.get("name")]

    def _save_data(self) -> None:
        if self._on_changed is not None:
            self._on_changed()
            return
        try:
            from ...config.game_config_files import save_game_config
            save_game_config(self._data)
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
        self._set_affix_options(
            self._combo_output,
            sorted(gc.get_affix_names_in_category("外功增益")
                   + gc.get_affix_names_in_category("属攻增益")),
            tr("无特定定音"))
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
        self._builds_panel.set_playstyle(name)
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
        self._keywords.set_tags(list(cfg.get("match_keywords") or []))
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
        """按武学收敛增伤，按绑定流派收敛防具定音。

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
            self._set_affix_options(
                combo, [affix] if affix else [], tr("不需要增伤"), str(saved or ""))

        school = str(self._combo_school.currentData() or "")
        skills = sorted(
            gc.get_affix_names_in_group("指定技能增效", school)
            if school else
            gc.get_affix_names_in_category("指定技能增效"))
        saved_defense = str(cfg.get("defense_dingyin") or "")
        if not school and saved_defense and saved_defense not in skills:
            skills.append(saved_defense)
        self._set_affix_options(
            self._combo_defense, skills, tr("无特定定音"), saved_defense)
        self._combo_output.setCurrentIndex(max(0, self._combo_output.findData(
            str(cfg.get("output_dingyin") or ""))))
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
                "自定义玩法可自由选择属性与武学，并显示全部防具定音"))
        self._summary.setText(" · ".join(filter(None, (
            self._combo_school.currentText(), " / ".join(filter(None, picked)),
        ))))
        self._definition_fields.refresh_widths()
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
                    "main_damage": self._combo_damage_a.currentData() or "",
                    "sub_damage": self._combo_damage_b.currentData() or "",
                    "output_dingyin": self._combo_output.currentData() or "",
                    "defense_dingyin": self._combo_defense.currentData() or "",
                    "all_skill_requirement":
                        self._combo_all_skill.currentText(),
                    "qishu_requirement": self._combo_qishu.currentText(),
                    "unit_requirement": self._combo_unit.currentText(),
                    "match_keywords": self._keywords.tags(),
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
        builds = self._builds_panel.repository.all(name)
        if builds:
            QMessageBox.warning(
                self, tr("无法删除"),
                tr("请先删除该玩法下的出装搭配：{builds}").format(
                    builds="、".join(build.name for build in builds)))
            return
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
