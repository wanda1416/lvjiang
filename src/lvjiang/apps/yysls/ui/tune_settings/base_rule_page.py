"""基础规则组管理页（base_groups/ 目录）

规则组 CRUD 与切换入口：
- 当前规则下拉：切换编辑目标，并通知三个行为页重载；
- 规则组列表：规则组名 / 等级门槛 / 调律门槛 / 规则说明 概览；
- 新增（key+名称对话框，key 走 _KEY_RE，空白组）/
  复制（需选中，独立副本）/ 删除（需选中，default 禁删）；
- 等级门槛（min_level）与调律门槛（scan.entry_min_rating）
  编辑当前组，沿用「变更即校验即暂存」模式：控件变更即重建
  raw dict → 校验 → 通过才写盘并 reload，失败时状态栏红字提示。
  `_build()` 以管理器最新 raw 为底、只替换本页负责的键，
  与行为三页/材料配置页各管各段互不覆盖。
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Callable

from loguru import logger
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from lvjiang.apps.yysls.core.tuning_rules import (
    RATING_LABELS,
    RuleValidationError,
    TuningGroupManager,
)
from lvjiang.apps.yysls.ui.layout_helpers import fit_combo_to_contents
from lvjiang.ui.button_styles import (
    apply_button_style,
    apply_dialog_button_box_style,
)

from .....i18n import tr

# 规则组 key 约束（作文件名，与 rules._KEY_RE 一致）
_KEY_RE = re.compile(r"^[a-z][a-z0-9_]*$")
#: 开发者可选的保存位置（层 key → 显示名）
_LAYER_CHOICES = (("system", "系统"), ("local", "本地"))
_LAYER_LABELS = {"system": "系统", "local": "本地", "remote": "远程"}
#: 表格列：规则组名 / 等级门槛 / 调律门槛 / 规则说明 / 保存位置（仅开发模式）
_COL_DESC = 3
_COL_LAYER = 4


class _NewGroupDialog(QDialog):
    """新增/复制规则组对话框：输入 key（英文标识，作文件名）与名称"""

    def __init__(self, title: str, parent=None, *, choose_layer: bool = False):
        super().__init__(parent)
        self.setWindowTitle(title)
        self._title = title
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self._key_edit = QLineEdit()
        self._key_edit.setPlaceholderText(tr("如 aggressive（小写字母/数字/下划线）"))
        form.addRow(tr("标识 key："), self._key_edit)
        self._name_edit = QLineEdit()
        self._name_edit.setPlaceholderText(tr("如 激进回收"))
        form.addRow(tr("规则组名称："), self._name_edit)
        # 保存位置只对开发者开放：私有规则组放 local，不写进随包的 system
        self._layer_combo: QComboBox | None = None
        if choose_layer:
            self._layer_combo = QComboBox()
            for layer, label in _LAYER_CHOICES:
                self._layer_combo.addItem(tr(label), layer)
            form.addRow(tr("保存位置："), self._layer_combo)
        layout.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        apply_dialog_button_box_style(buttons)
        layout.addWidget(buttons)

    def _on_accept(self):
        key, name = self.group_key(), self.group_name()
        if not _KEY_RE.match(key):
            QMessageBox.warning(
                self, self._title,
                tr("key 须为小写英文标识（字母开头，可含数字/下划线）"))
            return
        if not name:
            QMessageBox.warning(self, self._title, tr("规则组名称不能为空"))
            return
        self.accept()

    def group_key(self) -> str:
        return self._key_edit.text().strip()

    def group_name(self) -> str:
        return self._name_edit.text().strip()

    def group_layer(self) -> str | None:
        """开发者选择的保存位置；未开放该选项时 None（按模式默认）。"""
        if self._layer_combo is None:
            return None
        return str(self._layer_combo.currentData())


class BaseRuleGroupPage(QWidget):
    """基础规则组管理页（组切换 + CRUD + min_level / 调律门槛）"""

    def __init__(self, manager: TuningGroupManager, group_key: str,
                 status_cb: Callable[[str, bool], None], parent=None):
        super().__init__(parent)
        self._manager = manager
        groups = manager.get_groups()
        self._display_keys: list[str] = []
        self._group_key = group_key if manager.get_group(group_key) \
            else (self._ordered_keys(groups)[0] if groups else "")
        self._status_cb = status_cb
        # 规则组切换回调（由对话框注册，通知行为页重载）
        self._switch_cb: Callable[[str], None] | None = None
        self._loading = True
        self._init_ui()
        self._refresh()
        self._load()
        self._loading = False

    # ── 对外接口 ──

    def set_switch_callback(self, cb: Callable[[str], None]):
        """注册规则组切换回调（切换下拉时以新 key 调用）"""
        self._switch_cb = cb

    def current_group_key(self) -> str:
        return self._group_key

    def display_group_keys(self) -> list[str]:
        """下拉框与表格共用的显示顺序。"""
        return list(self._display_keys)

    def refresh(self):
        """重载规则组清单（下拉 + 列表），外部目录变更后可调用"""
        self._loading = True
        self._refresh()
        self._load()
        self._loading = False

    # ── UI 构建 ──

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            "<b>" + tr("基础规则") + "</b>（" + tr("等级门槛 + 调律门槛 + 扫描/材料/调律处理，"
            "一组一套可切换；流派规则全局不受影响") + "）"))

        # 当前规则（切换即激活）
        combo_row = QHBoxLayout()
        combo_row.addWidget(QLabel("<b>" + tr("当前规则") + "</b>"))
        self._combo = QComboBox()
        self._combo.setToolTip(
            tr("切换后扫描处理/材料处理/调律处理页同步对准该组，"
               "不会修改用户在自动调律页选择的规则组"))
        self._combo.currentIndexChanged.connect(self._on_combo_changed)
        combo_row.addWidget(self._combo)
        combo_row.addStretch()
        layout.addLayout(combo_row)

        # 规则组列表（规则组名 / 等级门槛 / 调律门槛 / 规则说明 / 保存位置）
        self._choose_layer = self._manager.can_choose_layer()
        self._table = QTableWidget(0, 5)
        self._table.setHorizontalHeaderLabels(
            [tr("规则组名"), tr("等级门槛"), tr("调律门槛"), tr("规则说明"), tr("保存位置")])
        for col, width in enumerate((220, 100, 120, 300, 110)):
            self._table.setColumnWidth(col, width)
        # 保存位置只对开发者有意义：普通用户永远写 local，看这一列只会困惑
        self._table.setColumnHidden(_COL_LAYER, not self._choose_layer)
        self._table.setEditTriggers(
            QTableWidget.EditTrigger.DoubleClicked
            | QTableWidget.EditTrigger.EditKeyPressed)
        self._table.cellChanged.connect(self._on_cell_changed)
        self._table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(
            QTableWidget.SelectionMode.SingleSelection)
        self._table.itemSelectionChanged.connect(self._refresh_delete_state)
        self._table.setToolTip(tr("双击「规则说明」列可编辑，选中一行后可复制/删除该规则组"))
        layout.addWidget(self._table)

        btn_row = QHBoxLayout()
        add_btn = QPushButton(tr("新增规则组"))
        add_btn.clicked.connect(self._on_add)
        btn_row.addWidget(add_btn)
        copy_btn = QPushButton(tr("复制选中"))
        copy_btn.setToolTip(tr("复制选中规则组为独立副本"))
        copy_btn.clicked.connect(self._on_copy)
        btn_row.addWidget(copy_btn)
        self._del_btn = QPushButton(tr("删除选中"))
        self._del_btn.setToolTip(tr("删除选中规则组（至少保留一个）"))
        self._del_btn.clicked.connect(self._on_delete)
        btn_row.addWidget(self._del_btn)
        apply_button_style(add_btn)
        apply_button_style(copy_btn, variant="neutral")
        apply_button_style(self._del_btn, variant="danger")
        btn_row.addStretch()
        layout.addLayout(btn_row)

        layout.addStretch()

    def _refresh_delete_state(self) -> None:
        key = self._selected_key()
        protected = bool(key and self._manager.is_system_group(key))
        self._del_btn.setEnabled(bool(key) and not protected)
        if protected:
            self._del_btn.setToolTip(tr("系统规则组不可删除"))
        else:
            self._del_btn.setToolTip(tr("删除选中规则组（至少保留一个）"))

    # ── 清单刷新 ──

    def _ordered_keys(self, groups: dict) -> list[str]:
        """系统/远程在前、本地在后；各层保留管理器的 order 顺序。"""
        return list(groups)

    def _refresh(self):
        groups = self._manager.get_groups()
        self._display_keys = self._ordered_keys(groups)
        if self._group_key not in groups:
            self._group_key = self._display_keys[0] if self._display_keys else ""
        self._combo.blockSignals(True)
        self._combo.clear()
        for key in self._display_keys:
            self._combo.addItem(groups[key].name, key)
        idx = self._combo.findData(self._group_key)
        self._combo.setCurrentIndex(max(idx, 0))
        # 规则组名称是页面主选择，不应为追求紧凑而缩到只剩箭头。
        fit_combo_to_contents(self._combo, minimum=200)
        self._combo.blockSignals(False)

        self._table.blockSignals(True)
        self._table.setRowCount(0)
        select_row = -1
        _readonly = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        _editable = _readonly | Qt.ItemFlag.ItemIsEditable
        for i, key in enumerate(self._display_keys):
            g = groups[key]
            row = self._table.rowCount()
            self._table.insertRow(row)
            # 列 0-2 只读，列 3（规则说明）可编辑，列 4 是下拉（仅开发模式）
            for col, text in enumerate((
                    g.name,
                    str(g.scan.min_level),
                    f"预期 ≥ {RATING_LABELS.get(g.scan.entry_min_rating, g.scan.entry_min_rating)}",
                    g.description)):
                item = QTableWidgetItem(text)
                item.setFlags(_editable if col == _COL_DESC else _readonly)
                self._table.setItem(row, col, item)
            self._table.setItem(row, _COL_LAYER, QTableWidgetItem(""))
            if self._choose_layer:
                self._table.setCellWidget(
                    row, _COL_LAYER, self._make_layer_combo(g.key))
            if g.key == self._group_key:
                select_row = i
        self._table.blockSignals(False)
        if select_row >= 0:
            self._table.selectRow(select_row)

    def _make_layer_combo(self, key: str) -> QComboBox:
        """保存位置下拉：改选即把规则组文件搬到另一层。"""
        combo = QComboBox()
        for layer, label in _LAYER_CHOICES:
            combo.addItem(tr(label), layer)
        current = self._manager.layer_of(key)
        idx = combo.findData(current)
        if idx < 0:
            # 远程等不可选的层：只展示，不允许改
            combo.addItem(tr(_LAYER_LABELS.get(current, current or "?")), current)
            idx = combo.count() - 1
            combo.setEnabled(False)
        combo.setCurrentIndex(idx)
        combo.setToolTip(tr("开发模式：本地 = 只在这台机器生效，不随安装包发布"))
        combo.currentIndexChanged.connect(
            lambda _i, k=key, c=combo: self._on_layer_changed(k, str(c.currentData())))
        return combo

    def _on_layer_changed(self, key: str, layer: str):
        if self._loading:
            return
        try:
            self._manager.move_group(key, layer)
        except (RuleValidationError, PermissionError, OSError) as e:
            self._status_cb(tr("搬移失败：{e}").format(e=e), True)
            self.refresh()
            return
        self.refresh()
        self._set_saved_status(
            tr("规则组「{key}」已移到{layer}").format(
                key=key, layer=tr(_LAYER_LABELS.get(layer, layer))))

    # ── 规则说明编辑 ──

    def _on_cell_changed(self, row: int, col: int):
        """规则说明列编辑完成即校验写盘"""
        if col != _COL_DESC or self._loading:
            return
        if row >= len(self._display_keys):
            return
        key = self._display_keys[row]
        new_desc = (self._table.item(row, col).text().strip()
                    if self._table.item(row, col) else "")
        # 更新 raw dict 的 description 字段
        raw = self._manager.get_raw(key)
        raw["description"] = new_desc
        err = self._manager.validate(raw)
        if err:
            self._status_cb(tr("校验失败（未保存）：{err}").format(err=err), True)
            return
        try:
            self._manager.save_group(key, raw)
        except Exception as e:  # noqa: BLE001
            logger.exception("规则说明保存失败")
            self._status_cb(tr("保存失败：{e}").format(e=e), True)
            return
        self._set_saved_status(tr("规则说明已保存（{key}）").format(key=key))

    # ── 规则组切换 ──

    def _on_combo_changed(self, _index: int):
        key = self._combo.currentData()
        if not key or key == self._group_key:
            return
        self._group_key = key
        self._loading = True
        self._refresh()
        self._load()
        self._loading = False
        if self._switch_cb is not None:
            self._switch_cb(key)

    # ── CRUD ──

    def _on_add(self):
        dlg = _NewGroupDialog(tr("新增基础规则组"), self, choose_layer=self._choose_layer)
        if not dlg.exec():
            return
        key, name = dlg.group_key(), dlg.group_name()
        try:
            self._manager.create_group(key, name, layer=dlg.group_layer())
        except RuleValidationError as e:
            QMessageBox.warning(self, tr("新增基础规则组"), str(e))
            return
        self._switch_to(key)
        self._set_saved_status(tr("已新增基础规则组「{name}」").format(name=name))

    def _on_copy(self):
        src_key = self._selected_key()
        if src_key is None:
            self._status_cb(tr("请先在列表中选中要复制的规则组"), True)
            return
        dlg = _NewGroupDialog(tr("复制基础规则组"), self, choose_layer=self._choose_layer)
        if not dlg.exec():
            return
        key, name = dlg.group_key(), dlg.group_name()
        try:
            self._manager.copy_group(src_key, key, name, layer=dlg.group_layer())
        except RuleValidationError as e:
            QMessageBox.warning(self, tr("复制基础规则组"), str(e))
            return
        self._switch_to(key)
        self._set_saved_status(tr("已复制为基础规则组「{name}」").format(name=name))

    def _on_delete(self):
        key = self._selected_key()
        if key is None:
            self._status_cb(tr("请先在列表中选中要删除的规则组"), True)
            return
        groups = self._manager.get_groups()
        if len(groups) <= 1:
            self._status_cb(tr("至少保留一个规则组"), True)
            return
        group = self._manager.get_group(key)
        name = group.name if group else key
        if QMessageBox.question(
                self, tr("删除基础规则组"),
                tr("确定删除规则组「{name}」？该操作不可恢复。").format(name=name)) \
                != QMessageBox.StandardButton.Yes:
            return
        try:
            self._manager.delete_group(key)
        except (RuleValidationError, PermissionError) as e:
            QMessageBox.warning(self, tr("删除基础规则组"), str(e))
            return
        if self._group_key == key:
            groups = self._manager.get_groups()
            ordered = self._ordered_keys(groups)
            self._group_key = ordered[0] if ordered else ""
            if self._switch_cb is not None and self._group_key:
                self._switch_cb(self._group_key)
        self._loading = True
        self._refresh()
        self._load()
        self._loading = False
        self._set_saved_status(tr("已删除基础规则组「{name}」").format(name=name))

    def _selected_key(self) -> str | None:
        row = self._table.currentRow()
        if row < 0 or row >= len(self._display_keys):
            return None
        return self._display_keys[row]

    def _switch_to(self, key: str):
        """CRUD 成功后切到目标组并通知行为页"""
        self._group_key = key
        self._loading = True
        self._refresh()
        self._load()
        self._loading = False
        if self._switch_cb is not None:
            self._switch_cb(key)

    # ── 门槛展示（当前组）──

    def _load(self):
        # 门槛编辑已移至扫描处理页，本页只负责规则组列表展示和 CRUD
        pass

    def _build(self) -> dict:
        # 本页不再负责编辑任何配置段，返回当前 raw 供校验用
        return self._manager.get_raw(self._group_key)

    def _apply(self):
        if self._loading:
            return
        data = self._build()
        err = self._manager.validate(data)
        if err:
            self._status_cb(tr("校验失败（未保存）：{err}").format(err=err), True)
            return
        try:
            self._manager.save_group(self._group_key, data)
        except Exception as e:  # noqa: BLE001
            logger.exception("基础规则组保存失败")
            self._status_cb(tr("保存失败：{e}").format(e=e), True)
            return
        self._refresh()
        self._set_saved_status()

    def _set_saved_status(self, text: str | None = None):
        now = datetime.now().strftime("%H:%M:%S")
        self._status_cb(text or tr("已保存并生效（{now}）").format(now=now), False)
