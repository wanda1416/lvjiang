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

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QFrame,
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
from lvjiang.apps.yysls.core.tuning_rules import (
    GENERIC_ATTR,
    QUALITY_PARTS,
    RATING_KEYS,
    RATING_LABELS,
    get_tune_config,
    standard_playstyle_attrs,
)
from lvjiang.core.config.resolver import LAYER_SYSTEM, EntityOrigin
from lvjiang.ui.button_styles import apply_button_style, apply_compact_tool_button_style
from lvjiang.ui.config_origin import layer_style, origin_tooltip
from lvjiang.ui.widgets import centered_cell_widget

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
                 protected: bool = False,
                 version_origin: EntityOrigin | None = None,
                 version_origins: tuple[EntityOrigin, ...] = (),
                 on_bump_version: Callable[[], int] | None = None,
                 parent=None):
        super().__init__(parent)
        self._data = data
        self._on_changed = on_changed
        self._on_delete = on_delete
        self._on_rename = on_rename
        self._on_enable_changed = on_enable_changed
        self._protected = protected
        self._version_origin = version_origin or EntityOrigin("", None)
        self._version_origins = version_origins or (
            (self._version_origin,) if self._version_origin.layer else ())
        self._on_bump_version = on_bump_version
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
        # 「重命名」改的就是左边这个 key，必须紧挨着它
        self._btn_rename_key = QPushButton(tr("重命名"))
        self._btn_rename_key.clicked.connect(self._rename_key)
        apply_button_style(self._btn_rename_key, variant="neutral")
        key_row.addWidget(self._btn_rename_key)

        # 版本号是另一码事（配置分发的代次，与 key 无关），拉开距离并用
        # 竖线隔断，否则挨着 key 摆容易被读成「key 的版本」
        key_row.addSpacing(28)
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.VLine)
        sep.setFrameShadow(QFrame.Shadow.Sunken)
        key_row.addWidget(sep)
        key_row.addSpacing(12)
        self._version_title = QLabel(tr("规则版本："))
        key_row.addWidget(self._version_title)
        self._version_label = QLabel()
        key_row.addWidget(self._version_label)
        self._btn_bump_version = QPushButton(tr("提升"))
        self._btn_bump_version.clicked.connect(self._bump_version)
        self._btn_bump_version.setEnabled(self._on_bump_version is not None)
        if self._on_bump_version is None:
            self._btn_bump_version.setToolTip(tr("仅开发模式可以提升系统配置版本"))
        apply_button_style(self._btn_bump_version, variant="neutral")
        key_row.addWidget(self._btn_bump_version)
        key_row.addStretch()
        # 删除入口收在首行最右，避免与表格编辑区混排误触
        self._btn_delete = QPushButton(tr("删除本规则"))
        apply_button_style(self._btn_delete, variant="danger")
        self._btn_delete.clicked.connect(self._confirm_delete)
        key_row.addWidget(self._btn_delete)
        if self._protected:
            hint = tr("系统规则不可删除或修改 key；不使用时请取消启用")
            self._btn_rename_key.setEnabled(False)
            self._btn_rename_key.setToolTip(hint)
            self._btn_delete.setEnabled(False)
            self._btn_delete.setToolTip(hint)
        self._key_row = key_row     # 排布有讲究（见上），留给测试盯住
        form.addRow(tr("标识 key："), key_row)
        self._set_version_display(
            self._version_origin.version, self._version_origin.layer)

        # 名称只读展示（修改走对话框左侧导航双击）
        name_row = QHBoxLayout()
        self._name_label = QLabel()
        name_row.addWidget(self._name_label)
        hint = QLabel(tr("（双击左侧导航中的规则名可修改）"))
        hint.setStyleSheet("color: palette(mid);")
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
        self._playstyle_tips_btn.setFixedSize(20, 20)
        apply_compact_tool_button_style(self._playstyle_tips_btn)
        self._playstyle_tips_btn.clicked.connect(self._show_playstyle_tips)
        title_row.addWidget(self._playstyle_tips_btn)
        title_row.addStretch()
        layout.addLayout(title_row)
        # 玩法定义已提到公共的「玩法配置」，规则只勾选引用哪些。
        # 以前每个规则各自内嵌一份定义，同一个「纯唐」在多个规则文件里重复
        # （实测 14 个玩法跨文件零差异），玩法因此没有唯一归属。
        # 只列本规则**已引用**的玩法。把全部玩法铺出来让人勾选，规则一多就
        # 分不清哪些是这条规则真正在用的。
        self._playstyle_table = QTableWidget(0, 6)
        self._playstyle_table.setHorizontalHeaderLabels(
            [tr("名字"), tr("主武器"), tr("主增伤词条"), tr("副武器"),
             tr("属性"), tr("绑定开关")])
        for col, width in enumerate((90, 90, 150, 90, 90, 120)):
            self._playstyle_table.setColumnWidth(col, width)
        # 定义列只读（改定义去玩法配置），只有绑定开关是本规则自己的事
        self._playstyle_table.setEditTriggers(
            QTableWidget.EditTrigger.NoEditTriggers)
        self._fix_table_height(self._playstyle_table, 10)
        layout.addWidget(self._playstyle_table)
        hint = QLabel(tr("玩法定义在「游戏配置 → 玩法配置」中维护，此处只引用"))
        hint.setStyleSheet("color: palette(mid); font-size: 11px;")
        layout.addWidget(hint)
        ps_btns = QHBoxLayout()
        self._btn_add_playstyle = QPushButton(tr("新增玩法"))
        self._btn_add_playstyle.clicked.connect(self._on_add_playstyle_ref)
        ps_btns.addWidget(self._btn_add_playstyle)
        self._btn_del_playstyle = QPushButton(tr("删除玩法"))
        self._btn_del_playstyle.clicked.connect(self._on_del_playstyle_ref)
        ps_btns.addWidget(self._btn_del_playstyle)
        apply_button_style(self._btn_add_playstyle)
        apply_button_style(self._btn_del_playstyle, variant="danger")
        ps_btns.addStretch()
        layout.addLayout(ps_btns)

        # ── 品阶门槛覆盖（只列出需覆盖全局默认的部位）──
        q_title = QHBoxLayout()
        q_title.addWidget(QLabel("<b>" + tr("品阶门槛（覆盖）") + "</b>"))
        q_hint = QLabel(tr("（仅列出的部位覆盖全局默认，未列部位沿用基础配置）"))
        q_hint.setStyleSheet("color: palette(mid);")
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
        apply_button_style(btn_add)
        apply_button_style(btn_del, variant="danger")

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
        apply_button_style(btn_add)
        apply_button_style(btn_del, variant="danger")

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

        referenced = list(d.get("playstyles") or [])
        switches = dict(d.get("playstyle_switches") or {})
        registry = get_game_config().get_playstyles()
        try:
            switch_keys = [""] + list(get_tune_config().switches)
        except Exception:  # noqa: BLE001 — 开关注册表异常不该让规则页打不开
            switch_keys = [""]
        self._playstyle_table.blockSignals(True)
        self._playstyle_table.setRowCount(0)
        for name in referenced:
            cfg = registry.get(name) or {}
            row = self._playstyle_table.rowCount()
            self._playstyle_table.insertRow(row)
            for col, text in enumerate((
                name, cfg.get("main_weapon", ""), cfg.get("main_damage", ""),
                cfg.get("sub_weapon", ""), cfg.get("attr", ""),
            )):
                item = QTableWidgetItem(str(text))
                if col:
                    item.setForeground(Qt.GlobalColor.gray)   # 定义只读
                self._playstyle_table.setItem(row, col, item)
            # 绑定开关属于本规则：开关控制的是非武器增伤这类判定口径，同一个
            # 玩法在不同规则下可以绑不同开关，甚至不绑。
            combo = QComboBox()
            combo.addItems(switch_keys)
            combo.setCurrentText(str(switches.get(name) or ""))
            combo.currentTextChanged.connect(
                lambda _t: self._apply_playstyles())
            self._playstyle_table.setCellWidget(row, 5, combo)
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

    def _set_version_display(self, version: int | None, layer: str) -> None:
        self._version_label.setText("-" if version is None else f"v{version}")
        self._version_label.setStyleSheet(layer_style(layer))
        # 标题、数值、按钮都挂上说明：只给数值挂，用户悬停在「规则版本：」
        # 上什么也看不到，等于没做
        current = EntityOrigin(layer, version)
        tip = origin_tooltip(current, self._version_origins)
        for widget in (self._version_title, self._version_label,
                       self._btn_bump_version):
            if widget is self._btn_bump_version and not widget.isEnabled():
                continue                      # 保留「仅开发模式可提升」那条
            widget.setToolTip(tip)

    def _bump_version(self) -> None:
        if self._on_bump_version is None:
            return
        try:
            version = self._on_bump_version()
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, tr("版本提升失败"), str(e))
            return
        self._version_origins = tuple(
            EntityOrigin(LAYER_SYSTEM, version)
            if origin.layer == LAYER_SYSTEM else origin
            for origin in self._version_origins
        )
        if not any(origin.layer == LAYER_SYSTEM
                   for origin in self._version_origins):
            self._version_origins += (EntityOrigin(LAYER_SYSTEM, version),)
        self._set_version_display(version, LAYER_SYSTEM)

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
        # 只写引用名 + 本规则的开关绑定；玩法定义在公共配置里
        names: list[str] = []
        switches: dict[str, str] = {}
        for i in range(self._playstyle_table.rowCount()):
            name = self._cell(self._playstyle_table, i, 0)
            if not name:
                continue
            names.append(name)
            combo = self._playstyle_table.cellWidget(i, 5)
            bound = combo.currentText().strip() if combo else ""
            if bound:
                switches[name] = bound
        if names:
            self._data["playstyles"] = names
        else:
            self._data.pop("playstyles", None)
        if switches:
            self._data["playstyle_switches"] = switches
        else:
            self._data.pop("playstyle_switches", None)
        self._on_changed()

    def _on_add_playstyle_ref(self):
        """从公共玩法里挑一个加进本规则的引用。"""
        referenced = set(self._data.get("playstyles") or [])
        available = sorted(
            n for n in get_game_config().get_playstyles() if n not in referenced)
        if not available:
            QMessageBox.information(
                self, tr("新增玩法"),
                tr("所有玩法都已引用；要新建玩法请到「游戏配置 → 玩法配置」"))
            return
        name, ok = QInputDialog.getItem(
            self, tr("新增玩法"), tr("选择要引用的玩法:"), available, 0, False)
        if not ok or not name:
            return
        self._data["playstyles"] = list(
            self._data.get("playstyles") or []) + [name]
        self._load()
        self._apply_playstyles()

    def _on_del_playstyle_ref(self):
        row = self._playstyle_table.currentRow()
        if row < 0:
            return
        name = self._cell(self._playstyle_table, row, 0)
        if not name:
            return
        self._data["playstyles"] = [
            n for n in (self._data.get("playstyles") or []) if n != name]
        (self._data.get("playstyle_switches") or {}).pop(name, None)
        self._load()
        self._apply_playstyles()

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
