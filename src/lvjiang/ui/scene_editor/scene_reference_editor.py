"""子场景引用定义与布局绑定编辑。"""

from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ...core.scene_definition_models import SubsceneRefDef
from ...core.scene_registry import (
    get_registry,
    get_subscene_scenes,
    is_view_visible,
)
from ...i18n import tr
from ..button_styles import apply_button_style, apply_dialog_button_box_style


class SceneReferenceEditorMixin:
    """父场景中引用子场景的声明和外框绑定。"""

    def _build_reference_panel(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        self._reference_table = QTableWidget(0, 3)
        self._reference_table.setHorizontalHeaderLabels(
            [tr("名称"), "Key", tr("子场景")])
        self._reference_table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows)
        self._reference_table.setSelectionMode(
            QTableWidget.SelectionMode.SingleSelection)
        self._reference_table.setEditTriggers(
            QTableWidget.EditTrigger.NoEditTriggers)
        header = self._reference_table.horizontalHeader()
        assert header is not None
        header.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._reference_table.currentCellChanged.connect(
            self._on_reference_selection)
        self._reference_table.cellDoubleClicked.connect(
            self._on_edit_reference)
        layout.addWidget(self._reference_table)

        buttons = QHBoxLayout()
        self._btn_new_reference = QPushButton(tr("+ 新建引用"))
        self._btn_new_reference.clicked.connect(self._on_new_reference)
        self._btn_bind_reference = QPushButton(tr("绑定引用"))
        self._btn_bind_reference.clicked.connect(self._on_bind_reference)
        self._btn_delete_reference = QPushButton(tr("删除引用"))
        self._btn_delete_reference.clicked.connect(self._on_delete_reference)
        apply_button_style(self._btn_new_reference)
        apply_button_style(self._btn_bind_reference, variant="neutral")
        apply_button_style(self._btn_delete_reference, variant="danger")
        buttons.addWidget(self._btn_new_reference)
        buttons.addWidget(self._btn_bind_reference)
        buttons.addWidget(self._btn_delete_reference)
        buttons.addStretch()
        layout.addLayout(buttons)
        return widget

    def _refresh_reference_list(self):
        if not hasattr(self, "_reference_table"):
            return
        scene = get_registry().get_scene(self._scene_key)
        refs = scene.subscene_refs if scene else []
        bound = {r.key for r in self._canvas.get_subscene_refs()}
        self._reference_table.blockSignals(True)
        self._reference_table.setRowCount(0)
        for ref in refs:
            if not is_view_visible(ref.views, self._current_view):
                continue
            row = self._reference_table.rowCount()
            self._reference_table.insertRow(row)
            mark = "✓" if ref.key in bound else "○"
            self._reference_table.setItem(row, 0, QTableWidgetItem(f"{mark} {ref.name}"))
            self._reference_table.setItem(row, 1, QTableWidgetItem(ref.key))
            self._reference_table.setItem(row, 2, QTableWidgetItem(ref.scene))
        self._reference_table.blockSignals(False)

    def _on_reference_selection(self, row, _col, _prev_row, _prev_col):
        if row < 0:
            return
        item = self._reference_table.item(row, 1)
        if item:
            self._canvas.select_subscene_ref_by_key(item.text())

    def _reference_dialog(self, old=None):
        available = get_subscene_scenes()
        if not available:
            QMessageBox.information(
                self, tr("没有子场景"), tr("请先在目标场景的「场景管理」中标记为引用子场景。"))
            return None
        dialog = QDialog(self)
        dialog.setWindowTitle(tr("编辑引用") if old else tr("新建引用"))
        form = QFormLayout(dialog)
        key_edit = QLineEdit(old.key if old else "")
        name_edit = QLineEdit(old.name if old else "")
        scene_combo = QComboBox()
        for key, name in available:
            scene_combo.addItem(f"{name} ({key})", key)
        if old:
            index = scene_combo.findData(old.scene)
            scene_combo.setCurrentIndex(max(0, index))
        form.addRow("Key:", key_edit)
        form.addRow(tr("名称:"), name_edit)
        form.addRow(tr("子场景:"), scene_combo)
        box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok |
            QDialogButtonBox.StandardButton.Cancel)
        apply_dialog_button_box_style(box)
        box.accepted.connect(dialog.accept)
        box.rejected.connect(dialog.reject)
        form.addRow(box)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        return SubsceneRefDef(
            key=key_edit.text().strip(), name=name_edit.text().strip(),
            scene=str(scene_combo.currentData()),
            view=(old.view if old else self._current_view))

    def _on_new_reference(self):
        ref = self._reference_dialog()
        if ref is None:
            return
        try:
            get_registry().add_subscene_ref_to_scene(self._scene_key, ref)
        except ValueError as exc:
            QMessageBox.warning(self, tr("创建失败"), str(exc))
            return
        self._refresh_reference_list()

    def _on_edit_reference(self, row, _col):
        key_item = self._reference_table.item(row, 1)
        scene = get_registry().get_scene(self._scene_key)
        if not key_item or not scene:
            return
        old = next((r for r in scene.subscene_refs if r.key == key_item.text()), None)
        if old is None:
            return
        new = self._reference_dialog(old)
        if new is None:
            return
        try:
            get_registry().update_subscene_ref_in_scene(self._scene_key, old.key, new)
        except ValueError as exc:
            QMessageBox.warning(self, tr("更新失败"), str(exc))
            return
        instances = self._canvas.get_subscene_refs()
        for instance in instances:
            if instance.key == old.key:
                instance.key = new.key
        self._canvas.set_subscene_refs(instances)
        self._canvas._notify_subscene_ref_changed()
        self._refresh_reference_list()

    def _on_bind_reference(self):
        scene = get_registry().get_scene(self._scene_key)
        if not scene:
            return
        bound = {r.key for r in self._canvas.get_subscene_refs()}
        available = [r for r in scene.subscene_refs if r.key not in bound]
        if not available:
            QMessageBox.information(self, tr("无可用引用"), tr("所有引用都已绑定，或尚未创建引用。"))
            return
        row = self._reference_table.currentRow()
        key = self._reference_table.item(row, 1).text() if row >= 0 else ""
        selected = next((r for r in available if r.key == key), available[0])
        self._canvas.begin_place_subscene_ref(selected)

    def _on_delete_reference(self):
        row = self._reference_table.currentRow()
        item = self._reference_table.item(row, 1) if row >= 0 else None
        if item is None:
            return
        key = item.text()
        if QMessageBox.question(
                self, tr("确认删除"), tr("确定删除引用 {key} 吗？").format(key=key),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        ) != QMessageBox.StandardButton.Yes:
            return
        get_registry().remove_subscene_ref_from_scene(self._scene_key, key)
        refs = [r for r in self._canvas.get_subscene_refs() if r.key != key]
        self._canvas.set_subscene_refs(refs)
        self._canvas._notify_subscene_ref_changed()
        self._refresh_reference_list()
