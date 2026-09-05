"""基础属性推导面板：由属性来源算出装备之外的战斗属性。

和「创建基础属性」互补——那边是抄面板再反推，这边是正向推导，两者
应当得到同一个结果。所以对话框的重点不是算出一个数，而是**逐来源的
明细与差异**：面板对不上时，能直接看出是哪一个来源贡献错了，而不是
只知道总数不对。

界面按游戏里实际能装的东西组织：四个心法槽（每槽一门 + 重数，选第
N 重则一重至 N 重全部生效），套装/武备/神工/吃食等各选一项。两门
武学由流派的主副武学决定，不给选；五维转换恒生效。

**没有「全部来源」这个选项**——互斥来源一起相加必然是错的，而空装配
得到的零值一眼能看出没配，比一个似是而非的数安全。

来源没填完时，选中一套对照即可反解出「未建模补足」：已建模的走推导、
缺口由补足兜底，两者相加等于实测面板。补足可以选择一起保存，于是模型
建到一半也能产出可用的基础属性，不必等 222 条心法填完。

推导结果通过「存为基础属性」写进现有的基础属性存储，毕业率链路照旧
读它；同时把这次的装配记进 attr_derivations，事后能查回它是怎么来的，
数据更新后也能按同一份装配重推。
"""
from __future__ import annotations

from loguru import logger
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from lvjiang.ui.button_styles import apply_button_style

from .....i18n import tr
from ...config import (
    get_base_attr_profiles,
    get_derivation,
    get_game_config,
    get_loadout,
    save_derivation,
    save_loadout,
    save_play_style,
)
from ...core.attr_model import (
    COMBAT_NUMERIC_FIELDS,
    INNER_WAY_SLOTS,
    INNER_WAY_TIERS,
    SELECT_SINGLE,
    SELECTION_POLICIES,
    SOURCE_KIND_LABELS,
    AttrLoadout,
    AttrModelError,
    InnerWaySlot,
    diff_against_panel,
    get_attr_model_manager,
    invalidate_attr_model_cache,
)
from ...core.combat.combat_attrs import COMBAT_ATTR_FIELDS, CombatAttributes
from .level_combo import LevelCombo

#: 差异大于该值才算对不上。面板只显示到小数点后一位。
_DIFF_EPSILON = 0.05


#: 心法槽的空选项
_EMPTY = ""


