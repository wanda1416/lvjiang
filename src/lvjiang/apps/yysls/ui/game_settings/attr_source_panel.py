"""属性来源面板：填写装备之外的战斗属性从哪来。

补数据是这块的真正瓶颈——心法 37 门 × 6 重就是 222 行。所以界面按
「一组一屏」组织：左侧选心法/武学，右侧只显示这一组的几行，每行两次
点击填完：

- **一整条词条**：选词条类别即可，数值由 affix_caps 按当前等级生成，
  换赛季不用重填（心法给的整条词条按 1:2 拆成最小/最大）；
- **无贡献**：心法六重里大量是触发类效果，只改战斗行为不进面板。
  确认无贡献要能推进进度，否则永远有一堆查过、确认没有、却仍显示
  待填的条目；
- **自定义数值**：单个字段 + 数值，覆盖不符合整条规律的重数；
- **高级**：极少数一条给多个属性或需要公式的，直接编辑该条目的 YAML。

条目 id 形如「易水歌·二重」，按「·」前半分组。没有「·」的来源自成一组。
"""
from __future__ import annotations

from typing import Any

import yaml
from loguru import logger
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from lvjiang.ui.button_styles import (
    apply_button_style,
    apply_dialog_button_box_style,
)

from .....i18n import tr
from ...config import get_game_config
from ...core.attr_model import (
    DIMENSION_LABELS,
    PERCENT_FIELDS,
    SCOPE_COMBAT,
    SCOPE_PANEL,
    SOURCE_KIND_LABELS,
    SOURCE_KINDS,
    SUPPORTED_FULL_AFFIX_CATEGORIES,
    AttrModelError,
    Formula,
    get_attr_model_manager,
    invalidate_attr_model_cache,
)
from ...core.combat.combat_attrs import COMBAT_ATTR_FIELDS
from ..layout_helpers import configure_navigation_list

#: 取值方式
MODE_PENDING = "未填"
MODE_FULL_AFFIX = "一整条词条"
MODE_VALUE = "自定义数值"
MODE_NO_EFFECT = "无贡献"
MODE_ADVANCED = "高级（多属性/公式）"

_COLUMNS = (MODE_PENDING, MODE_FULL_AFFIX, MODE_VALUE, MODE_NO_EFFECT)

#: 分组分隔符，与条目 id 的约定一致
SEPARATOR = "·"


def _stat_choices() -> list[tuple[str, str]]:
    """(显示名, 字段名)：战斗属性 + 五维。五维是求值中间量，
    武学天赋那类「敏 → 外功攻击」的转换要用到。"""
    choices = [(display, name) for name, display, _unit, _ in COMBAT_ATTR_FIELDS]
    choices += [(f"五维·{label}", name) for name, label in DIMENSION_LABELS.items()]
    return choices


#: 切到「自定义数值」时的默认字段
_DEFAULT_STAT_FIELD = "min_outer"


def _to_internal(field_name: str, shown: float) -> float:
    """界面值 → 内部值。百分比字段界面填 4.6，内部存 0.046。"""
    return shown / 100.0 if field_name in PERCENT_FIELDS else shown


def _to_shown(field_name: str, internal: float) -> float:
    """内部值 → 界面值"""
    return internal * 100.0 if field_name in PERCENT_FIELDS else internal


def _first_stat(effect) -> tuple[str | None, float]:
    """条目的首个常数属性；没有或是公式时返回 (None, 0)"""
    for name, value in effect.stats.items():
        if not isinstance(value, Formula):
            return name, float(value)
    return None, 0.0


def _configure_spin(spin: QDoubleSpinBox, field_name: Any, internal: float) -> None:
    """按字段单位配置输入框：百分比带 % 后缀且按百分数显示

    没有这一步的话，用户照着游戏面板填 4.6，内部会当成 460% 使用。
    """
    name = str(field_name or "")
    percent = name in PERCENT_FIELDS
    spin.setSuffix(" %" if percent else "")
    spin.setDecimals(3 if percent else 5)
    spin.setRange(-100000.0, 100000.0)
    spin.setValue(_to_shown(name, internal))


class _AdvancedDialog(QDialog):
    """单个条目的 YAML 直编，给一条给多个属性或需要公式的少数情形。"""

    def __init__(self, source_id: str, payload: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("高级编辑 - {name}").format(name=source_id))
        self.resize(560, 420)
        layout = QVBoxLayout(self)
        hint = QLabel(tr(
            "直接编辑该条目的 YAML。可用键：stats、extra、full_affix、split、"
            "scope、no_effect、label"))
        hint.setWordWrap(True)
        hint.setStyleSheet("color: palette(mid); font-size: 11px;")
        layout.addWidget(hint)
        self._editor = QPlainTextEdit()
        self._editor.setPlainText(
            yaml.dump(payload or {}, allow_unicode=True, sort_keys=False))
        layout.addWidget(self._editor)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel)
        apply_dialog_button_box_style(buttons)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def payload(self) -> dict:
        data = yaml.safe_load(self._editor.toPlainText()) or {}
        if not isinstance(data, dict):
            raise AttrModelError(tr("条目内容必须是映射"))
        return data


