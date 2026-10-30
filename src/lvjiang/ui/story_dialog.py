"""自动剧情文案库编辑对话框（通用工具，挂在「工具」菜单）。

支持多配置文件组：每组一个 ``tools/auto_plot/<组名>.json``，激活时全部组
一起生效。普通用户只能保存到 ``local``；``system`` 是随发布内容，仅在开发者
模式可选。

顶部两行：
1. 配置文件组下拉 + 新建/删除；
2. 匹配阈值 / 保存到 / 文案库来源。
"""

from __future__ import annotations

import uuid

from loguru import logger
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from lvjiang.core.config.resolver import get_resolver
from lvjiang.core.story import (
    AUTO_PLOT_DIR,
    StoryDoc,
    StoryEntry,
    delete_entry,
    delete_group,
    list_groups,
    load_group,
    load_settings,
    save_group,
    save_settings,
    upsert_entry,
)
from lvjiang.i18n import tr
from lvjiang.ui.button_styles import (
    apply_button_style,
    apply_dialog_button_box_style,
    exec_styled_message_box,
)
from lvjiang.ui.combo_box import AutoWidthComboBox
from lvjiang.ui.layout_helpers import (
    configure_navigation_list,
    navigation_width_for_chars,
)


class StoryChoicesDialog(QDialog):
    """剧情文案库编辑器（多配置组）。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("自动剧情"))
        self.setMinimumSize(960, 680)
        self.resize(1080, 760)
        self._current_id = ""
        self._build_ui()
        self._reload_groups()

    # ─── 构建 ────────────────────────────────────────────

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        layout.addLayout(self._build_group_row())
        layout.addLayout(self._build_settings_row())

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self._build_list())
        splitter.addWidget(self._build_form())
        splitter.setCollapsible(0, False)
        splitter.setCollapsible(1, False)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([320, 700])
        layout.addWidget(splitter, 1)

        layout.addLayout(self._build_bottom())

    def _build_group_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.addWidget(QLabel(tr("配置文件组：")))
        self._group = AutoWidthComboBox()
        self._group.setToolTip(
            tr("激活时全部配置组一起生效；这里只切换要编辑的那一组")
        )
        self._group.currentIndexChanged.connect(self._on_group_changed)
        row.addWidget(self._group)

        new_btn = QPushButton(tr("＋ 新建"))
        new_btn.setAutoDefault(False)
        new_btn.clicked.connect(self._on_new_group)
        del_btn = QPushButton(tr("删除组"))
        del_btn.setAutoDefault(False)
        del_btn.clicked.connect(self._on_delete_group)
        apply_button_style(new_btn)
        apply_button_style(del_btn, variant="danger")
        row.addWidget(new_btn)
        row.addWidget(del_btn)
        row.addStretch(1)
        return row

    def _build_settings_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(12)

        self._threshold = QDoubleSpinBox()
        self._threshold.setRange(0.0, 1.0)
        self._threshold.setSingleStep(0.05)
        self._threshold.setDecimals(2)
        self._threshold.setToolTip(
            tr("选项集合相似度低于该值时视为未收录，工作流会暂停等你手动选择")
        )
        row.addWidget(QLabel(tr("匹配阈值：")))
        row.addWidget(self._threshold)

        self._layer = AutoWidthComboBox()
        self._layer.addItem(tr("本地"), "local")
        if get_resolver().is_dev_mode():
            self._layer.addItem(tr("系统（随发布）"), "system")
        else:
            self._layer.addItem(tr("系统（仅开发者）"), "system")
            self._layer.model().item(1).setEnabled(False)
        self._layer.setToolTip(tr("普通用户只能保存到本地；系统为随发布内容"))
        row.addWidget(QLabel(tr("保存到：")))
        row.addWidget(self._layer)

        row.addStretch(1)
        row.addWidget(QLabel(tr("文案库来源：")))
        self._origin = QLabel()
        row.addWidget(self._origin)
        return row

    def _build_list(self) -> QWidget:
        box = QWidget()
        column = QVBoxLayout(box)
        column.setContentsMargins(0, 0, 6, 0)
        column.setSpacing(6)

        self._search = QLineEdit()
        self._search.setPlaceholderText(tr("搜索场景 / 选项 / 答案"))
        self._search.textChanged.connect(self._filter_list)
        column.addWidget(self._search)

        self._list = QListWidget()
        configure_navigation_list(
            self._list,
            minimum_width=navigation_width_for_chars(self._list, 8),
        )
        self._list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self._list.currentItemChanged.connect(self._on_select)
        column.addWidget(self._list, 1)

        buttons = QHBoxLayout()
        add_btn = QPushButton(tr("＋ 新增文案"))
        add_btn.setAutoDefault(False)
        add_btn.clicked.connect(self._on_add)
        del_btn = QPushButton(tr("删除文案"))
        del_btn.setAutoDefault(False)
        del_btn.clicked.connect(self._on_delete)
        apply_button_style(add_btn)
        apply_button_style(del_btn, variant="danger")
        buttons.addWidget(add_btn)
        buttons.addWidget(del_btn)
        buttons.addStretch(1)
        column.addLayout(buttons)
        return box

    def _build_form(self) -> QWidget:
        box = QWidget()
        form = QFormLayout(box)
        form.setContentsMargins(6, 0, 0, 0)
        form.setFieldGrowthPolicy(
            QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow
        )

        self._scene = QLineEdit()
        self._scene.setPlaceholderText(tr("如 主线-第三章-问剑"))
        form.addRow(tr("场景/任务："), self._scene)
        self._question = QLineEdit()
        self._question.setPlaceholderText(tr("问题或旁白关键文本（可选）"))
        form.addRow(tr("问题/旁白："), self._question)
        self._options = QPlainTextEdit()
        self._options.setPlaceholderText(tr("每行一个选项"))
        self._options.textChanged.connect(self._sync_answers)
        form.addRow(tr("选项："), self._options)
        self._answer = AutoWidthComboBox()
        form.addRow(tr("正确答案："), self._answer)
        self._note = QLineEdit()
        self._note.setPlaceholderText(tr("为什么选这个（可选）"))
        form.addRow(tr("备注："), self._note)
        return box

    def _build_bottom(self) -> QHBoxLayout:
        row = QHBoxLayout()
        self._status = QLabel(tr("修改暂存于当前对话框，点击保存后生效"))
        self._status.setStyleSheet("color: palette(mid);")
        row.addWidget(self._status, 1)

        self._buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Close
        )
        self._buttons.button(QDialogButtonBox.StandardButton.Save).setText(tr("保存"))
        self._buttons.button(QDialogButtonBox.StandardButton.Close).setText(tr("关闭"))
        self._buttons.accepted.connect(self._on_save)
        self._buttons.rejected.connect(self.reject)
        apply_dialog_button_box_style(self._buttons)
        row.addWidget(self._buttons)
        return row

    # ─── 组管理 ──────────────────────────────────────────

    def _reload_groups(self, select: str = "") -> None:
        groups = list_groups()
        current = select or self._group.currentText()
        self._group.blockSignals(True)
        self._group.clear()
        self._group.addItems(groups)
        self._group.blockSignals(False)
        if current and current in groups:
            self._group.setCurrentText(current)
        elif groups:
            self._group.setCurrentIndex(0)
        self._reload_entries()

    def _current_group(self) -> str:
        return self._group.currentText().strip()

    def _on_new_group(self) -> None:
        name, ok = QInputDialog.getText(
            self, tr("新建配置组"), tr("组名（如 主线-第三章 / 活动-夏日）：")
        )
        name = name.strip()
        if not ok or not name:
            return
        if name in list_groups():
            self._set_status(tr("该配置组已存在"), error=True)
            return
        try:
            save_group(name, StoryDoc(), layer=self._layer.currentData())
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"story: 新建配置组失败: {exc}")
            self._set_status(tr("新建失败：") + str(exc), error=True)
            return
        self._reload_groups(select=name)
        self._set_status(tr("已新建配置组：") + name)

    def _on_delete_group(self) -> None:
        group = self._current_group()
        if not group:
            self._set_status(tr("没有可删除的配置组"), error=True)
            return
        box = QMessageBox(self)
        box.setWindowTitle(tr("确认删除"))
        box.setText(tr("删除配置组「{name}」及其全部文案？").format(name=group))
        box.setStandardButtons(
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        box.setDefaultButton(QMessageBox.StandardButton.No)
        if exec_styled_message_box(box) != QMessageBox.StandardButton.Yes:
            return
        try:
            delete_group(group, layer=self._layer.currentData())
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"story: 删除配置组失败: {exc}")
            self._set_status(tr("删除失败：") + str(exc), error=True)
            return
        self._reload_groups()
        self._set_status(tr("已删除配置组：") + group)

    # ─── 条目 ────────────────────────────────────────────

    def _on_group_changed(self, _index: int) -> None:
        self._reload_entries()

    def _reload_entries(self) -> None:
        group = self._current_group()
        doc = load_group(group) if group else StoryDoc()
        self._list.blockSignals(True)
        self._list.clear()
        for entry in doc.entries:
            preview = entry.options[0] if entry.options else ""
            item = QListWidgetItem(f"{entry.scene or '—'} ｜ {preview} → {entry.answer}")
            item.setData(Qt.ItemDataRole.UserRole, entry.id)
            self._list.addItem(item)
        self._list.blockSignals(False)
        self._threshold.setValue(load_settings().match_threshold)
        self._refresh_origin()
        self._current_id = ""

    def _find_entry(self, entry_id: str) -> StoryEntry | None:
        group = self._current_group()
        if not group:
            return None
        return next(
            (item for item in load_group(group).entries if item.id == entry_id), None
        )

    def _filter_list(self, text: str) -> None:
        keyword = text.strip().lower()
        for index in range(self._list.count()):
            item = self._list.item(index)
            item.setHidden(bool(keyword) and keyword not in item.text().lower())

    def _sync_answers(self) -> None:
        current = self._answer.currentText()
        options = [
            line.strip()
            for line in self._options.toPlainText().splitlines()
            if line.strip()
        ]
        self._answer.blockSignals(True)
        self._answer.clear()
        self._answer.addItems(options)
        if current in options:
            self._answer.setCurrentText(current)
        self._answer.blockSignals(False)

    def _refresh_origin(self) -> None:
        group = self._current_group()
        if not group:
            self._origin.setText(tr("无配置组"))
            self._origin.setStyleSheet("color: palette(mid);")
            self._origin.setToolTip("")
            return
        resolver = get_resolver()
        rel = f"{AUTO_PLOT_DIR}/{group}.json"
        local_exists = (resolver.local_dir / rel).exists()
        system_exists = (resolver.system_dir / rel).exists()
        if local_exists:
            self._origin.setText(tr("本地"))
            self._origin.setStyleSheet("color: palette(highlight);")
            tip = tr("本地覆盖生效") + (tr("；系统也有副本") if system_exists else "")
        elif system_exists:
            self._origin.setText(tr("系统"))
            self._origin.setStyleSheet("color: palette(mid);")
            tip = tr("来自随发布的系统配置")
        else:
            self._origin.setText(tr("未创建"))
            self._origin.setStyleSheet("color: palette(mid);")
            tip = tr("该组还没有落盘，保存后创建")
        self._origin.setToolTip(tip)

    def _set_status(self, text: str, *, error: bool = False) -> None:
        self._status.setText(text)
        self._status.setStyleSheet(
            "color: #c62828;" if error else "color: palette(mid);"
        )

    # ─── 交互 ────────────────────────────────────────────

    def _on_select(self, current, _previous) -> None:
        if current is None:
            return
        entry_id = current.data(Qt.ItemDataRole.UserRole)
        entry = self._find_entry(entry_id)
        if entry is None:
            return
        self._current_id = entry_id
        self._scene.setText(entry.scene)
        self._question.setText(entry.question)
        self._options.setPlainText("\n".join(entry.options))
        self._answer.clear()
        self._answer.addItems(list(entry.options))
        self._answer.setCurrentText(entry.answer)
        self._note.setText(entry.note)
        self._set_status(tr("正在编辑：") + (entry.scene or entry.id))

    def _on_add(self) -> None:
        if not self._current_group():
            self._set_status(tr("请先新建一个配置组"), error=True)
            return
        self._current_id = ""
        self._scene.clear()
        self._question.clear()
        self._options.clear()
        self._answer.clear()
        self._note.clear()
        self._list.clearSelection()
        self._set_status(tr("新建文案：填写选项与正确答案后保存"))

    def _on_delete(self) -> None:
        group = self._current_group()
        if not self._current_id or not group:
            self._set_status(tr("请先在左侧选择要删除的文案"), error=True)
            return
        box = QMessageBox(self)
        box.setWindowTitle(tr("确认删除"))
        box.setText(tr("删除这条文案？"))
        box.setStandardButtons(
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        box.setDefaultButton(QMessageBox.StandardButton.No)
        if exec_styled_message_box(box) != QMessageBox.StandardButton.Yes:
            return
        try:
            delete_entry(
                self._current_id,
                group=group,
                layer=self._layer.currentData(),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"story: 删除文案失败: {exc}")
            self._set_status(tr("删除失败：") + str(exc), error=True)
            return
        self._current_id = ""
        self._reload_entries()
        self._set_status(tr("已删除"))

    def _on_save(self) -> None:
        group = self._current_group()
        if not group:
            self._set_status(tr("请先新建一个配置组"), error=True)
            return
        options = [
            line.strip()
            for line in self._options.toPlainText().splitlines()
            if line.strip()
        ]
        answer = self._answer.currentText().strip()
        if not options or not answer:
            self._set_status(tr("选项和正确答案不能为空"), error=True)
            return
        if answer not in options:
            self._set_status(tr("正确答案必须是选项之一"), error=True)
            return
        entry = StoryEntry(
            id=self._current_id or uuid.uuid4().hex[:12],
            options=tuple(options),
            answer=answer,
            question=self._question.text().strip(),
            scene=self._scene.text().strip(),
            note=self._note.text().strip(),
        )
        layer = self._layer.currentData()
        try:
            upsert_entry(entry, group=group, layer=layer)
            save_settings(self._threshold.value(), layer=layer)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"story: 保存文案失败: {exc}")
            self._set_status(tr("保存失败：") + str(exc), error=True)
            return
        self._current_id = entry.id
        self._reload_entries()
        self._set_status(
            tr("已保存到") + str(self._layer.currentText()) + tr("，配置组「") + group + tr("」")
        )


__all__ = ["StoryChoicesDialog"]