class AttrDerivePanel(QWidget):
    """配装 → 推导 → 与实测面板比对 → 存为基础属性。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._loading = False
        self._slot_rows: list[tuple[QComboBox, QComboBox]] = []
        self._single_combos: dict[str, QComboBox] = {}
        self._build_ui()
        self._reload_school()

    # ── 构建 ──

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        top = QHBoxLayout()
        top.addWidget(QLabel(tr("流派")))
        self._combo_school = QComboBox()
        self._combo_school.addItems(list(get_game_config().get_schools()))
        self._combo_school.currentIndexChanged.connect(self._reload_school)
        top.addWidget(self._combo_school)

        top.addWidget(QLabel(tr("等级")))
        self._combo_level = LevelCombo()
        self._combo_level.currentIndexChanged.connect(self._on_changed)
        top.addWidget(self._combo_level)

        top.addWidget(QLabel(tr("对照基础属性")))
        self._combo_reference = QComboBox()
        self._combo_reference.currentIndexChanged.connect(self._on_reference_changed)
        top.addWidget(self._combo_reference)
        top.addStretch()
        layout.addLayout(top)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)

        self._martial_label = QLabel()
        self._martial_label.setWordWrap(True)
        self._martial_label.setStyleSheet("color: palette(mid); font-size: 11px;")
        left_layout.addWidget(self._martial_label)

        slots_box = QGroupBox(tr("心法（{n} 个槽）").format(n=INNER_WAY_SLOTS))
        slots_form = QFormLayout(slots_box)
        for index in range(INNER_WAY_SLOTS):
            name_combo = QComboBox()
            tier_combo = QComboBox()
            tier_combo.addItem(_EMPTY, 0)
            for tier, label in enumerate(INNER_WAY_TIERS, start=1):
                tier_combo.addItem(label, tier)
            name_combo.currentIndexChanged.connect(self._on_changed)
            tier_combo.currentIndexChanged.connect(self._on_changed)
            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.addWidget(name_combo, 3)
            row_layout.addWidget(tier_combo, 1)
            slots_form.addRow(tr("槽 {n}").format(n=index + 1), row)
            self._slot_rows.append((name_combo, tier_combo))
        left_layout.addWidget(slots_box)

        others_box = QGroupBox(tr("其他来源"))
        others_form = QFormLayout(others_box)
        for kind, policy in SELECTION_POLICIES.items():
            if policy != SELECT_SINGLE:
                continue
            combo = QComboBox()
            combo.currentIndexChanged.connect(self._on_changed)
            others_form.addRow(tr(SOURCE_KIND_LABELS[kind]), combo)
            self._single_combos[kind] = combo
        left_layout.addWidget(others_box)
        left_layout.addStretch()
        splitter.addWidget(left)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        self._summary = QLabel()
        self._summary.setWordWrap(True)
        self._summary.setStyleSheet("color: palette(mid); font-size: 11px;")
        right_layout.addWidget(self._summary)

        self._table = QTableWidget(0, 5)
        self._table.setHorizontalHeaderLabels([
            tr("属性"), tr("推导值"), tr("未建模补足"), tr("对照值"),
            tr("按来源拆分"),
        ])
        header = self._table.horizontalHeader()
        if header is not None:
            header.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        rows_header = self._table.verticalHeader()
        if rows_header is not None:
            rows_header.setVisible(False)
        right_layout.addWidget(self._table)
        splitter.addWidget(right)

        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([320, 660])
        layout.addWidget(splitter)

        buttons = QHBoxLayout()
        self._check_residual = QCheckBox(tr("保存时包含未建模补足"))
        self._check_residual.setChecked(True)
        self._check_residual.setToolTip(tr(
            "来源没填完时，缺口由对照面板反解补齐；不勾则只存纯模型值"))
        self._check_residual.stateChanged.connect(lambda _s: self._on_changed())
        buttons.addWidget(self._check_residual)
        buttons.addStretch()
        self._btn_save = QPushButton(tr("存为基础属性"))
        self._btn_save.clicked.connect(self._on_save)
        apply_button_style(self._btn_save)
        buttons.addWidget(self._btn_save)
        layout.addLayout(buttons)

    # ── 流派相关 ──

    def reload(self) -> None:
        """重读来源后重建界面。

        填数据与推导在同一个窗口里来回切，不重读的话刚填的值不会反映
        到推导结果上。
        """
        invalidate_attr_model_cache()
        self._reload_school()

    def _manager(self):
        return get_attr_model_manager()

    def _school(self) -> str:
        return self._combo_school.currentText()

    def _school_attr(self) -> str:
        return get_game_config().get_school_attr(self._school()) or "通用"

    def _martial_arts(self) -> tuple[str, ...]:
        """两门武学由流派的主副武学决定，不给用户选"""
        config = get_game_config().get_schools().get(self._school()) or {}
        names = [
            (config.get(side) or {}).get("martial_art")
            for side in ("main", "sub")
        ]
        return tuple(name for name in names if name)

    def _reload_school(self) -> None:
        self._loading = True
        arts = self._martial_arts()
        self._martial_label.setText(
            tr("武学：{arts}（由流派决定，不需选择）").format(
                arts=" + ".join(arts) if arts else tr("未配置"))
        )

        groups = sorted({
            effect.group for effect in self._manager().effects(("inner_way",))
            if effect.group
        })
        for name_combo, _tier in self._slot_rows:
            name_combo.clear()
            name_combo.addItem(_EMPTY, _EMPTY)
            for group in groups:
                name_combo.addItem(group, group)

        for kind, combo in self._single_combos.items():
            combo.clear()
            combo.addItem(tr("（不选）"), _EMPTY)
            for effect in self._manager().effects((kind,)):
                combo.addItem(effect.label, effect.source_id)

        self._refresh_reference()
        self._apply_loadout(AttrLoadout.from_dict(get_loadout(self._school())))
        self._loading = False
        self._on_changed()

    def _refresh_reference(self) -> None:
        self._combo_reference.blockSignals(True)
        self._combo_reference.clear()
        self._combo_reference.addItem(tr("（不对照）"), "")
        for name in get_base_attr_profiles(self._school()):
            self._combo_reference.addItem(name, name)
        self._combo_reference.blockSignals(False)

    # ── 装配状态 ──

    def _loadout(self) -> AttrLoadout:
        slots: list[InnerWaySlot] = []
        used: set[str] = set()
        for name_combo, tier_combo in self._slot_rows:
            name = name_combo.currentData() or ""
            tier = tier_combo.currentData() or 0
            # 同一门心法装在两个槽是无效配置，后一个槽忽略——否则
            # AttrLoadout 的重复校验会让整个界面停在报错上。
            if not name or not tier or name in used:
                continue
            used.add(name)
            slots.append(InnerWaySlot(name=name, tier=int(tier)))
        selections = {
            kind: combo.currentData()
            for kind, combo in self._single_combos.items()
            if combo.currentData()
        }
        return AttrLoadout(
            level=self._combo_level.get_level() or 0,
            school=self._school(),
            inner_ways=tuple(slots),
            selections=selections,
        )

    def _apply_loadout(self, loadout: AttrLoadout) -> None:
        if loadout.level:
            position = self._combo_level.findText(str(loadout.level))
            if position >= 0:
                self._combo_level.setCurrentIndex(position)
        for index, (name_combo, tier_combo) in enumerate(self._slot_rows):
            if index < len(loadout.inner_ways):
                slot = loadout.inner_ways[index]
                name_combo.setCurrentIndex(max(0, name_combo.findData(slot.name)))
                tier_combo.setCurrentIndex(max(0, tier_combo.findData(slot.tier)))
            else:
                name_combo.setCurrentIndex(0)
                tier_combo.setCurrentIndex(0)
        for kind, combo in self._single_combos.items():
            combo.setCurrentIndex(
                max(0, combo.findData(loadout.selections.get(kind, _EMPTY))))

    def _on_reference_changed(self) -> None:
        """选中一套基础属性时，把它当时的装配也调出来。

        没有记录的（多半是抄面板反推的）保持当前装配不动。
        """
        if self._loading:
            return
        name = self._combo_reference.currentData()
        stored = get_derivation(self._school(), name) if name else {}
        if stored:
            self._loading = True
            self._apply_loadout(AttrLoadout.from_dict(stored))
            self._loading = False
        self._on_changed()

    def _on_changed(self) -> None:
        if self._loading:
            return
        loadout = self._loadout()
        save_loadout(self._school(), loadout.to_dict())
        self._recompute(loadout)

    # ── 推导 ──

    def _reference_attrs(self) -> CombatAttributes | None:
        name = self._combo_reference.currentData()
        if not name:
            return None
        stored = get_base_attr_profiles(self._school()).get(name) or {}
        known = set(CombatAttributes.__dataclass_fields__)
        reference = CombatAttributes()
        for key, value in stored.items():
            if str(key) in known and isinstance(value, (int, float)):
                setattr(reference, str(key), float(value))
        # extra_attrs 是嵌套的一层；漏读它，指定武学增效这类动态属性
        # 就永远显示成「模型有、对照没有」。
        nested = stored.get("extra_attrs")
        if isinstance(nested, dict):
            reference.extra_attrs = {
                str(k): float(v) for k, v in nested.items()
                if isinstance(v, (int, float))
            }
        return reference

    def _resolve(self, loadout: AttrLoadout, *, residual=None):
        return self._manager().resolve_loadout(
            loadout,
            school_attr=self._school_attr(),
            martial_arts=self._martial_arts(),
            residual=residual,
        )

    def _residual(self, loadout: AttrLoadout, reference) -> dict[str, float]:
        """让面板属性等于对照所需的补足；没选对照就没有补足可算"""
        if reference is None:
            return {}
        targets = {
            name: float(getattr(reference, name, 0.0))
            for name in COMBAT_NUMERIC_FIELDS
        }
        return self._manager().solve_residual_for_loadout(
            loadout, targets,
            school_attr=self._school_attr(),
            martial_arts=self._martial_arts(),
        )

    def _recompute(self, loadout: AttrLoadout) -> None:
        if not loadout.level:
            return
        try:
            result = self._resolve(loadout)
        except AttrModelError as exc:
            self._summary.setText(tr("推导失败：{msg}").format(msg=str(exc)))
            self._table.setRowCount(0)
            return

        reference = self._reference_attrs()
        residual = self._residual(loadout, reference)
        differences = (
            diff_against_panel(result, reference) if reference is not None else {}
        )
        self._fill_table(result, reference, residual)

        # 只数真正有贡献的：空装配时五维转换也会记 6 条 0 值明细，
        # 报「参与推导 6 项」而表格却是空的，只会让人以为界面坏了。
        effective = sum(1 for m in result.panel.modifiers if m.delta)
        if not loadout.inner_ways and not loadout.selections:
            parts = [tr("尚未配装，先在左侧选心法与其他来源")]
        else:
            parts = [tr("参与推导 {n} 项").format(n=effective)]
        if residual:
            parts.append(tr("{n} 个属性靠补足").format(n=len(residual)))
        if result.unmodeled:
            parts.append(tr("其中 {n} 项尚未填数值").format(n=len(result.unmodeled)))
        if reference is None:
            parts.append(tr("未选对照，只显示推导值"))
        elif differences:
            parts.append(tr("与对照有 {n} 项不一致，看「按来源拆分」定位")
                         .format(n=len(differences)))
        else:
            parts.append(tr("与对照完全一致"))
        combat_only = len(result.combat.modifiers) - len(result.panel.modifiers)
        if combat_only > 0:
            parts.append(tr("另有 {n} 项仅战斗内生效，不进本表").format(n=combat_only))
        self._summary.setText("　".join(parts))

    def _rows(self, result, reference) -> list[tuple[str, str, bool]]:
        """(字段名, 显示名, 是否 extra)

        战斗属性一个不落地全列，取不到的显示 0——只列有值的行会让「这个
        属性模型不管」和「这个属性恰好是 0」看起来一模一样，而两者的处理
        完全相反：前者要去补来源，后者不用管。全 0 的行会调淡，不抢眼。

        extra_attrs 是动态字段（流派专属），没有固定清单，只能有才列。
        """
        rows: list[tuple[str, str, bool]] = [
            (name, display, False) for name, display, _unit, _ in COMBAT_ATTR_FIELDS
        ]
        extra_names = set(result.panel_attrs.extra_attrs)
        if reference is not None:
            extra_names |= set(reference.extra_attrs)
        rows.extend((name, name, True) for name in sorted(extra_names))
        return rows

    def _fill_table(self, result, reference, residual: dict[str, float]) -> None:
        # 一律走 result.panel：显示的是面板值，拆分就必须是面板明细。
        # 混用战斗明细的话，两栏加不到一起（吃食只在战斗侧有贡献）。
        panel = result.panel
        rows = self._rows(result, reference)
        self._table.setRowCount(len(rows))
        for row, (name, display, is_extra) in enumerate(rows):
            derived = (
                panel.attrs.extra_attrs.get(name, 0.0) if is_extra
                else getattr(panel.attrs, name, 0.0)
            )
            name_cell = QTableWidgetItem(display)
            self._table.setItem(row, 0, name_cell)
            self._table.setItem(row, 1, QTableWidgetItem(f"{derived:.4g}"))

            # 补足 = 对照 − 推导，即尚未建模的那部分
            gap = 0.0 if is_extra else residual.get(name, 0.0)
            self._table.setItem(
                row, 2, QTableWidgetItem(f"{gap:+.4g}" if gap else "-"))

            actual = 0.0
            if reference is None:
                self._table.setItem(row, 3, QTableWidgetItem("-"))
            else:
                actual = (
                    reference.extra_attrs.get(name, 0.0) if is_extra
                    else getattr(reference, name, 0.0)
                )
                cell = QTableWidgetItem(f"{actual:.4g}")
                # 补足之后仍对不上才算异常——补足本身就是为了抹平缺口
                if abs(actual - derived - gap) > _DIFF_EPSILON:
                    cell.setForeground(Qt.GlobalColor.red)
                self._table.setItem(row, 3, cell)

            breakdown = panel.contribution_by_kind(name)
            text = "　".join(
                f"{tr(SOURCE_KIND_LABELS.get(kind, kind))} {value:+.4g}"
                for kind, value in breakdown.items() if value
            )
            self._table.setItem(row, 4, QTableWidgetItem(text))

            # 推导、补足、对照三者皆无的行调淡。全列出来是为了让「模型
            # 不管这个属性」和「这个属性恰好是 0」能分辨，但四十多行里
            # 真正在动的常常只有十几行，不调淡就没法一眼扫过去。
            if not derived and not gap and not actual:
                for column in range(self._table.columnCount()):
                    item = self._table.item(row, column)
                    if item is not None:
                        item.setForeground(Qt.GlobalColor.gray)

    # ── 保存 ──

    def _on_save(self) -> None:
        loadout = self._loadout()
        if not loadout.level:
            return
        residual = (
            self._residual(loadout, self._reference_attrs())
            if self._check_residual.isChecked() else {}
        )
        try:
            result = self._resolve(loadout, residual=residual)
        except AttrModelError as exc:
            QMessageBox.warning(self, tr("推导失败"), str(exc))
            return

        name, ok = QInputDialog.getText(
            self, tr("存为基础属性"), tr("名称："),
            text=tr("{school} 推导").format(school=self._school()))
        if not ok or not name.strip():
            return
        # 存的是战斗属性全集：吃食一类只在战斗内生效的加成也要计入，
        # 毕业率算的是战斗内表现，而不是角色面板。
        try:
            save_play_style(self._school(), name.strip(),
                            result.combat_attrs.to_dict())
            # 同时记下这次的装配，事后能查回它是怎么来的
            save_derivation(self._school(), name.strip(), loadout.to_dict())
        except Exception as exc:
            logger.error(f"保存基础属性失败: {exc}")
            QMessageBox.warning(self, tr("保存失败"), str(exc))
            return
        self._refresh_reference()
        QMessageBox.information(
            self, tr("已保存"),
            tr("已存为基础属性「{name}」，毕业率可直接选用").format(
                name=name.strip()),
        )