class AttrSourcePanel(QWidget):
    """左侧按来源类别列出分组，右侧填写该组的各条目。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._loading = False
        self._rows: list[str] = []          # 当前右表每行对应的 source_id
        self._build_ui()
        self._reload()

    # ── 构建 ──

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        layout.addWidget(splitter)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)

        self._combo_kind = QComboBox()
        for kind in SOURCE_KINDS:
            self._combo_kind.addItem(tr(SOURCE_KIND_LABELS[kind]), kind)
        self._combo_kind.currentIndexChanged.connect(self._refresh_groups)
        left_layout.addWidget(self._combo_kind)

        self._search = QLineEdit()
        self._search.setPlaceholderText(tr("搜索"))
        self._search.textChanged.connect(self._refresh_groups)
        left_layout.addWidget(self._search)

        self._list = QListWidget()
        configure_navigation_list(self._list, minimum_width=200)
        self._list.currentTextChanged.connect(self._on_group_selected)
        left_layout.addWidget(self._list)

        btns = QHBoxLayout()
        self._btn_add = QPushButton(tr("+ 条目"))
        self._btn_add.clicked.connect(self._on_add)
        self._btn_del = QPushButton(tr("- 条目"))
        self._btn_del.clicked.connect(self._on_delete)
        apply_button_style(self._btn_add)
        apply_button_style(self._btn_del, variant="danger")
        btns.addWidget(self._btn_add)
        btns.addWidget(self._btn_del)
        left_layout.addLayout(btns)

        self._btn_derive = QPushButton(tr("推导基础属性…"))
        self._btn_derive.clicked.connect(self._on_derive)
        apply_button_style(self._btn_derive)
        left_layout.addWidget(self._btn_derive)
        splitter.addWidget(left)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)

        self._progress = QLabel()
        self._progress.setStyleSheet("color: palette(mid); font-size: 11px;")
        right_layout.addWidget(self._progress)

        self._table = QTableWidget(0, 4)
        self._table.setHorizontalHeaderLabels(
            [tr("条目"), tr("取值方式"), tr("取值"), tr("作用域")])
        header = self._table.horizontalHeader()
        if header is not None:
            header.setSectionResizeMode(
                0, QHeaderView.ResizeMode.ResizeToContents)
            header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        rows_header = self._table.verticalHeader()
        if rows_header is not None:
            rows_header.setVisible(False)
        right_layout.addWidget(self._table)

        hint = QLabel(tr(
            "「一整条词条」的数值由词组配置的当前等级上限生成，换赛季只改"
            "词组配置一处；一条给多个属性或需要公式的，用「高级」编辑"))
        hint.setWordWrap(True)
        hint.setStyleSheet("color: palette(mid); font-size: 11px;")
        right_layout.addWidget(hint)
        splitter.addWidget(right)

        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([220, 650])

    # ── 数据 ──

    def _manager(self):
        return get_attr_model_manager()

    def _reload(self) -> None:
        invalidate_attr_model_cache()
        errors = self._manager().errors()
        if errors:
            logger.error(f"属性来源加载有错: {errors}")
            QMessageBox.warning(
                self, tr("属性来源"),
                tr("以下文件解析失败，已跳过：{files}").format(
                    files="、".join(sorted(errors))),
            )
        self._select_default_kind()
        self._refresh_groups()

    def _select_default_kind(self) -> None:
        """默认落到第一个还有待填条目的类别。

        按 SOURCE_KINDS 声明序打开的话，第一个是几乎总为空的「等级底子」，
        每次进来都要先切一下才看得到要填的东西。
        """
        manager = self._manager()
        for index in range(self._combo_kind.count()):
            kind = self._combo_kind.itemData(index)
            done, total = manager.progress(kind)
            if total and done < total:
                self._combo_kind.setCurrentIndex(index)
                return
        for index in range(self._combo_kind.count()):
            if manager.progress(self._combo_kind.itemData(index))[1]:
                self._combo_kind.setCurrentIndex(index)
                return

    def _current_kind(self) -> str:
        return self._combo_kind.currentData() or SOURCE_KINDS[0]

    def _groups(self) -> dict[str, list[str]]:
        """分组名 → 该组的 source_id 列表，保持文件内顺序"""
        keyword = self._search.text().strip()
        groups: dict[str, list[str]] = {}
        for effect in self._manager().effects((self._current_kind(),)):
            group = effect.source_id.split(SEPARATOR)[0]
            if keyword and keyword not in effect.source_id:
                continue
            groups.setdefault(group, []).append(effect.source_id)
        return groups

    def _refresh_groups(self) -> None:
        current = self._list.currentItem()
        previous = current.text() if current is not None else ""
        self._loading = True
        self._list.clear()
        groups = self._groups()
        for group, ids in groups.items():
            done = sum(
                1 for source_id in ids
                if not self._effect(source_id).pending
            )
            item = QListWidgetItem(group)
            item.setData(Qt.ItemDataRole.UserRole, group)
            if done < len(ids):
                item.setToolTip(tr("待填 {n} 条").format(n=len(ids) - done))
            self._list.addItem(item)
        self._loading = False
        if previous and previous in groups:
            self._list.setCurrentRow(list(groups).index(previous))
        elif groups:
            self._list.setCurrentRow(0)
        else:
            self._fill_table([])
        self._refresh_progress()

    def _refresh_progress(self) -> None:
        kind = self._current_kind()
        done, total = self._manager().progress(kind)
        overall_done, overall_total = self._manager().progress()
        self._progress.setText(tr(
            "{label}：已确认 {done}/{total}　全部来源：{all_done}/{all_total}"
        ).format(
            label=tr(SOURCE_KIND_LABELS[kind]), done=done, total=total,
            all_done=overall_done, all_total=overall_total,
        ))

    def _effect(self, source_id: str):
        for effect in self._manager().effects():
            if effect.source_id == source_id:
                return effect
        raise AttrModelError(
            tr("未知的属性来源条目: {ids}").format(ids=source_id))

    def _on_group_selected(self, group: str) -> None:
        if self._loading:
            return
        self._fill_table(self._groups().get(group, []))

    # ── 右表 ──

    def _fill_table(self, source_ids: list[str]) -> None:
        self._loading = True
        self._rows = list(source_ids)
        self._table.setRowCount(len(source_ids))
        for row, source_id in enumerate(source_ids):
            effect = self._effect(source_id)
            tail = source_id.split(SEPARATOR, 1)
            label = tail[1] if len(tail) > 1 else source_id
            self._table.setItem(row, 0, QTableWidgetItem(label))

            mode = QComboBox()
            mode.addItems([tr(text) for text in _COLUMNS] + [tr(MODE_ADVANCED)])
            mode.setCurrentIndex(self._mode_index(effect))
            mode.currentIndexChanged.connect(
                lambda _index, r=row: self._on_mode_changed(r))
            self._table.setCellWidget(row, 1, mode)

            self._table.setCellWidget(row, 2, self._value_widget(row, effect))

            scope = QComboBox()
            scope.addItem(tr("进面板"), SCOPE_PANEL)
            scope.addItem(tr("仅战斗内"), SCOPE_COMBAT)
            scope.setCurrentIndex(0 if effect.scope == SCOPE_PANEL else 1)
            scope.currentIndexChanged.connect(
                lambda _index, r=row: self._commit(r))
            self._table.setCellWidget(row, 3, scope)
        self._loading = False

    def _mode_index(self, effect) -> int:
        if effect.no_effect:
            return _COLUMNS.index(MODE_NO_EFFECT)
        if effect.full_affix is not None:
            return _COLUMNS.index(MODE_FULL_AFFIX)
        if effect.extra or len(effect.stats) > 1 or any(
                isinstance(v, Formula) for v in effect.stats.values()):
            return len(_COLUMNS)      # 高级
        if effect.stats:
            return _COLUMNS.index(MODE_VALUE)
        return _COLUMNS.index(MODE_PENDING)

    def _value_widget(self, row: int, effect) -> QWidget:
        index = self._mode_index(effect)
        if index == _COLUMNS.index(MODE_FULL_AFFIX):
            combo = QComboBox()
            # 只列求值器真正支持的类别。列全部 15 类会造成
            # 「可选择、可保存、推导时才报错」，而一个条目报错整次求值全废。
            for category in SUPPORTED_FULL_AFFIX_CATEGORIES:
                combo.addItem(category)
            if effect.full_affix is not None:
                combo.setCurrentText(effect.full_affix.category)
            combo.currentIndexChanged.connect(
                lambda _i, r=row: self._commit(r))
            return combo
        if index == _COLUMNS.index(MODE_VALUE):
            holder = QWidget()
            box = QHBoxLayout(holder)
            box.setContentsMargins(0, 0, 0, 0)
            field = QComboBox()
            for display, name in _stat_choices():
                field.addItem(display, name)
            spin = QDoubleSpinBox()
            stored_name, stored_value = _first_stat(effect)
            if stored_name is not None:
                position = field.findData(stored_name)
                if position >= 0:
                    field.setCurrentIndex(position)
            _configure_spin(spin, field.currentData(), stored_value)
            field.currentIndexChanged.connect(lambda _i, r=row: self._commit(r))
            spin.valueChanged.connect(lambda _v, r=row: self._commit(r))
            box.addWidget(field)
            box.addWidget(spin)
            return holder
        if index == len(_COLUMNS):
            button = QPushButton(tr("编辑…"))
            apply_button_style(button)
            button.clicked.connect(lambda _checked, r=row: self._on_advanced(r))
            return button
        return QWidget()

    def _on_mode_changed(self, row: int) -> None:
        if self._loading:
            return
        mode = self._table.cellWidget(row, 1)
        if mode is not None and mode.currentIndex() == len(_COLUMNS):
            self._on_advanced(row)
            return
        self._commit(row, reset_value=True)

    def _payload(self, row: int, *, reset_value: bool) -> dict:
        mode = self._table.cellWidget(row, 1)
        index = mode.currentIndex() if mode is not None else 0
        scope_combo = self._table.cellWidget(row, 3)
        payload: dict = {}
        if scope_combo is not None and scope_combo.currentData() == SCOPE_COMBAT:
            payload["scope"] = SCOPE_COMBAT

        if index == _COLUMNS.index(MODE_NO_EFFECT):
            payload["no_effect"] = True
            return payload
        if index == _COLUMNS.index(MODE_PENDING):
            payload["modeled"] = False
            return payload

        value_widget = self._table.cellWidget(row, 2)
        if index == _COLUMNS.index(MODE_FULL_AFFIX):
            if reset_value or not isinstance(value_widget, QComboBox):
                categories = get_game_config().get_all_affix_categories()
                payload["full_affix"] = categories[0] if categories else ""
            else:
                payload["full_affix"] = value_widget.currentText()
            return payload

        # 切到「自定义数值」时给一个可编辑的默认值。此前这里回落到
        # modeled: false，界面立刻弹回「未填」，这个模式根本进不去。
        if reset_value or value_widget is None:
            payload["stats"] = {_DEFAULT_STAT_FIELD: 0.0}
            return payload
        field = value_widget.findChild(QComboBox)
        spin = value_widget.findChild(QDoubleSpinBox)
        if field is None or spin is None:
            payload["stats"] = {_DEFAULT_STAT_FIELD: 0.0}
            return payload
        name = str(field.currentData())
        payload["stats"] = {name: _to_internal(name, spin.value())}
        return payload

    def _commit(self, row: int, *, reset_value: bool = False) -> None:
        if self._loading or row >= len(self._rows):
            return
        source_id = self._rows[row]
        try:
            self._manager().save_entry(
                source_id, self._payload(row, reset_value=reset_value))
        except Exception as exc:
            logger.error(f"保存属性来源 {source_id} 失败: {exc}")
            QMessageBox.warning(self, tr("保存失败"), str(exc))
        self._refresh_current_group()

    def _refresh_current_group(self) -> None:
        item = self._list.currentItem()
        group = item.text() if item else ""
        self._fill_table(self._groups().get(group, []))
        self._refresh_progress()

    def _on_advanced(self, row: int) -> None:
        if row >= len(self._rows):
            return
        source_id = self._rows[row]
        dialog = _AdvancedDialog(source_id, self._manager().raw_entry(source_id), self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            self._refresh_current_group()
            return
        try:
            self._manager().save_entry(source_id, dialog.payload())
        except Exception as exc:
            logger.error(f"保存属性来源 {source_id} 失败: {exc}")
            QMessageBox.warning(self, tr("保存失败"), str(exc))
        self._refresh_current_group()

    def _on_derive(self) -> None:
        from .attr_derive_dialog import AttrDeriveDialog

        AttrDeriveDialog(self).exec()

    # ── 增删 ──

    def _on_add(self) -> None:
        kind = self._current_kind()
        name, ok = QInputDialog.getText(
            self, tr("新增条目"),
            tr("条目名（分组用「·」分隔，如 易水歌·二重）："))
        if not ok or not name.strip():
            return
        try:
            self._manager().create_entry(kind, name.strip())
        except Exception as exc:
            QMessageBox.warning(self, tr("新增失败"), str(exc))
            return
        self._refresh_groups()

    def _on_delete(self) -> None:
        item = self._list.currentItem()
        if item is None:
            return
        group = item.text()
        ids = self._groups().get(group, [])
        if not ids:
            return
        answer = QMessageBox.question(
            self, tr("删除条目"),
            tr("删除「{name}」的 {n} 个条目？").format(name=group, n=len(ids)),
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        for source_id in ids:
            try:
                self._manager().delete_entry(source_id)
            except Exception as exc:
                logger.error(f"删除属性来源 {source_id} 失败: {exc}")
        self._refresh_groups()
