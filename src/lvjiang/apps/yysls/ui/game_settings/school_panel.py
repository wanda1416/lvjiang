"""流派配置面板

左侧为流派列表（对应游戏十大流派，可增删、直接编辑重命名），
右侧为选中流派的配置表单：
- 属性 / 主武器+主武学 / 副武器+副武学
- 基础属性管理（查看/编辑/删除当前流派的基础属性）
- 方案管理（导入 Excel 并注册毕业率计算方案）

数据存于 game_config.yaml 顶层 schools：
    流派名 → {
        attr: 属性,
        main: {weapon, martial_art},
        sub: {weapon, martial_art},
    }
修改即时写盘，并刷新 GameConfigManager 单例。

基础属性数据存于 config/session/yysls.json，兼容沿用 play_styles 存储键。
"""

from loguru import logger
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QDoubleValidator
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from .....i18n import tr

# 配置文件（聚合键值，经 resolver 读合并视图、按模式写回）
_ATTRS_REL = "yysls/game_config.yaml"

# 流派属性候选
_SCHOOL_ATTRS = [tr("鸣金"), tr("裂石"), tr("破竹"), tr("牵丝")]


class SchoolPanel(QWidget):
    """流派配置面板（左：流派列表；右：配置表单）"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._data: dict = {}  # 完整配置数据
        self._names: list[str] = []  # 列表行 → 流派名（重命名时对照旧名）
        self._loading = False  # 防止刷新控件时触发保存
        self._init_ui()
        self._load_data()

    def _init_ui(self):
        layout = QHBoxLayout(self)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        layout.addWidget(splitter)

        # ── 左侧：流派列表 ──
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.addWidget(QLabel(tr("流派类型")))

        self._school_list = QListWidget()
        self._school_list.currentRowChanged.connect(self._on_school_changed)
        self._school_list.itemChanged.connect(self._on_item_renamed)
        left_layout.addWidget(self._school_list)

        btn_layout = QHBoxLayout()
        self._btn_add = QPushButton(tr("添加"))
        self._btn_add.clicked.connect(self._on_add_school)
        btn_layout.addWidget(self._btn_add)
        self._btn_del = QPushButton(tr("删除"))
        self._btn_del.clicked.connect(self._on_del_school)
        btn_layout.addWidget(self._btn_del)
        left_layout.addLayout(btn_layout)

        splitter.addWidget(left_widget)

        # ── 右侧：流派配置表单 + 基础属性/方案管理 ──
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(0, 0, 0, 0)

        hint = QLabel(tr("武器候选来自装备配置的武器类型。武学增效已移至装备配置的武器类型中。"))
        hint.setStyleSheet("color: #888;")
        right_layout.addWidget(hint)

        # ── 武学属性 ──
        attr_group = QGroupBox(tr("武学属性"))
        attr_layout = QVBoxLayout(attr_group)

        # 第一行：属性
        row_attr = QHBoxLayout()
        row_attr.setSpacing(28)
        row_attr.addWidget(QLabel(tr("属性")))
        self._combo_attr = QComboBox()
        self._combo_attr.setFixedWidth(100)
        row_attr.addWidget(self._combo_attr)
        row_attr.addStretch()
        attr_layout.addLayout(row_attr)

        # 第二/三行：主武器+主武学 / 副武器+副武学
        self._combo_main_weapon = QComboBox()
        self._combo_main_weapon.setFixedWidth(100)
        self._edit_main_martial = QLineEdit()
        self._edit_main_martial.setPlaceholderText(tr("武学名称"))
        self._edit_main_martial.setMaxLength(6)
        self._edit_main_martial.setFixedWidth(100)
        self._combo_sub_weapon = QComboBox()
        self._combo_sub_weapon.setFixedWidth(100)
        self._edit_sub_martial = QLineEdit()
        self._edit_sub_martial.setPlaceholderText(tr("武学名称"))
        self._edit_sub_martial.setMaxLength(6)
        self._edit_sub_martial.setFixedWidth(100)

        def _pair(label: str, widget) -> QHBoxLayout:
            lay = QHBoxLayout()
            lay.setSpacing(14)
            lay.addWidget(QLabel(label))
            lay.addWidget(widget)
            lay.addStretch()
            return lay

        grid = QGridLayout()
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        grid.addLayout(_pair(tr("主武器"), self._combo_main_weapon), 0, 0)
        grid.addLayout(_pair(tr("主武学"), self._edit_main_martial), 0, 1)
        grid.addLayout(_pair(tr("副武器"), self._combo_sub_weapon), 1, 0)
        grid.addLayout(_pair(tr("副武学"), self._edit_sub_martial), 1, 1)
        attr_layout.addLayout(grid)

        for combo in self._combos():
            combo.currentTextChanged.connect(self._on_field_changed)
        for edit in self._edits():
            edit.textChanged.connect(self._on_field_changed)

        right_layout.addWidget(attr_group)

        # ── 方案管理 / 基础属性：同一行、等高双栏 ──
        management_layout = QHBoxLayout()
        management_layout.setSpacing(10)

        scheme_group = QGroupBox(tr("方案管理"))
        self._scheme_group = scheme_group
        scheme_group.setMinimumHeight(150)
        scheme_layout = QVBoxLayout(scheme_group)
        self._btn_import_scheme = QPushButton(tr("导入 Excel…"))
        self._btn_import_scheme.clicked.connect(self._on_import_scheme)
        scheme_layout.addWidget(self._btn_import_scheme)
        self._scheme_list = QListWidget()
        self._scheme_list.currentRowChanged.connect(self._on_scheme_selected)
        scheme_layout.addWidget(self._scheme_list, stretch=1)
        management_layout.addWidget(scheme_group, stretch=1)

        ps_group = QGroupBox(tr("基础属性"))
        self._base_attrs_group = ps_group
        ps_group.setMinimumHeight(150)
        ps_layout = QVBoxLayout(ps_group)
        self._ps_list = QListWidget()
        self._ps_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._ps_list.customContextMenuRequested.connect(self._on_ps_context_menu)
        self._ps_list.currentRowChanged.connect(self._on_ps_selected)
        ps_layout.addWidget(self._ps_list)
        management_layout.addWidget(ps_group, stretch=1)
        right_layout.addLayout(management_layout, stretch=1)

        # ── 数值展示：方案为只读满值，基础属性保留可编辑能力 ──
        value_group = QGroupBox(tr("数值展示"))
        value_layout = QVBoxLayout(value_group)
        self._value_source_label = QLabel(tr("请选择方案或基础属性"))
        self._value_source_label.setStyleSheet("color: palette(mid);")
        value_layout.addWidget(self._value_source_label)
        self._ps_scroll = QScrollArea()
        self._ps_scroll.setWidgetResizable(True)
        self._ps_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self._ps_scroll_widget = QWidget()
        self._ps_scroll_layout = QVBoxLayout(self._ps_scroll_widget)
        self._ps_scroll_layout.setContentsMargins(4, 4, 4, 4)
        self._ps_scroll_layout.setSpacing(6)
        self._ps_scroll.setWidget(self._ps_scroll_widget)
        self._ps_edits: dict[str, QLineEdit] = {}
        self._ps_current_name: str = ""  # 当前编辑的基础属性名
        value_layout.addWidget(self._ps_scroll, stretch=1)
        right_layout.addWidget(value_group, stretch=2)

        splitter.addWidget(right_widget)
        splitter.setSizes([150, 400])

    def _combos(self) -> list[QComboBox]:
        return [
            self._combo_attr,
            self._combo_main_weapon,
            self._combo_sub_weapon,
        ]

    def _edits(self) -> list[QLineEdit]:
        return [self._edit_main_martial, self._edit_sub_martial]

    def showEvent(self, event):
        """每次显示时重新加载（武器类型可能已在其他面板变更）"""
        super().showEvent(event)
        self._load_data()

    # ── 数据加载 / 候选 ──────────────────────────────────────

    def _load_data(self):
        """从 YAML 加载数据并刷新列表与表单"""
        from lvjiang.core.config.resolver import get_resolver
        try:
            self._data = get_resolver().load_merged(_ATTRS_REL)
        except Exception as e:
            logger.error(f"加载配置失败: {e}")
            self._data = {}
        self._refresh_list()

    def _schools(self) -> dict[str, dict]:
        return self._data.get("schools") or {}

    def _weapon_candidates(self) -> list[str]:
        """武器候选：支持新格式（dict 列表）和旧格式（字符串列表）"""
        raw = self._data.get("weapon_types") or []
        return [
            str(t["name"]) if isinstance(t, dict) else str(t)
            for t in raw
        ]

    # ── 左侧列表 ──────────────────────────────────────────────

    def _refresh_list(self, select: str | None = None):
        """重建流派列表；select 指定选中项（默认保持当前选中）"""
        if select is None:
            current = self._school_list.currentItem()
            select = current.text() if current else None
        self._loading = True
        self._names = list(self._schools().keys())
        self._school_list.clear()
        for name in self._names:
            self._school_list.addItem(name)
            item = self._school_list.item(self._school_list.count() - 1)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)
        self._loading = False
        row = self._names.index(select) if select in self._names else 0
        if self._names:
            self._school_list.setCurrentRow(row)
            self._on_school_changed(row)
        else:
            self._on_school_changed(-1)

    def _current_school(self) -> str | None:
        row = self._school_list.currentRow()
        return self._names[row] if 0 <= row < len(self._names) else None

    def _on_school_changed(self, row: int):
        """切换流派 → 刷新右侧表单"""
        name = self._names[row] if 0 <= row < len(self._names) else None
        cfg = (self._schools().get(name) or {}) if name else {}
        main = cfg.get("main") or {}
        sub = cfg.get("sub") or {}

        prev_loading = self._loading
        self._loading = True
        weapons = self._weapon_candidates()
        self._fill_combo(self._combo_attr, _SCHOOL_ATTRS, cfg.get("attr"))
        self._fill_combo(self._combo_main_weapon, weapons, main.get("weapon"))
        self._edit_main_martial.setText(main.get("martial_art", "") or "")
        self._fill_combo(self._combo_sub_weapon, weapons, sub.get("weapon"))
        self._edit_sub_martial.setText(sub.get("martial_art", "") or "")
        enabled = name is not None
        for combo in self._combos():
            combo.setEnabled(enabled)
        for edit in self._edits():
            edit.setEnabled(enabled)

        self._loading = prev_loading
        self._refresh_schemes()
        self._refresh_play_styles()
        if self._scheme_list.count():
            self._scheme_list.setCurrentRow(0)
        elif self._ps_list.count():
            self._ps_list.setCurrentRow(0)
        else:
            self._clear_ps_editor()
            self._value_source_label.setText(tr("请选择方案或基础属性"))

    @staticmethod
    def _fill_combo(combo: QComboBox, candidates: list[str], value: str | None):
        """重建候选并选中当前值；未配置时留空，失效值也保留展示便于改正"""
        combo.clear()
        combo.addItem("")  # 未配置占位
        combo.addItems(candidates)
        value = value or ""
        if value and value not in candidates:
            combo.addItem(value)
        combo.setCurrentText(value)

    def _on_item_renamed(self, item):
        """列表项编辑 → 流派重命名（保持原顺序）"""
        if self._loading:
            return
        row = self._school_list.row(item)
        old_name = self._names[row] if 0 <= row < len(self._names) else None
        if old_name is None:
            return
        new_name = item.text().strip()
        if new_name == old_name:
            return
        if not new_name or new_name in self._schools():
            QMessageBox.warning(self, tr("无法重命名"), tr("流派名不能为空或与已有流派重名。"))
            self._refresh_list(select=old_name)
            return
        schools = {
            (new_name if name == old_name else name): cfg
            for name, cfg in self._schools().items()
        }
        self._data["schools"] = schools
        self._save_data()
        self._refresh_list(select=new_name)

    def _on_add_school(self):
        name, ok = QInputDialog.getText(self, tr("添加流派"), tr("流派名称："))
        if not ok:
            return
        name = name.strip()
        if not name:
            return
        if name in self._schools():
            QMessageBox.warning(self, tr("无法添加"), tr("流派「{name}」已存在。").format(name=name))
            return
        self._data.setdefault("schools", {})[name] = {}
        self._save_data()
        self._refresh_list(select=name)

    def _on_del_school(self):
        name = self._current_school()
        if name is None:
            return
        ret = QMessageBox.question(self, tr("确认删除"), tr("确定删除流派「{name}」？").format(name=name))
        if ret != QMessageBox.StandardButton.Yes:
            return
        self._data.get("schools", {}).pop(name, None)
        self._save_data()
        self._refresh_list()

    # ── 右侧表单保存 ──────────────────────────────────────────

    def _on_field_changed(self, _text: str):
        """任一控件变化 → 回写当前流派配置（空值省略对应键）"""
        if self._loading:
            return
        name = self._current_school()
        if name is None:
            return
        cfg: dict = self._schools().get(name, {})
        attr = self._combo_attr.currentText()
        if attr:
            cfg["attr"] = attr
        for key, combo_w, edit_m in (
            ("main", self._combo_main_weapon, self._edit_main_martial),
            ("sub", self._combo_sub_weapon, self._edit_sub_martial),
        ):
            group = {}
            if combo_w.currentText():
                group["weapon"] = combo_w.currentText()
            martial = edit_m.text().strip()
            if martial:
                group["martial_art"] = martial
            if group:
                cfg[key] = group
        self._data.setdefault("schools", {})[name] = cfg
        self._save_data()

    # ── 保存 ──────────────────────────────────────────────────

    def _save_data(self):
        """保存数据到 YAML 并刷新 GameConfigManager 单例"""
        from lvjiang.core.config.resolver import get_resolver
        try:
            get_resolver().save_merged(_ATTRS_REL, self._data)
            logger.debug(f"配置已保存: {_ATTRS_REL}")
            from lvjiang.apps.yysls.config import get_game_config
            get_game_config()._load()
        except Exception as e:
            logger.error(f"保存失败: {e}")

    # ── 方案管理 ──────────────────────────────────────────────

    def _refresh_schemes(self):
        self._scheme_list.clear()
        school = self._current_school()
        if not school:
            self._btn_import_scheme.setEnabled(False)
            return
        self._btn_import_scheme.setEnabled(True)
        cfg = self._schools().get(school) or {}
        schemes = cfg.get("schemes") or []
        if isinstance(schemes, list):
            self._scheme_list.addItems([str(name) for name in schemes if str(name)])

    def _on_import_scheme(self):
        school = self._current_school()
        if not school:
            return
        excel_path, _ = QFileDialog.getOpenFileName(
            self, tr("导入毕业率 Excel"), "", tr("Excel 工作簿 (*.xlsx)"),
        )
        if not excel_path:
            return
        name, ok = QInputDialog.getText(
            self, tr("方案名称"), tr("保存为方案："), text=tr("基础方案"),
        )
        name = name.strip()
        if not ok or not name:
            return
        cfg = self._schools().get(school) or {}
        schemes = cfg.get("schemes") or []
        if name in schemes:
            ret = QMessageBox.question(
                self, tr("方案已存在"),
                tr("方案「{name}」已存在，是否覆盖？").format(name=name),
            )
            if ret != QMessageBox.StandardButton.Yes:
                return
        try:
            from ...evaluator.graduation import invalidate_graduation_cache
            from ...evaluator.graduation_converter import import_graduation_scheme
            destination, outputs = import_graduation_scheme(excel_path, school, name)
            invalidate_graduation_cache()
        except Exception as exc:
            logger.exception("导入毕业率方案失败")
            QMessageBox.critical(self, tr("导入失败"), str(exc))
            return
        if name not in schemes:
            schemes = list(schemes) + [name]
            cfg["schemes"] = schemes
            self._data.setdefault("schools", {})[school] = cfg
            self._save_data()
        self._refresh_schemes()
        matches = self._scheme_list.findItems(name, Qt.MatchFlag.MatchExactly)
        if matches:
            self._scheme_list.setCurrentItem(matches[0])
        QMessageBox.information(
            self, tr("导入成功"),
            tr("方案「{name}」已生成。\nDPS：{dps:.2f}\n文件：{path}").format(
                name=name, dps=outputs["dps"], path=str(destination),
            ),
        )

    # ── 基础属性管理 ──────────────────────────────────────────

    def _refresh_play_styles(self):
        """刷新当前流派的基础属性列表"""
        school = self._current_school()
        self._ps_list.clear()
        self._clear_ps_editor()
        if not school:
            return
        from ...config import get_play_styles
        styles = get_play_styles(school)
        for name in sorted(styles.keys()):
            self._ps_list.addItem(name)

    def _clear_ps_editor(self):
        """清空共享数值展示区。"""
        while self._ps_scroll_layout.count():
            item = self._ps_scroll_layout.takeAt(0)
            if item.widget():
                widget = item.widget()
                # deleteLater() 要等事件循环返回才生效；切换列表时新旧面板会短暂
                # 叠在同一个滚动区域。先隐藏并脱离父控件，保证本次刷新立即清空。
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()
        self._ps_edits.clear()
        self._ps_current_name = ""

    def _on_scheme_selected(self, row: int) -> None:
        """选中方案 → 展示 Excel 输入满值（输入契约不包含食物加成）。"""
        school = self._current_school()
        if not school or row < 0:
            return
        self._ps_list.blockSignals(True)
        self._ps_list.setCurrentRow(-1)
        self._ps_list.blockSignals(False)
        self._clear_ps_editor()
        scheme = self._scheme_list.item(row).text()
        self._value_source_label.setText(
            tr("方案：{name}（满值属性，不含食物加成）").format(name=scheme)
        )
        try:
            from ...evaluator.graduation import get_graduation_scheme_combat_attrs

            attrs = get_graduation_scheme_combat_attrs(school, scheme)
        except Exception as exc:
            logger.error(f"读取毕业率方案数值失败: {exc}")
            self._value_source_label.setText(tr("方案数值读取失败：{error}").format(
                error=str(exc),
            ))
            return
        from ...config import get_game_config

        school_attr = get_game_config().get_school_attr(school)
        self._ps_scroll_layout.addWidget(
            self._create_standard_attrs_widget(attrs, school_attr, editable=False)
        )
        self._ps_scroll_layout.addStretch()

    def _on_ps_selected(self, row: int):
        """选中基础属性 → 在右侧显示可编辑卡片"""
        school = self._current_school()
        if not school or row < 0:
            return
        self._scheme_list.blockSignals(True)
        self._scheme_list.setCurrentRow(-1)
        self._scheme_list.blockSignals(False)
        from ...config import get_game_config, get_play_styles
        styles = get_play_styles(school)
        name = self._ps_list.item(row).text()
        attrs = styles.get(name, {})
        self._clear_ps_editor()
        self._ps_current_name = name
        self._value_source_label.setText(tr("基础属性：{name}").format(name=name))

        gc = get_game_config()
        school_attr = gc.get_school_attr(school)
        from ...combat_attrs import CombatAttributes

        # 基础属性也进入标准战斗属性模型，再按战斗属性面板结构展示。
        combat_attrs = CombatAttributes.from_dict(attrs)
        card = self._create_standard_attrs_widget(
            combat_attrs, school_attr, editable=True,
        )
        self._ps_scroll_layout.addWidget(card)
        self._ps_scroll_layout.addStretch()

    def _create_standard_attrs_widget(
        self, attrs, school_attr: str | None, *, editable: bool,
    ) -> QWidget:
        """按战斗属性页的四张标准卡片展示 CombatAttributes。"""
        from ...combat_attrs import SCHOOL_ATTR_FIELD_MAP
        attr_map = SCHOOL_ATTR_FIELD_MAP.get(school_attr or "", {})
        attr_name = school_attr or tr("属攻")
        attr_fields = {
            "min_attr": attr_map.get("min_attr", "min_mingjin"),
            "max_attr": attr_map.get("max_attr", "max_mingjin"),
            "attr_pen": attr_map.get("attr_pen", "mingjin_pen"),
            "attr_bonus": attr_map.get("attr_bonus", "mingjin_bonus"),
        }

        attr_series = {
            "攻击": (
                ("鸣金", "min_mingjin", "max_mingjin"),
                ("裂石", "min_lieshi", "max_lieshi"),
                ("破竹", "min_pozhu", "max_pozhu"),
                ("牵丝", "min_qiansi", "max_qiansi"),
            ),
            "穿透": (
                ("鸣金", "mingjin_pen"), ("裂石", "lieshi_pen"),
                ("破竹", "pozhu_pen"), ("牵丝", "qiansi_pen"),
            ),
            "伤害加成": (
                ("鸣金", "mingjin_bonus"), ("裂石", "lieshi_bonus"),
                ("破竹", "pozhu_bonus"), ("牵丝", "qiansi_bonus"),
            ),
        }

        def tooltip(kind: str) -> str:
            lines = []
            for item in attr_series[kind]:
                name, *fields = item
                values = [getattr(attrs, field, 0.0) for field in fields]
                if kind == "攻击":
                    lines.append(f"{name}攻击：{values[0]:.2f} - {values[1]:.2f}")
                elif kind == "伤害加成":
                    lines.append(f"{name}伤害加成：{values[0] * 100:.2f}%")
                else:
                    lines.append(f"{name}穿透：{values[0]:.2f}")
            return "\n".join(lines)

        def make_cell(
            field_name: str | None, label_text: str, unit: str = "",
            tip: str = "", value_override: float | None = None,
        ) -> QWidget:
            value = value_override if value_override is not None else (
                getattr(attrs, field_name, 0.0) if field_name else 0.0
            )
            cell = QWidget()
            cell_layout = QHBoxLayout(cell)
            cell_layout.setContentsMargins(0, 0, 0, 0)
            cell_layout.setSpacing(8)
            label = QLabel(tr(label_text))
            label.setStyleSheet("font-size: 12px; color: palette(mid);")
            cell_layout.addWidget(label)
            cell_layout.addStretch()
            if editable and field_name:
                value_widget = QLineEdit()
                value_widget.setAlignment(Qt.AlignmentFlag.AlignRight)
                value_widget.setFixedWidth(100)
                value_widget.setValidator(
                    QDoubleValidator(-999999.0, 999999.0, 4, value_widget)
                )
                value_widget.setText(
                    f"{value * 100:.2f}" if unit == "%" else f"{value:.2f}"
                )
                value_widget.textChanged.connect(
                    lambda _text, fn=field_name: self._on_ps_field_changed(fn, unit)
                )
                self._ps_edits[field_name] = value_widget
            else:
                text = f"{value * 100:.2f}%" if unit == "%" else (
                    f"{value:.2f}" if value != int(value) else str(int(value))
                )
                value_widget = QLabel(text)
                value_widget.setStyleSheet("font-size: 13px; font-weight: 600;")
                value_widget.setAlignment(Qt.AlignmentFlag.AlignRight)
            if tip:
                label.setToolTip(tip)
                value_widget.setToolTip(tip)
            cell_layout.addWidget(value_widget)
            return cell

        def make_card(title: str, cells: list[tuple | None]) -> QGroupBox:
            group = QGroupBox(tr(title))
            grid = QGridLayout(group)
            grid.setContentsMargins(12, 8, 12, 10)
            grid.setHorizontalSpacing(20)
            grid.setVerticalSpacing(7)
            for index, cell_args in enumerate(cells):
                widget = make_cell(*cell_args) if cell_args is not None else QWidget()
                grid.addWidget(widget, index // 2, index % 2)
            grid.setColumnStretch(0, 1)
            grid.setColumnStretch(1, 1)
            return group

        pen_tip = tooltip("穿透")
        bonus_tip = tooltip("伤害加成")
        attack_cells = [
            ("min_outer", "最小外功攻击", ""),
            ("max_outer", "最大外功攻击", ""),
            ("min_mingjin", "最小鸣金攻击", ""),
            ("max_mingjin", "最大鸣金攻击", ""),
            ("min_lieshi", "最小裂石攻击", ""),
            ("max_lieshi", "最大裂石攻击", ""),
            ("min_pozhu", "最小破竹攻击", ""),
            ("max_pozhu", "最大破竹攻击", ""),
            ("min_qiansi", "最小牵丝攻击", ""),
            ("max_qiansi", "最大牵丝攻击", ""),
            ("min_wuxiang", "最小无相攻击", ""),
            ("max_wuxiang", "最大无相攻击", ""),
        ]
        judgment_cells = [
            ("precision", "精准率", "%"),
            None,
            ("crit_rate", "会心率", "%"),
            ("direct_crit", "直接会心率", "%"),
            ("intent_rate", "会意率", "%"),
            ("direct_intent", "直接会意率", "%"),
        ]
        weapon_extras = [
            (name, value) for name, value in sorted(attrs.extra_attrs.items())
            if name.endswith(("武学增伤", "武学增效")) and value
        ]
        skill_extras = [
            (name, value) for name, value in sorted(attrs.extra_attrs.items())
            if not name.endswith(("武学增伤", "武学增效")) and value
        ]
        weapon_cells = [
            (None, name, "%", "", value) for name, value in weapon_extras[:2]
        ]
        weapon_cells.extend([None] * (2 - len(weapon_cells)))
        skill_cell = (
            (None, skill_extras[0][0], "%", "", skill_extras[0][1])
            if skill_extras else None
        )
        gain_cells = [
            ("outer_pen", "外功穿透", ""),
            (attr_fields["attr_pen"], f"属攻穿透（{attr_name}）", "", pen_tip),
            *weapon_cells,
            ("all_skill_bonus", "全武学增效", "%"),
            None,
            ("single_qs_bonus", "单体类奇术增伤", "%"),
            ("group_qs_bonus", "群体类奇术增伤", "%"),
            ("boss_bonus", "对首领单位增伤", "%"),
            ("player_bonus", "对玩家单位增效", "%"),
            skill_cell,
            None,
        ]
        damage_cells = [
            ("crit_dmg", "会心伤害加成", "%"),
            ("intent_dmg", "会意伤害加成", "%"),
            ("outer_bonus", "外功伤害加成", "%"),
            (None, "外功伤害减免", "%", "", 0.0),
            (attr_fields["attr_bonus"], f"属攻伤害加成（{attr_name}）", "%", bonus_tip),
            (None, f"属攻伤害减免（{attr_name}）", "%", "", 0.0),
        ]

        root = QWidget()
        columns = QHBoxLayout(root)
        columns.setContentsMargins(0, 0, 0, 0)
        columns.setSpacing(10)
        left = QVBoxLayout()
        right = QVBoxLayout()
        left.addWidget(make_card("攻击属性", attack_cells))
        left.addWidget(make_card("判定属性", judgment_cells))
        right.addWidget(make_card("增益效果", gain_cells))
        right.addWidget(make_card("伤害加成", damage_cells))
        left.addStretch()
        right.addStretch()
        columns.addLayout(left, stretch=1)
        columns.addLayout(right, stretch=1)
        return root

    def _on_ps_field_changed(self, field_name: str, unit: str):
        """任一字段变更 → 自动保存到当前基础属性"""
        school = self._current_school()
        name = self._ps_current_name
        if not school or not name:
            return
        from ...config import get_play_styles, save_play_style
        styles = get_play_styles(school)
        attrs = dict(styles.get(name, {}))
        for fn, edit in self._ps_edits.items():
            try:
                v = float(edit.text())
            except (ValueError, TypeError):
                v = 0.0
            # 判断 unit：从当前 resolved fields 中查找
            fn_unit = self._find_field_unit(fn)
            if fn_unit == "%":
                v = v / 100.0
            if v:
                attrs[fn] = v
            else:
                attrs.pop(fn, None)
        save_play_style(school, name, attrs)

    def _find_field_unit(self, field_name: str) -> str:
        """查找字段单位"""
        from ...combat_attrs import COMBAT_ATTR_FIELDS
        for fn, _dn, unit, _ in COMBAT_ATTR_FIELDS:
            if fn == field_name:
                return unit
        return ""

    def _on_ps_context_menu(self, pos):
        """基础属性列表右键菜单：删除"""
        item = self._ps_list.itemAt(pos)
        if not item:
            return
        menu = QMenu(self)
        del_action = menu.addAction(tr("删除"))
        action = menu.exec(self._ps_list.mapToGlobal(pos))
        if action == del_action:
            self._do_delete_play_style(item.text())

    def _do_delete_play_style(self, name: str):
        """删除基础属性"""
        school = self._current_school()
        if not school:
            return
        ret = QMessageBox.question(
            self, tr("确认删除"),
            tr("确定删除基础属性「{name}」？").format(name=name),
        )
        if ret != QMessageBox.StandardButton.Yes:
            return
        from ...config import delete_play_style
        delete_play_style(school, name)
        self._refresh_play_styles()


class _PlayStyleEditDialog(QDialog):
    """编辑/创建基础属性对话框。"""

    def __init__(self, parent=None, name: str = "", attrs: dict | None = None,
                 school_attr: str | None = None):
        super().__init__(parent)
        self.setWindowTitle(tr("编辑基础属性") if name else tr("创建基础属性"))
        self.setMinimumWidth(400)
        self._school_attr = school_attr
        self._spins: dict[str, QDoubleSpinBox] = {}
        self._setup_ui(name, attrs or {})

    def _get_resolved_fields(self) -> list[tuple[str, list[tuple[str, str, str]]]]:
        """解析占位符字段，返回实际字段列表"""
        from ...combat_attrs import PLAY_STYLE_FIELD_GROUPS, SCHOOL_ATTR_FIELD_MAP

        if not self._school_attr or self._school_attr not in SCHOOL_ATTR_FIELD_MAP:
            # 无流派属性时，跳过属攻相关字段
            result = []
            for label, fields in PLAY_STYLE_FIELD_GROUPS:
                resolved = [(fn, dl, u) for fn, dl, u in fields if not fn.startswith("__")]
                if resolved:
                    result.append((label, resolved))
            return result

        # 有流派属性时，替换占位符
        attr_map = SCHOOL_ATTR_FIELD_MAP[self._school_attr]
        result = []
        for label, fields in PLAY_STYLE_FIELD_GROUPS:
            resolved = []
            for fn, dl, u in fields:
                if fn == "__attr_pen__":
                    resolved.append((attr_map["attr_pen"], dl, u))
                elif fn == "__attr_bonus__":
                    resolved.append((attr_map["attr_bonus"], dl, u))
                elif fn == "__min_attr__":
                    resolved.append((attr_map["min_attr"], dl, u))
                elif fn == "__max_attr__":
                    resolved.append((attr_map["max_attr"], dl, u))
                else:
                    resolved.append((fn, dl, u))
            result.append((label, resolved))
        return result

    def _setup_ui(self, name: str, attrs: dict):
        layout = QVBoxLayout(self)

        # 属性表单（分组同行显示）
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        form_widget = QWidget()
        form = QFormLayout(form_widget)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        resolved_groups = self._get_resolved_fields()
        for group_label, fields in resolved_groups:
            row_widget = QWidget()
            row_layout = QHBoxLayout(row_widget)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(6)

            for field_name, display_label, unit in fields:
                row_layout.addWidget(QLabel(tr(display_label)))
                spin = QDoubleSpinBox()
                spin.setRange(-999999, 999999)
                spin.setDecimals(4)
                spin.setSingleStep(0.1)
                spin.setFixedWidth(100)
                if unit == "%":
                    spin.setSuffix("%")
                v = attrs.get(field_name, 0)
                if unit == "%":
                    spin.setValue(v * 100)
                else:
                    spin.setValue(v)
                row_layout.addWidget(spin)
                self._spins[field_name] = spin

            row_layout.addStretch()
            form.addRow(QLabel(tr(group_label)) if group_label else QLabel(""), row_widget)

        scroll.setWidget(form_widget)
        layout.addWidget(scroll, stretch=1)

        # 名称
        name_row = QHBoxLayout()
        name_row.addWidget(QLabel(tr("基础属性名称")))
        self._edit_name = QLineEdit()
        self._edit_name.setText(name)
        self._edit_name.setPlaceholderText(tr("输入基础属性名称"))
        self._edit_name.setMaxLength(20)
        name_row.addWidget(self._edit_name)
        layout.addLayout(name_row)

        # 按钮
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_save = QPushButton(tr("保存"))
        btn_save.clicked.connect(self._on_save)
        btn_row.addWidget(btn_save)
        btn_cancel = QPushButton(tr("取消"))
        btn_cancel.clicked.connect(self.reject)
        btn_row.addWidget(btn_cancel)
        layout.addLayout(btn_row)

    def _on_save(self):
        if not self._edit_name.text().strip():
            QMessageBox.warning(self, tr("名称为空"), tr("基础属性名称不能为空"))
            return
        self.accept()

    def get_name(self) -> str:
        return self._edit_name.text().strip()

    def get_attrs(self) -> dict:
        result = {}
        for _, fields in self._get_resolved_fields():
            for fn, _, unit in fields:
                spin = self._spins.get(fn)
                if not spin:
                    continue
                v = spin.value()
                if unit == "%":
                    v = v / 100.0
                if v:
                    result[fn] = v
        return result
