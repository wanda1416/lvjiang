"""规则设置页（规则级字段）

key 与名称均为只读文本展示：key 重命名走展示区后的「重命名」按钮
（弹窗提醒会修改 id，可能影响直接引用的代码）；名称修改走
对话框左侧导航双击。playstyles 玩法设定表（名字/主武器/主增伤
词条/副武器/副增伤词条/属性，增伤留空 = 不需要增伤，主/副增伤
的留空项均以「- 无需增伤 -」展示，说明文案收入标题旁「?」按钮点击
展示），及「删除本规则」
入口。武器与增伤词条为下拉选择，候选来自游戏配置数据源
（GameConfigManager：weapon_types 注册表 / 指定武学增效词条），
不开放手写；名字列为文本格，直接对应 YAML playstyles 节字段；
属性列候选 = 属性攻击词组组名（通用/鸣金/牵丝/裂石/破竹）。
增伤词条列候选随同侧武器收窄：武器已绑定武学增效词条
（weapon_types.wuxue_affix）时仅保留留空 + 该绑定词条。
另含品阶门槛覆盖表：按部位覆盖全局 tune_config 默认，
未列部位沿用基础配置（如 小外流佩/会意环 金紫皆可）。
"""

from __future__ import annotations

import re
from typing import Callable

from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from lvjiang.apps.yysls.config import get_game_config
from lvjiang.apps.yysls.evaluator.tuning_rules import (
    GENERIC_ATTR,
    QUALITY_PARTS,
    RATING_KEYS,
    RATING_LABELS,
    get_tune_config,
    standard_playstyle_attrs,
)

from .....i18n import tr

# 规则 key 约束（作文件名，与 rules._KEY_RE 一致）
_KEY_RE = re.compile(r"^[a-z][a-z0-9_]*$")

# 品阶枚举（与 tune_config 一致）
_QUALITIES = ("gold", "purple", "blue")

# 增伤列留空项的展示文案（仅显示层，收集时仍写入空 = null）
_NO_DAMAGE_LABEL = tr("- 无需增伤 -")

# 玩法设定表说明（「?」按钮点击展示，不用悬停 tooltip）
_PLAYSTYLE_TIPS = (
    "名字 = 调律 Tab 勾选项；\n"
    "增伤词条留空/选「- 无需增伤 -」= 该侧不需要增伤；\n"
    "属性 = 玩法属攻流派（非武器部位据此做属攻→无相等价）；\n"
    "绑定开关 = 该玩法判定时等价于激活该开关（覆盖全局状态）；\n"
    "武器/词条候选来自游戏配置，增伤候选随同侧武器绑定收窄。")  # runtime tr()


