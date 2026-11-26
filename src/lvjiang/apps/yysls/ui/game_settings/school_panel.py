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

基础属性数据存于 config/session/yysls/play_styles.json。
"""

from __future__ import annotations

from collections.abc import Sequence

from loguru import logger
from PyQt6.QtCore import QLocale, Qt
from PyQt6.QtGui import QDoubleValidator
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLayout,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from lvjiang.ui.widgets.button_styles import apply_button_style
from lvjiang.ui.widgets.combo_box import AutoWidthComboBox
from lvjiang.ui.widgets.layout_helpers import (
    configure_navigation_list,
    fit_combo_to_contents,
)

from .....i18n import tr, tr_label
from ..domain_labels import domain_label
from ..tune_settings.affix_picker import AffixSelectSortDialog
from .factory_guard import READONLY_HINT, deletable, factory_dict_keys
from .level_combo import LevelCombo
from .order_list import DragOrderListWidget, reorder_mapping

# 配置文件（聚合键值，经 resolver 读合并视图、按模式写回）
_ATTRS_REL = "yysls/game_config"

# 流派属性候选
_SCHOOL_ATTRS = ["鸣金", "裂石", "破竹", "牵丝"]


class SchoolPanel(QWidget):
    """流派配置面板（左：流派列表；右：配置表单）"""

    def __init__(self, parent=None, *, data: dict | None = None, on_changed=None):
        super().__init__(parent)
        self._data: dict = data if data is not None else {}
        self._external_data = data is not None
        self._on_changed = on_changed
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

        self._school_list = DragOrderListWidget()
        configure_navigation_list(self._school_list, minimum_width=200)
        self._school_list.currentRowChanged.connect(self._on_school_changed)
        self._school_list.itemChanged.connect(self._on_item_renamed)
        self._school_list.order_changed.connect(self._on_order_changed)
        left_layout.addWidget(self._school_list)

        btn_layout = QHBoxLayout()
        self._btn_add = QPushButton(tr("添加"))
        self._btn_add.clicked.connect(self._on_add_school)
        btn_layout.addWidget(self._btn_add)
        self._btn_del = QPushButton(tr("删除"))
        self._btn_del.clicked.connect(self._on_del_school)
        btn_layout.addWidget(self._btn_del)
        apply_button_style(self._btn_add)
        apply_button_style(self._btn_del, variant="danger")
        left_layout.addLayout(btn_layout)

        splitter.addWidget(left_widget)

        # ── 右侧：参考词组配置拆分为「流派方案」（默认首屏）与「流派属性」两个子 Tab ──
        right_widget = QWidget()
        right_outer = QVBoxLayout(right_widget)
        right_outer.setContentsMargins(0, 0, 0, 0)

        self._sub_tabs = QTabWidget()

        def _make_scroll_page() -> tuple[QScrollArea, QWidget, QVBoxLayout]:
            scroll = QScrollArea()
            scroll.setFrameShape(QFrame.Shape.NoFrame)
            scroll.setWidgetResizable(True)
            scroll.setHorizontalScrollBarPolicy(
                Qt.ScrollBarPolicy.ScrollBarAlwaysOff
            )
            page = QWidget()
            lay = QVBoxLayout(page)
            lay.setContentsMargins(6, 6, 6, 6)
            lay.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
            scroll.setWidget(page)
            return scroll, page, lay

        self._scheme_scroll, self._details_widget, scheme_page_layout = _make_scroll_page()
        self._attr_scroll, self._attr_page, attr_page_layout = _make_scroll_page()

        hint = QLabel(tr("武器候选来自装备配置的武器类型。武学增效已移至装备配置的武器类型中。"))
        hint.setStyleSheet("color: palette(mid);")
        attr_page_layout.addWidget(hint)

        # ── 武学属性 ──
        attr_group = QGroupBox(tr("武学属性"))
        attr_layout = QVBoxLayout(attr_group)

        # 第一行：属性
        row_attr = QHBoxLayout()
        row_attr.setSpacing(28)
        row_attr.addWidget(QLabel(tr("属性")))
        self._combo_attr = AutoWidthComboBox()
        self._combo_attr.setMinimumWidth(100)
        row_attr.addWidget(self._combo_attr)
        row_attr.addStretch()
        attr_layout.addLayout(row_attr)

        # 第二/三行：主武学+主武器 / 副武学+副武器。
        # 武学是下拉（来自武学配置），武器由武学派生且只读——这两个字段以前
        # 各录各的，写成「武器=枪 + 武学=无名剑法」也存得下来，然后毕业率按枪
        # 算、词条按剑法找，全程静默。派生之后这种不一致在数据层就不存在。
        self._combo_main_martial = AutoWidthComboBox()
        self._combo_main_martial.setMinimumWidth(100)
        self._edit_main_weapon = QLineEdit()
        self._edit_main_weapon.setReadOnly(True)
        self._edit_main_weapon.setMinimumWidth(140)
        self._edit_main_weapon.setPlaceholderText(tr("随武学"))
        self._combo_sub_martial = AutoWidthComboBox()
        self._combo_sub_martial.setMinimumWidth(100)
        self._edit_sub_weapon = QLineEdit()
        self._edit_sub_weapon.setReadOnly(True)
        self._edit_sub_weapon.setMinimumWidth(140)
        self._edit_sub_weapon.setPlaceholderText(tr("随武学"))

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
        grid.addLayout(_pair(tr("主武学"), self._combo_main_martial), 0, 0)
        grid.addLayout(_pair(tr("主武器"), self._edit_main_weapon), 0, 1)
        grid.addLayout(_pair(tr("副武学"), self._combo_sub_martial), 1, 0)
        grid.addLayout(_pair(tr("副武器"), self._edit_sub_weapon), 1, 1)
        attr_layout.addLayout(grid)

        for combo in (self._combo_main_martial, self._combo_sub_martial):
            combo.currentTextChanged.connect(
                lambda _t: self._sync_derived_weapons())
        for combo in self._combos():
            combo.currentTextChanged.connect(self._on_field_changed)
        for edit in self._edits():
            edit.textChanged.connect(self._on_field_changed)

        attr_page_layout.addWidget(attr_group)

        # ── 转律词条库（流派级：转律只能转入库内词条，序即转入优先级） ──
        transmute_group = QGroupBox(tr("转律词条库"))
        transmute_layout = QVBoxLayout(transmute_group)

        pool_row = QHBoxLayout()
        self._pool_label = QLabel()
        self._pool_label.setWordWrap(True)
        self._pool_label.setStyleSheet("color: palette(text);")
        pool_row.addWidget(self._pool_label, 1)
        self._btn_pool_edit = QPushButton(tr("编辑"))
        self._btn_pool_edit.clicked.connect(self._on_pool_edit)
        apply_button_style(self._btn_pool_edit, variant="neutral")
        pool_row.addWidget(self._btn_pool_edit)
        transmute_layout.addLayout(pool_row)

        attr_page_layout.addWidget(transmute_group)
        attr_page_layout.addStretch()

        # ── 方案管理 / 预置基础属性 / 个人基础属性：同一行、等高三栏 ──
        management_layout = QHBoxLayout()
        management_layout.setSpacing(10)

        scheme_group = QGroupBox(tr("方案管理"))
        self._scheme_group = scheme_group
        scheme_group.setMinimumHeight(150)
        scheme_layout = QVBoxLayout(scheme_group)
        self._btn_import_scheme = QPushButton(tr("导入 Excel…"))
        self._btn_import_scheme.clicked.connect(self._on_import_scheme)
        apply_button_style(self._btn_import_scheme, variant="neutral")
        self._scheme_list = QTableWidget(0, 4)
        self._scheme_list.setHorizontalHeaderLabels([
            tr("方案等级"), tr("方案名称"), tr("默认属性"), tr("版本号")])
        header = self._scheme_list.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self._scheme_list.verticalHeader().setVisible(False)
        self._scheme_list.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows)
        self._scheme_list.setEditTriggers(
            QTableWidget.EditTrigger.NoEditTriggers)
        self._scheme_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._scheme_list.customContextMenuRequested.connect(self._on_scheme_context_menu)
        self._scheme_list.currentCellChanged.connect(
            lambda row, _col, _old_row, _old_col: self._on_scheme_selected(row))
        self._scheme_list.cellDoubleClicked.connect(self._on_scheme_double_clicked)
        scheme_layout.addWidget(self._scheme_list, stretch=1)
        scheme_layout.addWidget(self._btn_import_scheme)
        management_layout.addWidget(scheme_group, stretch=2)

        preset_group = QGroupBox(tr("预置基础属性"))
        self._preset_group = preset_group
        preset_group.setMinimumHeight(150)
        preset_layout = QVBoxLayout(preset_group)
        self._preset_list = QListWidget()
        self._preset_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._preset_list.customContextMenuRequested.connect(self._on_preset_context_menu)
        self._preset_list.currentRowChanged.connect(self._on_preset_selected)
        preset_layout.addWidget(self._preset_list, stretch=1)
        self._btn_add_preset = QPushButton(tr("新建预置…"))
        self._btn_add_preset.clicked.connect(self._on_add_preset)
        apply_button_style(self._btn_add_preset, variant="neutral")
        preset_layout.addWidget(self._btn_add_preset)
        management_layout.addWidget(preset_group, stretch=1)

        ps_group = QGroupBox(tr("个人基础属性"))
        self._base_attrs_group = ps_group
        ps_group.setMinimumHeight(150)
        ps_layout = QVBoxLayout(ps_group)
        self._ps_list = QListWidget()
        self._ps_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._ps_list.customContextMenuRequested.connect(self._on_ps_context_menu)
        self._ps_list.currentRowChanged.connect(self._on_ps_selected)
        ps_layout.addWidget(self._ps_list, stretch=1)
        self._btn_add_play_style = QPushButton(tr("新建属性…"))
        self._btn_add_play_style.clicked.connect(self._on_add_play_style)
        apply_button_style(self._btn_add_play_style, variant="neutral")
        ps_layout.addWidget(self._btn_add_play_style)
        management_layout.addWidget(ps_group, stretch=1)
        scheme_page_layout.addLayout(management_layout)

        # ── 数值展示：方案为只读满值，预置与个人基础属性复用可编辑能力 ──
        value_group = QGroupBox(tr("数值展示"))
        value_layout = QVBoxLayout(value_group)
        self._value_group_layout = value_layout
        self._value_source_label = QLabel(tr("请选择方案或基础属性"))
        self._value_source_label.setStyleSheet("color: palette(mid);")
        value_layout.addWidget(self._value_source_label)
        self._value_content_widget = QWidget()
        self._value_content_layout = QVBoxLayout(self._value_content_widget)
        self._value_content_layout.setContentsMargins(4, 4, 4, 4)
        self._value_content_layout.setSpacing(6)
        self._ps_edits: dict[str, QLineEdit] = {}
        self._edit_source: str = ""      # "preset" | "session" | ""
        self._edit_preset_id: str = ""   # 当前编辑的预置基础属性 ID
        self._ps_current_name: str = ""  # 当前编辑的个人基础属性名
        self._scheme_baseline_edit: QLineEdit | None = None
        value_layout.addWidget(self._value_content_widget)
        scheme_page_layout.addWidget(value_group)
        scheme_page_layout.addStretch()

        self._sub_tabs.addTab(self._scheme_scroll, tr("流派方案"))
        self._sub_tabs.addTab(self._attr_scroll, tr("流派属性"))
        right_outer.addWidget(self._sub_tabs)
        splitter.addWidget(right_widget)
        splitter.setChildrenCollapsible(False)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([220, 650])

    def _combos(self) -> list[QComboBox]:
        return [
            self._combo_attr,
            self._combo_main_martial,
            self._combo_sub_martial,
        ]

    def _edits(self) -> list[QLineEdit]:
        return []

    def showEvent(self, event):
        """每次显示时重新加载（武器类型可能已在其他面板变更）"""
        super().showEvent(event)
        self._load_data()

    # ── 数据加载 / 候选 ──────────────────────────────────────

    def _load_data(self):
        """从 YAML 加载数据并刷新列表与表单"""
        if not self._external_data:
            try:
                from ...config.game_config_files import load_game_config
                self._data = load_game_config()
            except Exception as e:
                logger.error(f"加载配置失败: {e}")
                self._data = {}
        self._refresh_list()

    def _schools(self) -> dict[str, dict]:
        return self._data.get("schools") or {}

    def _martial_art_candidates(self) -> list[str]:
        """武学下拉候选：键序即武学配置声明顺序，不再按属性/武器重排。"""
        return [str(name) for name in (self._data.get("martial_arts") or {})]

    def _weapon_of(self, martial_art: str) -> str:
        """武器由武学派生，不接受独立录入。"""
        entry = (self._data.get("martial_arts") or {}).get(martial_art)
        return str(entry.get("weapon") or "") if isinstance(entry, dict) else ""

    def _sync_derived_weapons(self) -> None:
        for combo, edit in ((self._combo_main_martial, self._edit_main_weapon),
                            (self._combo_sub_martial, self._edit_sub_weapon)):
            edit.setText(self._weapon_of(combo.currentText().strip()))

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

    def _on_order_changed(self) -> None:
        """拖动后按当前行序重写流派键序，保持选中项（用户模式无入口）。"""
        if not self._school_list.is_reorderable():
            return
        current = self._school_list.currentItem()
        select = current.text() if current else None
        names = [item.text() for index in range(self._school_list.count())
                 if (item := self._school_list.item(index)) is not None]
        entries = self._data.get("schools") or {}
        ordered = reorder_mapping(entries, names)
        if list(ordered) != list(entries):
            self._data["schools"] = ordered
            self._save_data()
        self._refresh_list(select=select)

    def _current_school(self) -> str | None:
        row = self._school_list.currentRow()
        return self._names[row] if 0 <= row < len(self._names) else None

    def select_school_base_attr(
        self, school: str | None, base_attr: str | None,
    ) -> None:
        """供外部跳转：支持定位预置（preset:<id>）或个人基础属性。"""
        self._sub_tabs.setCurrentIndex(0)
        self._refresh_list(select=school)
        if not base_attr:
            return
        from ...config import parse_preset_ref

        preset_id = parse_preset_ref(base_attr)
        if preset_id:
            for row in range(self._preset_list.count()):
                item = self._preset_list.item(row)
                if item is not None and item.data(Qt.ItemDataRole.UserRole) == preset_id:
                    self._preset_list.setCurrentRow(row)
                    return
            return
        matches = self._ps_list.findItems(
            base_attr, Qt.MatchFlag.MatchExactly,
        )
        if matches:
            self._ps_list.setCurrentItem(matches[0])

    def _on_school_changed(self, row: int):
        """切换流派 → 刷新右侧表单与删除按钮可用性"""
        name = self._names[row] if 0 <= row < len(self._names) else None
        ok, hint = deletable(name, factory_dict_keys(_ATTRS_REL, "schools"),
                             hint=READONLY_HINT)
        self._btn_del.setEnabled(name is not None and ok)
        self._btn_del.setToolTip(hint)
        self._btn_add_preset.setEnabled(name is not None)
        self._btn_add_play_style.setEnabled(name is not None)
        cfg = (self._schools().get(name) or {}) if name else {}
        main = cfg.get("main") or {}
        sub = cfg.get("sub") or {}

        prev_loading = self._loading
        self._loading = True
        arts = self._martial_art_candidates()
        self._fill_combo(
            self._combo_attr,
            _SCHOOL_ATTRS,
            cfg.get("attr"),
            translate_items=True,
        )
        self._fill_combo(self._combo_main_martial, arts, main.get("martial_art"))
        self._fill_combo(self._combo_sub_martial, arts, sub.get("martial_art"))
        self._sync_derived_weapons()
        enabled = name is not None
        for combo in self._combos():
            combo.setEnabled(enabled)
        for edit in self._edits():
            edit.setEnabled(enabled)

        self._loading = prev_loading
        self._refresh_transmute_pool(cfg)
        self._refresh_schemes()
        self._refresh_presets()
        self._refresh_play_styles()
        if self._scheme_list.rowCount():
            self._scheme_list.selectRow(0)
        elif self._preset_list.count():
            self._preset_list.setCurrentRow(0)
        elif self._ps_list.count():
            self._ps_list.setCurrentRow(0)
        else:
            self._clear_ps_editor()
            self._value_source_label.setText(tr("请选择方案或基础属性"))

    @staticmethod
    def _fill_combo(
        combo: QComboBox,
        candidates: list[str],
        value: str | None,
        *,
        translate_items: bool = False,
    ):
        """重建候选并选中当前值；未配置时留空，失效值也保留展示便于改正"""
        combo.clear()
        combo.addItem("", "")  # 未配置占位
        for candidate in candidates:
            label = domain_label(candidate) if translate_items else candidate
            combo.addItem(label, candidate)
        value = value or ""
        if value and value not in candidates:
            combo.addItem(value, value)
        combo.setCurrentIndex(max(combo.findData(value), 0))
        fit_combo_to_contents(combo, minimum=100)

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
        name, ok = QInputDialog.getText(self, tr("添加流派"), tr_label('流派名称'))
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
        attr = str(self._combo_attr.currentData() or "")
        if attr:
            cfg["attr"] = attr
        for key, combo_m in (
            ("main", self._combo_main_martial),
            ("sub", self._combo_sub_martial),
        ):
            group = {}
            martial = combo_m.currentText().strip()
            if martial:
                group["martial_art"] = martial
                # 武器仍然落盘：配置文件格式不变，只是它现在恒等于派生值，
                # 不再是一个可以和武学矛盾的独立录入项。
                weapon = self._weapon_of(martial)
                if weapon:
                    group["weapon"] = weapon
            if group:
                cfg[key] = group
        self._data.setdefault("schools", {})[name] = cfg
        self._save_data()

    # ── 转律词条库 ──────────────────────────────────────

    def _refresh_transmute_pool(self, cfg: dict):
        """按当前流派配置刷新词条展示与按钮可用性"""
        names = cfg.get("transmute_pool") or []
        self._pool_label.setText("、".join(str(n) for n in names) if names else tr("（未配置）"))
        self._pool_label.setStyleSheet(
            "color: palette(text);" if names else "color: palette(mid);")
        enabled = bool(cfg)
        self._btn_pool_edit.setEnabled(enabled)

    def _pool_names(self) -> list[str]:
        text = self._pool_label.text()
        if not text or text == tr("（未配置）"):
            return []
        return [n.strip() for n in text.split("、") if n.strip()]

    def _on_pool_edit(self):
        """打开词条选择对话框（复用调律规则词条库的 AffixSelectSortDialog）"""
        school = self._current_school()
        if not school:
            return
        from ...config import get_game_config
        from ...core.tuning_rules.models import DYNAMIC_AFFIXES, dynamic_affix_map

        gc = get_game_config()
        aliases = dynamic_affix_map(str((self._schools().get(school) or {}).get("attr") or ""))
        # 编辑器保存动态表达；已有字面选择仍保留，取消/重开不改写配置。
        selected = self._pool_names()
        candidates = list(dict.fromkeys([
            *DYNAMIC_AFFIXES[:2],
            *(name for name in gc.get_normal_affix_names()
              if aliases.get(name) not in DYNAMIC_AFFIXES[:2]),
            *selected,
        ])) if aliases else gc.get_normal_affix_names()
        dlg = AffixSelectSortDialog(
            candidates, selected,
            tr("选择转律词条库（{school}）").format(school=school), self)
        if dlg.exec():
            self._write_transmute_pool(school, dlg.selected())

    def _write_transmute_pool(self, school: str, names: list[str]):
        """回写当前流派的转律词条库（空列表省略键）并刷新"""
        cfg = self._schools().get(school) or {}
        if names:
            cfg["transmute_pool"] = names
        else:
            cfg.pop("transmute_pool", None)
        self._data.setdefault("schools", {})[school] = cfg
        self._save_data()
        self._refresh_transmute_pool(cfg)

    # ── 保存 ──────────────────────────────────────────────────

    def _save_data(self):
        """保存数据到 YAML 并刷新 GameConfigManager 单例"""
        if self._on_changed is not None:
            self._on_changed()
            return
        try:
            from ...config.game_config_files import save_game_config
            save_game_config(self._data)
            logger.debug(f"配置已保存: {_ATTRS_REL}")
            from lvjiang.apps.yysls.config import get_game_config
            get_game_config()._load()
        except Exception as e:
            logger.error(f"保存失败: {e}")

    # ── 方案管理 ──────────────────────────────────────────────

    def _format_default_base_attr_cell(
        self, school: str, default_ref: str,
    ) -> tuple[str, str]:
        """返回方案默认属性列的展示文本与提示。"""
        if not default_ref:
            return tr("自动"), tr("双击设置方案绑定的预置基础属性")
        from ...config import get_base_attribute_preset, parse_preset_ref

        preset_id = parse_preset_ref(default_ref)
        if preset_id:
            preset = get_base_attribute_preset(school, preset_id)
            if preset is not None:
                tag = tr("本地") if preset.source == "local" else tr("系统")
                return f"[{tag}] {preset.label}", default_ref
            return tr("{id}（缺失）").format(id=preset_id), default_ref
        return default_ref, default_ref

    def _refresh_schemes(self):
        self._scheme_list.setRowCount(0)
        school = self._current_school()
        if not school:
            self._btn_import_scheme.setEnabled(False)
            return
        self._btn_import_scheme.setEnabled(True)
        from ...core.graduation.model_registry import available_models
        for ref in sorted(
            available_models(school),
            key=lambda ref: (-ref.level, -ref.version, ref.scheme),
        ):
            row = self._scheme_list.rowCount()
            self._scheme_list.insertRow(row)
            level_item = QTableWidgetItem(tr("{level}级").format(level=ref.level))
            # ref 挂在首列：_selected_model_ref 按首列取，列序调整时跟着走。
            level_item.setData(Qt.ItemDataRole.UserRole, ref)
            self._scheme_list.setItem(row, 0, level_item)
            self._scheme_list.setItem(row, 1, QTableWidgetItem(ref.scheme))
            default_text, default_tip = self._format_default_base_attr_cell(
                school, ref.default_base_attribute,
            )
            default_item = QTableWidgetItem(default_text)
            default_item.setToolTip(default_tip)
            if not ref.default_base_attribute:
                default_item.setForeground(self.palette().mid())
            self._scheme_list.setItem(row, 2, default_item)
            self._scheme_list.setItem(
                row, 3,
                QTableWidgetItem(tr("v{version}").format(version=ref.version)))

    def _on_scheme_double_clicked(self, row: int, _col: int) -> None:
        """双击方案行 → 编辑该方案的默认预置基础属性。"""
        self._edit_scheme_default_base_attr(row)

    def _edit_scheme_default_base_attr(self, row: int) -> None:
        school = self._current_school()
        item = self._scheme_list.item(row, 0) if row >= 0 else None
        ref = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        if not school or ref is None:
            return
        from ...config import get_base_attribute_presets, make_preset_ref
        from ...core.graduation import set_graduation_default_base_attribute

        presets = get_base_attribute_presets(school)
        dialog = QDialog(self)
        dialog.setWindowTitle(tr("设置方案默认属性"))
        dialog.setMinimumWidth(340)
        form = QFormLayout(dialog)
        info = QLabel(
            tr("{scheme} · {level}级 v{version}").format(
                scheme=ref.scheme, level=ref.level, version=ref.version,
            )
        )
        info.setStyleSheet("font-weight: 600;")
        form.addRow(tr_label("方案"), info)
        combo = AutoWidthComboBox()
        combo.addItem(tr("自动匹配（按等级与阶段）"), "")
        for preset in presets:
            tag = tr("本地") if preset.source == "local" else tr("系统")
            combo.addItem(f"[{tag}] {preset.label}", make_preset_ref(preset.id))
        cur_idx = combo.findData(ref.default_base_attribute or "")
        if cur_idx < 0 and ref.default_base_attribute:
            combo.addItem(ref.default_base_attribute, ref.default_base_attribute)
            cur_idx = combo.count() - 1
        combo.setCurrentIndex(max(0, cur_idx))
        form.addRow(tr_label("默认预置属性"), combo)
        if not presets:
            hint = QLabel(tr("当前流派暂无预置基础属性，可先在右侧「预置基础属性」中新建。"))
            hint.setStyleSheet("color: palette(mid); font-size: 12px;")
            hint.setWordWrap(True)
            form.addRow(hint)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        selected_ref = str(combo.currentData() or "")
        try:
            set_graduation_default_base_attribute(
                school, ref.scheme, selected_ref, model_ref=ref,
            )
        except Exception as exc:
            logger.error(f"保存方案默认属性失败: {exc}")
            QMessageBox.warning(self, tr("保存失败"), str(exc))
            return
        self._refresh_schemes()
        for r in range(self._scheme_list.rowCount()):
            r_item = self._scheme_list.item(r, 0)
            r_ref = r_item.data(Qt.ItemDataRole.UserRole) if r_item is not None else None
            if (r_ref is not None
                    and r_ref.scheme == ref.scheme
                    and r_ref.level == ref.level
                    and r_ref.version == ref.version):
                self._scheme_list.selectRow(r)
                break

    def _selected_model_ref(self):
        row = self._scheme_list.currentRow()
        item = self._scheme_list.item(row, 0) if row >= 0 else None
        return item.data(Qt.ItemDataRole.UserRole) if item is not None else None

    def _on_import_scheme(self):
        school = self._current_school()
        if not school:
            return
        excel_path, _ = QFileDialog.getOpenFileName(
            self, tr("导入毕业率 Excel"), "", tr("Excel 工作簿 (*.xlsx)"),
        )
        if not excel_path:
            return
        meta_dialog = QDialog(self)
        meta_dialog.setWindowTitle(tr("导入毕业率方案"))
        meta_layout = QFormLayout(meta_dialog)
        name_edit = QLineEdit(tr("基础方案"))
        level_combo = LevelCombo()
        from ...config import get_game_config
        current_level = get_game_config().current_equip_level()
        if current_level:
            level_combo.set_level(current_level)
        level_combo.setToolTip(tr(
            "默认使用当前赛季等级，也可以手动选择旧等级。"))
        meta_layout.addRow(tr_label('方案名称'), name_edit)
        meta_layout.addRow(tr_label('方案等级'), level_combo)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(meta_dialog.accept)
        buttons.rejected.connect(meta_dialog.reject)
        meta_layout.addRow(buttons)
        if meta_dialog.exec() != QDialog.DialogCode.Accepted:
            return
        name = name_edit.text().strip()
        model_level = level_combo.get_level()
        if not name or model_level is None:
            QMessageBox.warning(self, tr("导入失败"), tr("请填写方案名称并选择方案等级"))
            return
        try:
            from ...core.graduation import invalidate_graduation_cache
            from ...core.graduation.graduation_converter import import_graduation_scheme
            destination, outputs = import_graduation_scheme(
                excel_path, school, name, model_level)
            invalidate_graduation_cache()
        except Exception as exc:
            logger.exception("导入毕业率方案失败")
            QMessageBox.critical(self, tr("导入失败"), str(exc))
            return
        self._refresh_schemes()
        for row in range(self._scheme_list.rowCount() - 1, -1, -1):
            ref = self._scheme_list.item(row, 0).data(Qt.ItemDataRole.UserRole)
            if ref.scheme == name and ref.level == model_level:
                self._scheme_list.selectRow(row)
                break
        QMessageBox.information(
            self, tr("导入成功"),
            tr("方案「{name}」已生成。\nDPS：{dps:.2f}\n文件：{path}").format(
                name=name, dps=outputs["dps"], path=str(destination),
            ),
        )

    def _on_scheme_context_menu(self, pos):
        """方案列表右键菜单：设置默认属性 / 删除"""
        item = self._scheme_list.itemAt(pos)
        if not item:
            return
        menu = QMenu(self)
        default_action = menu.addAction(tr("设置默认属性…"))
        del_action = menu.addAction(tr("删除"))
        action = menu.exec(self._scheme_list.mapToGlobal(pos))
        if action == default_action:
            self._scheme_list.selectRow(item.row())
            self._edit_scheme_default_base_attr(item.row())
        elif action == del_action:
            self._scheme_list.selectRow(item.row())
            self._do_delete_scheme()

    def _do_delete_scheme(self):
        """删除毕业率方案：移除 JSON 文件 + 更新 game_config.yaml"""
        school = self._current_school()
        ref = self._selected_model_ref()
        if not school or ref is None:
            return
        ret = QMessageBox.question(
            self, tr("确认删除"),
            tr("确定删除方案「{name}」的 {level}级 v{version}？\n"
               "对应的 JSON 文件也会被删除。").format(
                   name=ref.scheme, level=ref.level, version=ref.version),
        )
        if ret != QMessageBox.StandardButton.Yes:
            return
        # 1. 先删除 JSON 文件（失败则中止，保持配置与磁盘一致）
        try:
            from lvjiang.core.config.resolver import get_resolver
            get_resolver().delete_entity(ref.rel_path)
            logger.info(f"已删除方案文件: {ref.rel_path}")
        except Exception as exc:
            logger.warning(f"删除方案文件失败: {exc}")
            QMessageBox.warning(
                self, tr("删除失败"),
                tr("无法删除方案文件: {exc}").format(exc=exc),
            )
            return
        # 2. 失效毕业率缓存；若该逻辑方案已无任何版本，再移除方案登记。
        try:
            from ...core.graduation import invalidate_graduation_cache
            invalidate_graduation_cache()
        except Exception:
            pass
        self._refresh_schemes()

    # ── 基础属性管理 ──────────────────────────────────────────

    def _on_add_play_style(self) -> None:
        """在当前流派中新建基础属性，不覆盖已有同名项。"""
        school = self._current_school()
        if not school:
            return
        from ...config import (
            PRESET_REF_PREFIX,
            get_game_config,
            get_play_styles,
            save_play_style,
        )

        dialog = _PlayStyleEditDialog(
            self, school_attr=get_game_config().get_school_attr(school),
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        name = dialog.get_name()
        if name.startswith(PRESET_REF_PREFIX):
            QMessageBox.warning(
                self, tr("无法保存"),
                tr("基础属性名称不能以 {prefix} 开头").format(prefix=PRESET_REF_PREFIX),
            )
            return
        if name in get_play_styles(school):
            QMessageBox.warning(
                self, tr("名称已存在"),
                tr("基础属性「{name}」已存在，请使用其他名称。").format(name=name),
            )
            return
        save_play_style(school, name, dialog.get_attrs())
        self._refresh_play_styles()
        matches = self._ps_list.findItems(name, Qt.MatchFlag.MatchExactly)
        if matches:
            self._ps_list.setCurrentItem(matches[0])

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

    # ── 预置基础属性管理 ──────────────────────────────────────

    def _refresh_presets(self, select_id: str | None = None):
        """刷新当前流派的预置基础属性列表（system + local 合并视图）。"""
        school = self._current_school()
        self._preset_list.blockSignals(True)
        self._preset_list.clear()
        self._preset_list.blockSignals(False)
        if not school:
            return
        from ...config import get_base_attribute_presets

        target_row = -1
        for idx, preset in enumerate(get_base_attribute_presets(school)):
            tag = tr("本地") if preset.source == "local" else tr("系统")
            item = QListWidgetItem(f"[{tag}] {preset.label}")
            item.setData(Qt.ItemDataRole.UserRole, preset.id)
            item.setData(Qt.ItemDataRole.UserRole + 1, preset.source)
            item.setToolTip(
                tr("ID: {id} · {level}级 {stage}阶段 · 来源：{source}").format(
                    id=preset.id,
                    level=preset.level,
                    stage=preset.stage,
                    source=tag,
                )
            )
            self._preset_list.addItem(item)
            if select_id and preset.id == select_id:
                target_row = idx
        if target_row >= 0:
            self._preset_list.setCurrentRow(target_row)

    def _on_add_preset(self) -> None:
        """在当前流派中新建预置基础属性（复用属性编辑弹窗）。"""
        school = self._current_school()
        if not school:
            return
        from ...config import (
            get_base_attribute_preset,
            get_game_config,
            save_base_attribute_preset,
        )

        gc = get_game_config()
        dialog = _PlayStyleEditDialog(
            self,
            school_attr=gc.get_school_attr(school),
            preset_mode=True,
            level=gc.current_equip_level() or 115,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        preset = dialog.get_preset(school)
        if get_base_attribute_preset(school, preset.id) is not None:
            QMessageBox.warning(
                self, tr("预置已存在"),
                tr("预置基础属性「{id}」已存在，请使用其他 ID。").format(id=preset.id),
            )
            return
        try:
            save_base_attribute_preset(school, preset)
        except Exception as exc:
            logger.error(f"保存预置基础属性失败: {exc}")
            QMessageBox.warning(self, tr("保存失败"), str(exc))
            return
        self._refresh_presets(select_id=preset.id)
        self._refresh_schemes()

    def _on_preset_selected(self, row: int) -> None:
        """选中预置基础属性 → 复用标准数值展示与编辑卡片。"""
        school = self._current_school()
        if not school or row < 0:
            return
        item = self._preset_list.item(row)
        if item is None:
            return
        preset_id = str(item.data(Qt.ItemDataRole.UserRole) or "")
        from ...config import get_base_attribute_preset

        preset = get_base_attribute_preset(school, preset_id)
        if preset is None:
            return
        self._scheme_list.blockSignals(True)
        self._scheme_list.setCurrentCell(-1, -1)
        self._scheme_list.clearSelection()
        self._scheme_list.blockSignals(False)
        self._ps_list.blockSignals(True)
        self._ps_list.setCurrentRow(-1)
        self._ps_list.blockSignals(False)
        tag = tr("本地预置") if preset.source == "local" else tr("系统预置")
        self._show_editable_base_attrs(
            school,
            preset.stats,
            title=tr("{tag}：{label}（{id}）").format(
                tag=tag, label=preset.label, id=preset.id,
            ),
            source="preset",
            key=preset.id,
        )

    def _on_preset_context_menu(self, pos) -> None:
        """预置基础属性列表右键菜单：编辑元信息 / 删除或还原默认。"""
        item = self._preset_list.itemAt(pos)
        if not item:
            return
        preset_id = str(item.data(Qt.ItemDataRole.UserRole) or "")
        source = str(item.data(Qt.ItemDataRole.UserRole + 1) or "system")
        from .factory_guard import READONLY_HINT, is_user_mode

        menu = QMenu(self)
        menu.setToolTipsVisible(True)
        edit_meta_action = menu.addAction(tr("编辑元信息…"))
        del_label = tr("删除 / 还原默认") if source == "local" else tr("删除")
        del_action = menu.addAction(del_label)
        if del_action is not None and is_user_mode() and source == "system":
            del_action.setEnabled(False)
            del_action.setToolTip(READONLY_HINT)
        action = menu.exec(self._preset_list.mapToGlobal(pos))
        if action == edit_meta_action:
            self._do_edit_preset_meta(preset_id)
        elif del_action is not None and action == del_action and del_action.isEnabled():
            self._do_delete_preset(preset_id)

    def _do_edit_preset_meta(self, preset_id: str) -> None:
        school = self._current_school()
        if not school:
            return
        from ...config import (
            get_base_attribute_preset,
            get_game_config,
            save_base_attribute_preset,
        )

        preset = get_base_attribute_preset(school, preset_id)
        if preset is None:
            return
        dialog = _PlayStyleEditDialog(
            self,
            name=preset.label,
            attrs=dict(preset.stats),
            school_attr=get_game_config().get_school_attr(school),
            preset_mode=True,
            level=preset.level,
            stage=preset.stage,
            playstyle=preset.playstyle or "",
            preset_id=preset.id,
            lock_preset_id=True,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        updated = dialog.get_preset(school)
        try:
            save_base_attribute_preset(school, updated)
        except Exception as exc:
            logger.error(f"更新预置基础属性失败: {exc}")
            QMessageBox.warning(self, tr("保存失败"), str(exc))
            return
        self._refresh_presets(select_id=updated.id)
        self._refresh_schemes()

    def _do_delete_preset(self, preset_id: str) -> None:
        school = self._current_school()
        if not school:
            return
        ret = QMessageBox.question(
            self, tr("确认删除"),
            tr("确定删除预置基础属性「{id}」？（普通模式下本地覆盖将还原为系统默认）").format(
                id=preset_id,
            ),
        )
        if ret != QMessageBox.StandardButton.Yes:
            return
        from ...config import delete_base_attribute_preset

        try:
            delete_base_attribute_preset(school, preset_id)
        except Exception as exc:
            logger.warning(f"删除预置基础属性失败: {exc}")
            QMessageBox.warning(self, tr("删除失败"), str(exc))
            return
        self._refresh_presets()
        self._refresh_schemes()
        if self._edit_source == "preset" and self._edit_preset_id == preset_id:
            self._clear_ps_editor()
            self._value_source_label.setText(tr("请选择方案或基础属性"))

    def _do_save_play_style_as_preset(self, name: str) -> None:
        """将一条个人基础属性另存为预置基础属性。"""
        school = self._current_school()
        if not school:
            return
        from ...config import (
            get_base_attribute_preset,
            get_game_config,
            get_play_styles,
            save_base_attribute_preset,
        )

        attrs = dict(get_play_styles(school).get(name, {}))
        gc = get_game_config()
        dialog = _PlayStyleEditDialog(
            self,
            attrs=attrs,
            school_attr=gc.get_school_attr(school),
            preset_mode=True,
            level=gc.current_equip_level() or 115,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        preset = dialog.get_preset(school)
        if get_base_attribute_preset(school, preset.id) is not None:
            ret = QMessageBox.question(
                self, tr("覆盖确认"),
                tr("预置基础属性「{id}」已存在，是否覆盖？").format(id=preset.id),
            )
            if ret != QMessageBox.StandardButton.Yes:
                return
        try:
            save_base_attribute_preset(school, preset)
        except Exception as exc:
            logger.error(f"另存为预置基础属性失败: {exc}")
            QMessageBox.warning(self, tr("保存失败"), str(exc))
            return
        self._refresh_presets(select_id=preset.id)
        self._refresh_schemes()

    def _clear_ps_editor(self):
        """清空共享数值展示区。"""
        self._ps_edits.clear()
        self._edit_source = ""
        self._edit_preset_id = ""
        self._ps_current_name = ""
        self._scheme_baseline_edit = None
        self._replace_value_content([])

    def _replace_value_content(self, widgets: list[QWidget]) -> None:
        """在未展示的容器里装好卡片，再一次替换，避免中途高度塌缩。"""
        old = self._value_content_widget
        new = QWidget()
        layout = QVBoxLayout(new)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(6)
        for widget in widgets:
            layout.addWidget(widget)
        if widgets:
            layout.addStretch()
        layout.activate()
        new.adjustSize()
        self._value_group_layout.replaceWidget(old, new)
        new.show()
        self._value_content_widget = new
        self._value_content_layout = layout
        old.hide()
        old.setParent(None)
        old.deleteLater()
        self._details_widget.layout().invalidate()

    def _on_scheme_selected(self, row: int) -> None:
        """选中方案 → 展示 Excel 输入满值（输入契约不包含食物加成）。"""
        school = self._current_school()
        if not school or row < 0:
            return
        self._preset_list.blockSignals(True)
        self._preset_list.setCurrentRow(-1)
        self._preset_list.blockSignals(False)
        self._ps_list.blockSignals(True)
        self._ps_list.setCurrentRow(-1)
        self._ps_list.blockSignals(False)
        self._ps_edits.clear()
        self._edit_source = ""
        self._edit_preset_id = ""
        self._ps_current_name = ""
        self._scheme_baseline_edit = None
        item = self._scheme_list.item(row, 0)
        if item is None:
            return
        ref = item.data(Qt.ItemDataRole.UserRole)
        scheme = ref.scheme
        self._value_source_label.setText(
            tr("方案：{name} · {level}级 v{version}（满值属性，不含食物加成）")
            .format(name=scheme, level=ref.level, version=ref.version)
        )
        try:
            from ...core.graduation import (
                get_graduation_scheme_combat_attrs,
                get_graduation_scheme_metrics,
            )

            attrs = get_graduation_scheme_combat_attrs(
                school, scheme, model_ref=ref)
            adps, baseline_dps = get_graduation_scheme_metrics(
                school, scheme, model_ref=ref)
        except Exception as exc:
            logger.error(f"读取毕业率方案数值失败: {exc}")
            self._value_source_label.setText(tr("方案数值读取失败：{error}").format(
                error=str(exc),
            ))
            self._replace_value_content([])
            return
        from ...config import get_game_config

        school_attr = get_game_config().get_school_attr(school)
        self._replace_value_content([
            self._create_scheme_metrics_widget(
                school, scheme, ref, adps, baseline_dps),
            self._create_standard_attrs_widget(attrs, school_attr, editable=False),
        ])

    def _create_scheme_metrics_widget(
        self, school: str, scheme: str, model_ref,
        adps: float, baseline_dps: float,
    ) -> QWidget:
        """四张属性卡片下方的方案 DPS 校正区。"""
        panel = QFrame()
        panel.setObjectName("schemeMetricsPanel")
        panel.setStyleSheet(
            "QFrame#schemeMetricsPanel {"
            " background-color: palette(base);"
            " border: 1px solid palette(midlight); border-radius: 6px;"
            "}"
        )
        layout = QHBoxLayout(panel)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(10)
        layout.addWidget(QLabel(tr("方案 ADPS")))
        adps_value = QLabel(f"{adps:.2f}")
        adps_value.setObjectName("schemeAdpsValue")
        adps_value.setStyleSheet("font-weight: 600;")
        layout.addWidget(adps_value)
        layout.addStretch()
        layout.addWidget(QLabel(tr("100%毕业率基准 DPS")))
        edit = QLineEdit(f"{baseline_dps:.2f}")
        edit.setObjectName("graduationBaselineDpsEdit")
        edit.setAlignment(Qt.AlignmentFlag.AlignRight)
        edit.setFixedSize(130, 24)
        baseline_validator = QDoubleValidator(0.01, 999999999.0, 2, edit)
        baseline_validator.setLocale(QLocale.c())
        baseline_validator.setNotation(QDoubleValidator.Notation.StandardNotation)
        edit.setValidator(baseline_validator)
        edit.setToolTip(tr("修改后，毕业率按 当前ADPS ÷ 此基准DPS 重新计算"))
        edit.editingFinished.connect(
            lambda: self._save_scheme_baseline_dps(
                school, scheme, model_ref, edit)
        )
        self._scheme_baseline_edit = edit
        layout.addWidget(edit)
        return panel

    def _save_scheme_baseline_dps(
        self, school: str, scheme: str, model_ref, edit: QLineEdit,
    ) -> None:
        """保存人工校正的 100% 毕业率基准 DPS。"""
        try:
            value = float(edit.text())
            from ...core.graduation import set_graduation_baseline_dps

            set_graduation_baseline_dps(
                school, scheme, value, model_ref=model_ref)
            edit.setText(f"{value:.2f}")
        except Exception as exc:
            logger.error(f"保存毕业率基准 DPS 失败: {exc}")
            QMessageBox.warning(self, tr("保存失败"), str(exc))
            try:
                from ...core.graduation import get_graduation_scheme_metrics

                _adps, old_value = get_graduation_scheme_metrics(
                    school, scheme, model_ref=model_ref)
                edit.setText(f"{old_value:.2f}")
            except Exception:
                pass

    def _show_editable_base_attrs(
        self,
        school: str,
        attrs: dict,
        *,
        title: str,
        source: str,
        key: str,
    ) -> None:
        """预置基础属性与个人基础属性共用的标准卡片展示与编辑入口。"""
        from ...config import get_game_config
        from ...core.combat.combat_attrs import CombatAttributes

        self._ps_edits.clear()
        self._scheme_baseline_edit = None
        self._edit_source = source
        self._edit_preset_id = key if source == "preset" else ""
        self._ps_current_name = key if source == "session" else ""
        self._value_source_label.setText(title)

        school_attr = get_game_config().get_school_attr(school)
        combat_attrs = CombatAttributes.from_dict(attrs)
        card = self._create_standard_attrs_widget(
            combat_attrs, school_attr, editable=True,
        )
        self._replace_value_content([card])

    def _on_ps_selected(self, row: int):
        """选中个人基础属性 → 复用标准可编辑卡片"""
        school = self._current_school()
        if not school or row < 0:
            return
        self._scheme_list.blockSignals(True)
        self._scheme_list.setCurrentCell(-1, -1)
        self._scheme_list.clearSelection()
        self._scheme_list.blockSignals(False)
        self._preset_list.blockSignals(True)
        self._preset_list.setCurrentRow(-1)
        self._preset_list.blockSignals(False)
        from ...config import get_play_styles

        styles = get_play_styles(school)
        name = self._ps_list.item(row).text()
        self._show_editable_base_attrs(
            school,
            styles.get(name, {}),
            title=tr("个人基础属性：{name}").format(name=name),
            source="session",
            key=name,
        )

    def _create_standard_attrs_widget(
        self, attrs, school_attr: str | None, *, editable: bool,
    ) -> QWidget:
        """按战斗属性页的四张标准卡片展示 CombatAttributes。"""
        from ...core.combat.combat_attrs import SCHOOL_ATTR_FIELD_MAP
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
                    lines.append(tr("{name}攻击：{value}").format(
                        name=tr(name),
                        value=f"{values[0]:.2f} - {values[1]:.2f}"))
                elif kind == "伤害加成":
                    lines.append(tr("{name}伤害加成：{value}").format(
                        name=tr(name), value=f"{values[0] * 100:.2f}%"))
                else:
                    lines.append(tr("{name}穿透：{value}").format(
                        name=tr(name), value=f"{values[0]:.2f}"))
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
                line_edit = QLineEdit()
                value_widget: QWidget = line_edit
                line_edit.setAlignment(Qt.AlignmentFlag.AlignRight)
                line_edit.setFixedHeight(26)
                field_validator = QDoubleValidator(-999999.0, 999999.0, 4, line_edit)
                field_validator.setLocale(QLocale.c())
                field_validator.setNotation(QDoubleValidator.Notation.StandardNotation)
                line_edit.setValidator(field_validator)
                line_edit.setText(
                    f"{value * 100:.2f}" if unit == "%" else f"{value:.2f}"
                )
                # 依当前数值限制宽度，但允许窗口缩小时继续收缩。
                text_width = line_edit.fontMetrics().horizontalAdvance(line_edit.text())
                line_edit.setMaximumWidth(max(72, min(text_width + 30, 112)))
                line_edit.textChanged.connect(
                    lambda _text, fn=field_name, u=unit: self._on_ps_field_changed(fn, u)
                )
                self._ps_edits[field_name] = line_edit
            else:
                text = f"{value * 100:.2f}%" if unit == "%" else (
                    f"{value:.2f}" if value != int(value) else str(int(value))
                )
                value_widget = QLabel(text)
                value_widget.setFixedHeight(24)
                value_widget.setStyleSheet("font-size: 13px; font-weight: 600;")
                value_widget.setAlignment(Qt.AlignmentFlag.AlignRight)
            if tip:
                label.setToolTip(tip)
                value_widget.setToolTip(tip)
            cell_layout.addWidget(value_widget)
            return cell

        def make_card(title: str, cells: Sequence[tuple | None]) -> QGroupBox:  # type: ignore[type-arg]
            group = QGroupBox(tr(title))
            grid = QGridLayout(group)
            grid.setContentsMargins(12, 8, 12, 10)
            grid.setHorizontalSpacing(20)
            grid.setVerticalSpacing(7)
            for index, cell_args in enumerate(cells):
                if cell_args is not None:
                    widget = make_cell(*cell_args)
                else:
                    # 空属性仍占据与真实行一致的标准数据单元高度（编辑态 26px，只读态 24px），
                    # 保证攻击属性与增益效果行数一致、下方判定属性与伤害加成严格对齐。
                    widget = QWidget()
                    widget.setFixedHeight(26 if editable else 24)
                grid.addWidget(widget, index // 2, index % 2)
            grid.setColumnStretch(0, 1)
            grid.setColumnStretch(1, 1)
            return group

        pen_tip = tooltip("穿透")
        bonus_tip = tooltip("伤害加成")
        attack_cells: list[tuple | None] = [
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
        weapon_cells: list[tuple | None] = [
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
            # 115 起单体/群体类奇术增伤合并成全奇术增伤，聚合时两路都已折进
            # all_qs_bonus，再分别展示只会永远显示 0。
            ("all_qs_bonus", "全奇术增伤", "%"),
            ("boss_bonus", "对首领单位增伤", "%"),
            ("player_bonus", "对玩家单位增效", "%"),
            skill_cell,
            None,
        ]
        # 增益效果与攻击属性（默认 6 行 = 12 格）保持相同行数，不足补空位，
        # 确保下方的「判定属性」与「伤害加成」卡片顶部严格对齐。
        target_top_cells = max(len(attack_cells), len(gain_cells), 12)
        if target_top_cells % 2:
            target_top_cells += 1
        if len(attack_cells) < target_top_cells:
            attack_cells.extend([None] * (target_top_cells - len(attack_cells)))
        if len(gain_cells) < target_top_cells:
            gain_cells.extend([None] * (target_top_cells - len(gain_cells)))
        damage_cells = [
            ("crit_dmg", "会心伤害加成", "%"),
            ("intent_dmg", "会意伤害加成", "%"),
            ("outer_bonus", "外功伤害加成", "%"),
            (None, "外功伤害减免", "%", "", 0.0),
            (attr_fields["attr_bonus"], "属攻伤害加成", "%", bonus_tip),
            (None, "属攻伤害减免", "%", "", 0.0),
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
        """任一字段变更 → 按当前选中来源自动保存该字段到预置或个人基础属性。"""
        school = self._current_school()
        edit = self._ps_edits.get(field_name)
        if not school or not self._edit_source or edit is None:
            return
        if edit.text().strip() in ("-", "+", ".", "-.", "+."):
            return

        def _apply_single_edit(base: dict) -> dict[str, float]:
            attrs = dict(base)
            try:
                v = float(edit.text())
            except (ValueError, TypeError):
                v = 0.0
            fn_unit = unit or self._find_field_unit(field_name)
            if fn_unit == "%":
                v = v / 100.0
            if v:
                attrs[field_name] = v
            else:
                attrs.pop(field_name, None)
            return attrs

        if self._edit_source == "preset":
            preset_id = self._edit_preset_id
            if not preset_id:
                return
            from dataclasses import replace

            from ...config import (
                get_base_attribute_preset,
                save_base_attribute_preset,
            )

            preset = get_base_attribute_preset(school, preset_id)
            if preset is None:
                return
            updated = replace(preset, stats=_apply_single_edit(preset.stats))
            try:
                save_base_attribute_preset(school, updated)
            except Exception as exc:
                logger.error(f"自动保存预置基础属性失败: {exc}")
                return
            # 若普通用户首次覆盖系统预置，刷新列表标签为 [本地]
            refreshed = get_base_attribute_preset(school, preset_id)
            if refreshed is not None and refreshed.source != preset.source:
                cur_item = self._preset_list.currentItem()
                if cur_item is not None and cur_item.data(Qt.ItemDataRole.UserRole) == preset_id:
                    tag = tr("本地") if refreshed.source == "local" else tr("系统")
                    cur_item.setText(f"[{tag}] {refreshed.label}")
                    cur_item.setData(Qt.ItemDataRole.UserRole + 1, refreshed.source)
                    self._refresh_schemes()
            return

        name = self._ps_current_name
        if not name:
            return
        from ...config import get_play_styles, save_play_style

        styles = get_play_styles(school)
        save_play_style(school, name, _apply_single_edit(styles.get(name, {})))

    def _find_field_unit(self, field_name: str) -> str:
        """查找字段单位"""
        from ...core.combat.combat_attrs import COMBAT_ATTR_FIELDS
        for fn, _dn, unit, _ in COMBAT_ATTR_FIELDS:
            if fn == field_name:
                return unit
        return ""

    def _on_ps_context_menu(self, pos):
        """个人基础属性列表右键菜单：另存为预置 / 删除"""
        item = self._ps_list.itemAt(pos)
        if not item:
            return
        menu = QMenu(self)
        as_preset_action = menu.addAction(tr("另存为预置…"))
        del_action = menu.addAction(tr("删除"))
        action = menu.exec(self._ps_list.mapToGlobal(pos))
        if action == as_preset_action:
            self._do_save_play_style_as_preset(item.text())
        elif action == del_action:
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


def _format_attr_input(value: float) -> str:
    """格式化基础属性数值到输入框文本（零值留空，非零去多余尾零）。"""
    if not value:
        return ""
    text = f"{value:.4f}".rstrip("0").rstrip(".")
    return "0" if text == "-0" else text


class _PlayStyleEditDialog(QDialog):
    """编辑/创建基础属性对话框（个人属性与预置属性共用同一套属性表单）。"""

    def __init__(
        self,
        parent=None,
        name: str = "",
        attrs: dict | None = None,
        school_attr: str | None = None,
        *,
        preset_mode: bool = False,
        level: int = 115,
        stage: int = 1,
        playstyle: str = "",
        preset_id: str = "",
        lock_preset_id: bool = False,
    ):
        super().__init__(parent)
        self._preset_mode = preset_mode
        self._preset_level = level or 115
        self._preset_stage = max(1, stage)
        self._preset_playstyle = playstyle
        self._preset_id = preset_id
        self._lock_preset_id = lock_preset_id
        self._id_manually_edited = bool(preset_id)
        if preset_mode:
            title = tr("编辑预置基础属性") if preset_id else tr("创建预置基础属性")
        else:
            title = tr("编辑基础属性") if name else tr("创建基础属性")
        self.setWindowTitle(title)
        self._school_attr = school_attr
        self._initial_attrs = dict(attrs or {})
        self._edits: dict[str, QLineEdit] = {}
        self._setup_ui(name, self._initial_attrs)

    def _get_resolved_fields(self) -> list[tuple[str, list[tuple[str, str, str]]]]:
        """解析占位符字段，返回实际字段列表"""
        from ...core.combat.combat_attrs import (
            PLAY_STYLE_FIELD_GROUPS,
            SCHOOL_ATTR_FIELD_MAP,
        )

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
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(12)
        layout.setSizeConstraint(QLayout.SizeConstraint.SetFixedSize)

        # 属性表单：统一 6 列网格按内容自然包裹，不用滚动区、不预填 0
        form_frame = QFrame()
        form_frame.setFrameShape(QFrame.Shape.StyledPanel)
        grid = QGridLayout(form_frame)
        grid.setContentsMargins(12, 10, 12, 10)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(8)
        grid.setColumnMinimumWidth(3, 20)

        resolved_groups = self._get_resolved_fields()
        for row_idx, (group_label, fields) in enumerate(resolved_groups):
            if group_label:
                grp = QLabel(tr(group_label))
                grp.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                grp.setStyleSheet("font-weight: 600;")
                grid.addWidget(grp, row_idx, 0)

            for col_idx, (field_name, display_label, unit) in enumerate(fields[:2]):
                if unit:
                    display_label = tr("{label}（{unit}）").format(
                        label=tr(display_label), unit=unit,
                    )
                label = QLabel(tr(display_label))
                label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
                edit = QLineEdit()
                edit.setAlignment(Qt.AlignmentFlag.AlignRight)
                edit.setMinimumWidth(120)
                edit.setMinimumHeight(26)
                validator = QDoubleValidator(-999999.0, 999999.0, 4, edit)
                validator.setNotation(QDoubleValidator.Notation.StandardNotation)
                validator.setLocale(QLocale.c())
                edit.setValidator(validator)
                edit.setPlaceholderText("")
                if field_name in attrs:
                    v = attrs[field_name]
                    if isinstance(v, (int, float)) and not isinstance(v, bool) and v:
                        edit.setText(_format_attr_input(float(v) * 100.0 if unit == "%" else float(v)))
                label_col = 1 if col_idx == 0 else 4
                edit_col = 2 if col_idx == 0 else 5
                grid.addWidget(label, row_idx, label_col)
                grid.addWidget(edit, row_idx, edit_col)
                self._edits[field_name] = edit

        grid.setColumnStretch(2, 1)
        grid.setColumnStretch(5, 1)
        layout.addWidget(form_frame)

        if self._preset_mode:
            meta_form = QFormLayout()
            self._spin_level = QSpinBox()
            self._spin_level.setRange(1, 999)
            self._spin_level.setValue(self._preset_level)
            self._spin_stage = QSpinBox()
            self._spin_stage.setRange(1, 99)
            self._spin_stage.setValue(self._preset_stage)
            self._edit_playstyle = QLineEdit(self._preset_playstyle)
            self._edit_playstyle.setPlaceholderText(tr("留空表示流派默认（如 PVE / PVP）"))
            self._edit_name = QLineEdit(name)
            self._edit_name.setPlaceholderText(tr("留空自动生成，如：115级 1阶段（默认）"))
            self._edit_preset_id = QLineEdit(self._preset_id or self._suggest_preset_id())
            self._edit_preset_id.setReadOnly(self._lock_preset_id)
            self._edit_preset_id.textEdited.connect(self._on_preset_id_edited)
            self._spin_level.valueChanged.connect(self._sync_auto_preset_id)
            self._spin_stage.valueChanged.connect(self._sync_auto_preset_id)
            self._edit_playstyle.textChanged.connect(self._sync_auto_preset_id)

            lv_row = QHBoxLayout()
            lv_row.addWidget(self._spin_level)
            lv_row.addWidget(QLabel(tr("阶段")))
            lv_row.addWidget(self._spin_stage)
            lv_row.addStretch()
            meta_form.addRow(tr_label("等级"), lv_row)
            meta_form.addRow(tr_label("玩法（可选）"), self._edit_playstyle)
            meta_form.addRow(tr_label("显示名称"), self._edit_name)
            meta_form.addRow(tr_label("预置 ID"), self._edit_preset_id)
            layout.addLayout(meta_form)
        else:
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
        apply_button_style(btn_save)
        apply_button_style(btn_cancel, variant="neutral")
        layout.addLayout(btn_row)
        self.adjustSize()

    def _suggest_preset_id(self) -> str:
        lv = self._spin_level.value()
        st = self._spin_stage.value()
        ps = self._edit_playstyle.text().strip()
        if ps:
            safe_ps = ps.lower().replace(" ", "_")
            return f"{lv}_s{st}_{safe_ps}"
        return f"{lv}_s{st}"

    def _on_preset_id_edited(self, _text: str) -> None:
        self._id_manually_edited = True

    def _sync_auto_preset_id(self, *_args) -> None:
        if not self._lock_preset_id and not self._id_manually_edited:
            self._edit_preset_id.setText(self._suggest_preset_id())

    def _on_save(self):
        if self._preset_mode:
            if not self._edit_preset_id.text().strip():
                QMessageBox.warning(self, tr("ID 为空"), tr("预置 ID 不能为空"))
                return
            self.accept()
            return
        name = self._edit_name.text().strip()
        if not name:
            QMessageBox.warning(self, tr("名称为空"), tr("基础属性名称不能为空"))
            return
        from ...config import PRESET_REF_PREFIX

        if name.startswith(PRESET_REF_PREFIX):
            QMessageBox.warning(
                self, tr("无法保存"),
                tr("基础属性名称不能以 {prefix} 开头").format(prefix=PRESET_REF_PREFIX),
            )
            return
        self.accept()

    def get_name(self) -> str:
        return self._edit_name.text().strip()

    def get_preset(self, school: str):
        from ...config import BaseAttributePreset

        level = self._spin_level.value()
        stage = self._spin_stage.value()
        playstyle = self._edit_playstyle.text().strip() or None
        label = self._edit_name.text().strip() or (
            f"{level}级 {stage}阶段（{playstyle}）"
            if playstyle else f"{level}级 {stage}阶段（默认）"
        )
        return BaseAttributePreset(
            school=school,
            id=self._edit_preset_id.text().strip(),
            level=level,
            stage=stage,
            playstyle=playstyle,
            label=label,
            stats=self.get_attrs(),
        )

    def get_attrs(self) -> dict:
        result = dict(self._initial_attrs)
        for _, fields in self._get_resolved_fields():
            for fn, _, unit in fields:
                edit = self._edits.get(fn)
                if not edit:
                    continue
                text = edit.text().strip()
                if not text:
                    result.pop(fn, None)
                    continue
                try:
                    v = float(text)
                except ValueError:
                    continue
                if unit == "%":
                    v = v / 100.0
                if v:
                    result[fn] = v
                else:
                    result.pop(fn, None)
        return result