class RuleSettingsPage(QWidget):
    """规则级设置页（编辑共享 raw dict，变更即回调保存）"""

    def __init__(self, data: dict, on_changed: Callable[[], None],
                 on_delete: Callable[[], None] | None = None,
                 on_rename: Callable[[str, str, str], None] | None = None,
                 on_enable_changed: Callable[[bool], None] | None = None,
                 parent=None):
        super().__init__(parent)
        self._data = data
        self._on_changed = on_changed
        self._on_delete = on_delete
        self._on_rename = on_rename
        self._on_enable_changed = on_enable_changed
        self._loading = True
        # 武器/增伤词条候选来自游戏配置数据源（保存时已刷新单例）
        mgr = get_game_config()
        weapons = mgr.get_weapon_types()
        wuxue = mgr.get_wuxue_affix_names()
        attrs = standard_playstyle_attrs()
        # 绑定开关候选来自开关注册表
        try:
            switch_keys = list(get_tune_config().switches)
        except Exception:  # noqa: BLE001
            switch_keys = []
        self._col_candidates: dict[int, list[str]] = {
            1: weapons, 2: wuxue, 3: weapons, 4: wuxue, 5: attrs,
            6: switch_keys}
        # 武器 → 绑定武学增效词条（增伤列候选据此收窄）
        self._weapon_affixes = mgr.get_all_weapon_wuxue_affixes()
        self._init_ui()
        self._load()
        self._loading = False

    def _init_ui(self):
        layout = QVBoxLayout(self)

        # ── 启用该规则（顶部第一行）──
        self._enable_cb = QCheckBox(tr("启用该规则"))
        self._enable_cb.setChecked(True)  # 默认勾选
        self._enable_cb.stateChanged.connect(self._on_enable_cb_changed)
        self._enable_cb.setStyleSheet("font-weight: bold;")
        layout.addWidget(self._enable_cb)

        form = QFormLayout()
        # key 只读展示 + 「重命名」按钮（弹窗提醒 id 变更影响）
        key_row = QHBoxLayout()
        self._key_label = QLabel()
        key_row.addWidget(self._key_label)
        btn_rename_key = QPushButton(tr("重命名"))
        btn_rename_key.clicked.connect(self._rename_key)
        key_row.addWidget(btn_rename_key)
        key_row.addStretch()
        # 删除入口收在首行最右，避免与表格编辑区混排误触
        btn_delete = QPushButton(tr("删除本规则"))
        btn_delete.setStyleSheet("color: #c62828;")
        btn_delete.clicked.connect(self._confirm_delete)
        key_row.addWidget(btn_delete)
        form.addRow(tr("标识 key："), key_row)

        # 名称只读展示（修改走对话框左侧导航双击）
        name_row = QHBoxLayout()
        self._name_label = QLabel()
        name_row.addWidget(self._name_label)
        hint = QLabel(tr("（双击左侧导航中的规则名可修改）"))
        hint.setStyleSheet("color: #888;")
        name_row.addWidget(hint)
        name_row.addStretch()
        form.addRow(tr("规则名称："), name_row)

        # 默认判定：四档条件全不命中时的兜底档位
        self._default_rating_combo = QComboBox()
        for rating_key in RATING_KEYS:
            self._default_rating_combo.addItem(
                RATING_LABELS[rating_key], rating_key)
        self._default_rating_combo.currentIndexChanged.connect(
            self._apply_default_rating)
        form.addRow(tr("默认判定："), self._default_rating_combo)
        layout.addLayout(form)

        # ── playstyles 玩法设定（说明收入「?」按钮，点击展示）──
        title_row = QHBoxLayout()
        title_row.addWidget(QLabel("<b>" + tr("玩法设定") + "</b>"))
        self._playstyle_tips_btn = QToolButton()
        self._playstyle_tips_btn.setText("?")
        self._playstyle_tips_btn.clicked.connect(self._show_playstyle_tips)
        title_row.addWidget(self._playstyle_tips_btn)
        title_row.addStretch()
        layout.addLayout(title_row)
        self._playstyle_table = QTableWidget(0, 7)
        self._playstyle_table.setHorizontalHeaderLabels(
            [tr("名字"), tr("主武器"), tr("主增伤词条"), tr("副武器"), tr("副增伤词条"), tr("属性"),
             tr("绑定开关")])
        # 增伤词条列比武器列更宽；不拉伸末列，列宽固定、列表偏左，
        # 未占满 dialog 时右侧留白（避免末列被拉升占满全宽）
        for col, width in enumerate((80, 90, 150, 90, 150, 90, 120)):
            self._playstyle_table.setColumnWidth(col, width)
        self._playstyle_table.cellChanged.connect(self._apply_playstyles)
        self._fix_table_height(self._playstyle_table, 10)
        layout.addWidget(self._playstyle_table)
        layout.addLayout(
            self._table_buttons(self._playstyle_table, self._apply_playstyles))

        # ── 品阶门槛覆盖（只列出需覆盖全局默认的部位）──
        q_title = QHBoxLayout()
        q_title.addWidget(QLabel("<b>" + tr("品阶门槛（覆盖）") + "</b>"))
        q_hint = QLabel(tr("（仅列出的部位覆盖全局默认，未列部位沿用基础配置）"))
        q_hint.setStyleSheet("color: #888;")
        q_title.addWidget(q_hint)
        q_title.addStretch()
        layout.addLayout(q_title)
        self._quality_table = QTableWidget(0, 4)
        self._quality_table.setHorizontalHeaderLabels(
            [tr("部位"), "gold", "purple", "blue"])
        for col, width in enumerate((120, 70, 70, 70)):
            self._quality_table.setColumnWidth(col, width)
        self._fix_table_height(self._quality_table, 3)
        layout.addWidget(self._quality_table)
        layout.addLayout(self._quality_table_buttons())
        layout.addStretch()

    @staticmethod
    def _fix_table_height(table: QTableWidget, rows: int):
        """表格默认展示区固定为 rows 行高（超出行数走滚动条）"""
        hheader = table.horizontalHeader()
        assert hheader is not None
        header_h = hheader.sizeHint().height()
        vheader = table.verticalHeader()
        assert vheader is not None
        row_h = vheader.defaultSectionSize()
        table.setFixedHeight(
            header_h + row_h * rows + 2 * table.frameWidth())

    def _table_buttons(self, table: QTableWidget, apply) -> QHBoxLayout:
        row = QHBoxLayout()
        btn_add = QPushButton(tr("添加行"))
        btn_add.clicked.connect(
            lambda: self._insert_playstyle_row(table.rowCount()))
        btn_del = QPushButton(tr("删除选中行"))

        def _delete():
            r = table.currentRow()
            if r >= 0:
                table.removeRow(r)
                apply()

        btn_del.clicked.connect(_delete)
        row.addWidget(btn_add)
        row.addWidget(btn_del)
        row.addStretch()
        return row

    # ── 品阶门槛覆盖表 ──

    def _quality_table_buttons(self) -> QHBoxLayout:
        row = QHBoxLayout()
        btn_add = QPushButton(tr("添加行"))
        btn_add.clicked.connect(
            lambda: (self._insert_quality_row(self._quality_table.rowCount()),
                     self._apply_quality()))
        btn_del = QPushButton(tr("删除选中行"))

        def _delete():
            r = self._quality_table.currentRow()
            if r >= 0:
                self._quality_table.removeRow(r)
                self._apply_quality()

        btn_del.clicked.connect(_delete)
        row.addWidget(btn_add)
        row.addWidget(btn_del)
        row.addStretch()
        return row

    def _insert_quality_row(self, row: int, part: str = "",
                            qualities: set[str] | None = None):
        qualities = qualities or set()
        table = self._quality_table
        table.insertRow(row)
        combo = QComboBox()
        combo.addItems(list(QUALITY_PARTS))
        combo.setCurrentText(part or QUALITY_PARTS[0])
        combo.currentTextChanged.connect(lambda _t: self._apply_quality())
        table.setCellWidget(row, 0, combo)
        for i, q in enumerate(_QUALITIES, start=1):
            cb = QCheckBox()
            cb.setChecked(q in qualities)
            cb.stateChanged.connect(lambda _s: self._apply_quality())
            table.setCellWidget(row, i, cb)

    def _apply_quality(self):
        if self._loading:
            return
        table = self._quality_table
        thresholds: dict[str, list[str]] = {}
        for r in range(table.rowCount()):
            combo = table.cellWidget(r, 0)
            part = combo.currentText().strip() if combo else ""
            if not part:
                continue
            chosen = [q for i, q in enumerate(_QUALITIES, start=1)
                      if (cb := table.cellWidget(r, i)) and cb.isChecked()]
            thresholds[part] = chosen
        if thresholds:
            self._data["quality_thresholds"] = thresholds
        else:
            self._data.pop("quality_thresholds", None)
        self._on_changed()

    # ── 玩法设定表行构建（名字为文本格，其余列为下拉格）──

    def _insert_playstyle_row(self, row: int,
                              values: tuple = (
                                  "", "", "", "", "", GENERIC_ATTR, "")):
        table = self._playstyle_table
        table.insertRow(row)
        table.setItem(row, 0, QTableWidgetItem(values[0]))
        for col in range(1, 7):
            # 增伤列（col 2/4）候选随同侧武器（col 1/3）绑定收窄
            candidates = (self._damage_candidates(values[col - 1])
                          if col in (2, 4) else None)
            table.setCellWidget(
                row, col, self._make_cell_combo(col, values[col], candidates))

    def _damage_candidates(self, weapon: str) -> list[str]:
        """增伤词条候选：武器已绑定武学增效词条时仅保留该词条，
        未选武器/未绑定时回退全量候选"""
        bound = self._weapon_affixes.get(weapon.strip()) if weapon else ""
        return [bound] if bound else self._col_candidates[2]

    def _make_cell_combo(self, col: int, value: str,
                         candidates: list[str] | None = None,
                         ) -> QComboBox:
        """单元格下拉框：候选来自游戏配置数据源；
        失效旧值保留展示便于改正"""
        combo = QComboBox()
        if col in (2, 4):  # 增伤留空项以占位文案展示（收集时仍写入空）
            combo.addItem(_NO_DAMAGE_LABEL)
        elif col == 6:  # 绑定开关列：留空 = 不绑定
            combo.addItem("")
        elif col != 5:  # 属性列必选（默认通用），无留空项
            combo.addItem("")  # 留空 = 未配置
        if candidates is None:
            candidates = self._col_candidates[col]
        combo.addItems(candidates)
        if value and value not in candidates:
            combo.addItem(value)
        if col == 6:
            combo.setCurrentText(value or "")
        elif col == 5:
            combo.setCurrentText(value or GENERIC_ATTR)
        elif col in (2, 4):
            combo.setCurrentText(value or _NO_DAMAGE_LABEL)
        else:
            combo.setCurrentText(value)
        combo.currentTextChanged.connect(
            lambda _text: self._on_combo_changed(combo, col))
        return combo

    def _on_combo_changed(self, combo: QComboBox, col: int):
        # 武器列变更时先收窄同侧增伤列候选，再统一收集保存
        if col in (1, 3):
            self._sync_damage_candidates(combo, col)
        self._apply_playstyles()

    def _sync_damage_candidates(self, weapon_combo: QComboBox,
                                weapon_col: int):
        """重建同侧增伤列候选；现值不在新候选内则重置为留空"""
        row = self._find_widget_row(weapon_combo, weapon_col)
        if row < 0:
            return
        dmg_col = weapon_col + 1
        dmg = self._playstyle_table.cellWidget(row, dmg_col)
        if dmg is None:
            return
        current = dmg.currentText()
        dmg.blockSignals(True)
        dmg.clear()
        dmg.addItem(_NO_DAMAGE_LABEL)
        dmg.addItems(
            self._damage_candidates(weapon_combo.currentText()))
        if dmg.findText(current) >= 0:
            dmg.setCurrentText(current)
        else:
            dmg.setCurrentIndex(0)
        dmg.blockSignals(False)

    def _find_widget_row(self, widget: QWidget, col: int) -> int:
        table = self._playstyle_table
        for r in range(table.rowCount()):
            if table.cellWidget(r, col) is widget:
                return r
        return -1

    def _combo_text(self, row: int, col: int) -> str:
        widget = self._playstyle_table.cellWidget(row, col)
        text = widget.currentText().strip() if widget else ""
        return "" if text == _NO_DAMAGE_LABEL else text

    # ── 回填 ──

    def _load(self):
        d = self._data
        self._key_label.setText(str(d.get("key", "")))
        self._name_label.setText(str(d.get("name", "")))

        rating = str(d.get("default_rating", "excellent"))
        idx = self._default_rating_combo.findData(rating)
        self._default_rating_combo.blockSignals(True)
        self._default_rating_combo.setCurrentIndex(max(idx, 0))
        self._default_rating_combo.blockSignals(False)

        rules = d.get("playstyles") or {}
        self._playstyle_table.blockSignals(True)
        self._playstyle_table.setRowCount(0)
        for name, raw in rules.items():
            raw = raw or {}
            main = raw.get("main") or {}
            sub = raw.get("sub") or {}
            self._insert_playstyle_row(self._playstyle_table.rowCount(), (
                name,
                str(main.get("weapon") or ""),
                str(main.get("damage") or ""),
                str(sub.get("weapon") or ""),
                str(sub.get("damage") or ""),
                str(raw.get("attr") or GENERIC_ATTR),
                str(raw.get("switch") or "")))
        self._playstyle_table.blockSignals(False)

        thresholds = d.get("quality_thresholds") or {}
        self._quality_table.setRowCount(0)
        for part in QUALITY_PARTS:
            if part in thresholds:
                self._insert_quality_row(
                    self._quality_table.rowCount(), part,
                    set(thresholds.get(part) or []))

    def set_enabled(self, enabled: bool):
        """回填启用状态（不触发回调）"""
        self._enable_cb.blockSignals(True)
        self._enable_cb.setChecked(enabled)
        self._enable_cb.blockSignals(False)

    def _on_enable_cb_changed(self, _state: int):
        """启用复选框变更 → 回调通知管理器"""
        if self._loading:
            return
        if self._on_enable_changed is not None:
            self._on_enable_changed(self._enable_cb.isChecked())

    # ── 收集（写回共享 dict） ──

    def set_name(self, name: str):
        """名称变更后同步只读展示（数据由面板写入，见导航双击重命名）"""
        self._name_label.setText(name)

    def _show_playstyle_tips(self):
        btn = self._playstyle_tips_btn
        QToolTip.showText(
            btn.mapToGlobal(btn.rect().bottomRight()), tr(_PLAYSTYLE_TIPS), btn)

    def _rename_key(self):
        old_key = str(self._data.get("key", ""))
        new_key, ok = QInputDialog.getText(
            self, tr("重命名规则 key"),
            tr("重命名会修改规则 id（YAML 文件名），\n"
               "可能影响直接引用该 key 的代码/工作流配置，请确认。\n\n"
               "新 key（小写字母开头的英文/数字/下划线）："),
            text=old_key)
        if not ok:
            return
        new_key = new_key.strip()
        if not new_key or new_key == old_key:
            return
        if not _KEY_RE.match(new_key):
            QMessageBox.warning(
                self, tr("重命名规则 key"),
                tr("标识 key 须为小写字母开头的英文/数字/下划线"))
            return
        # 重命名文件并通知面板/对话框更新导航（失败已由状态栏提示）
        if self._on_rename is not None:
            try:
                self._on_rename(
                    old_key, new_key, str(self._data.get("name", "")))
            except Exception:  # noqa: BLE001
                return
        self._data["key"] = new_key
        self._key_label.setText(new_key)
        self._on_changed()

    def _apply_default_rating(self, _index: int):
        if self._loading:
            return
        self._data["default_rating"] = self._default_rating_combo.currentData()
        self._on_changed()

    def _apply_playstyles(self):
        if self._loading:
            return
        rules: dict = {}
        for i in range(self._playstyle_table.rowCount()):
            name = self._cell(self._playstyle_table, i, 0)
            if not name:
                continue
            rules[name] = {
                "main": {
                    "weapon": self._combo_text(i, 1),
                    "damage": self._combo_text(i, 2) or None,
                },
                "sub": {
                    "weapon": self._combo_text(i, 3),
                    "damage": self._combo_text(i, 4) or None,
                },
                "attr": self._combo_text(i, 5) or GENERIC_ATTR,
            }
            # 绑定开关（留空 = 不绑定，不写入字段）
            sw = self._combo_text(i, 6)
            if sw:
                rules[name]["switch"] = sw
        if rules:
            self._data["playstyles"] = rules
        else:
            self._data.pop("playstyles", None)
        self._on_changed()

    @staticmethod
    def _cell(table: QTableWidget, row: int, col: int) -> str:
        item = table.item(row, col)
        return item.text().strip() if item else ""

    # ── 删除 ──

    def _confirm_delete(self):
        if self._on_delete is None:
            return
        name = str(self._data.get("name") or self._data.get("key") or "")
        ret = QMessageBox.question(
            self, tr("删除规则"),
            tr("确定删除调律规则「{name}」？规则文件将被删除，不可恢复。").format(name=name))
        if ret == QMessageBox.StandardButton.Yes:
            self._on_delete()
