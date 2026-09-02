"""视图管理对话框 - 开启/取消多视图，新增/重命名/删除视图，调整顺序"""

import re

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ...core.layout_manager import rename_view_screenshots
from ...core.scene_definition import BASE_VIEW_KEY
from ...core.scene_registry import get_registry, sync_scene_cache
from ...core.scene_transitions import entries_of_view, exits_of_view
from ...i18n import tr
from ..button_styles import (
    apply_button_style,
    apply_dialog_button_box_style,
    fit_button_width,
)

_RE_VIEW_KEY = re.compile(r"^[a-z][a-z0-9_]*$")


class ViewManagerDialog(QDialog):
    """管理单个场景的视图（同一页面的多个滚动态）

    视图只影响编辑期的可见性与底图，不进入运行时寻址。
    """

    def __init__(self, scene_key: str, parent=None):
        super().__init__(parent)
        self._scene_key = scene_key
        self._registry = get_registry()
        self.setWindowTitle(tr("视图管理"))
        self.setMinimumSize(640, 560)
        self.resize(760, 680)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            "视图用于把同一页面的多个滚动态分屏排布，避免坐标叠在一起。\n"
            "选定视图时只展示该视图自身的定义，选「全部」才展示所有视图。"
        ))

        self._list = QListWidget()
        self._list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self._list.setMinimumHeight(240)
        # 默认行高下几条视图会挤成一片文字，读不出「这是几条独立视图」。
        # 拉开行距 + 加厚行内边距 + 给每行描边，让每个视图各占一块。
        self._list.setSpacing(6)
        self._list.setStyleSheet(
            "QListWidget { outline: none; }"
            "QListWidget::item {"
            "  min-height: 34px;"
            "  padding: 8px 10px;"
            "  border: 1px solid palette(mid);"
            "  border-radius: 4px;"
            "}"
            "QListWidget::item:selected {"
            "  background-color: palette(highlight);"
            "  color: palette(highlighted-text);"
            "  border: 1px solid palette(highlight);"
            "}"
        )
        self._list.currentRowChanged.connect(self._update_move_buttons)
        self._list.currentRowChanged.connect(self._refresh_contract)
        layout.addWidget(self._list, 1)

        self._cb_same_layer = QCheckBox(tr("同层视图（与基底同一图层，如滚动后的另一屏）"))
        self._cb_same_layer.setToolTip(
            tr("同层视图没有入口，只有跳转；取消勾选表示它是独立页面，需要声明入口"))
        self._cb_same_layer.toggled.connect(self._on_toggle_same_layer)
        layout.addWidget(self._cb_same_layer)

        # 页面切换契约：选中视图由哪些按钮进入、又能转向哪里。
        # 只读展示——契约是逐步补全的声明，不驱动执行。
        self._contract = QWidget()
        contract_layout = QVBoxLayout(self._contract)
        contract_layout.setContentsMargins(0, 4, 0, 4)
        contract_layout.setSpacing(2)
        self._entry_title = self._contract_title(tr("入口"))
        contract_layout.addWidget(self._entry_title)
        self._entry_lines = QVBoxLayout()
        self._entry_lines.setSpacing(3)
        contract_layout.addLayout(self._entry_lines)
        contract_layout.addSpacing(5)
        self._exit_title = self._contract_title(tr("跳转"))
        contract_layout.addWidget(self._exit_title)
        self._exit_lines = QVBoxLayout()
        self._exit_lines.setSpacing(3)
        contract_layout.addLayout(self._exit_lines)
        layout.addWidget(self._contract)

        btn_row = QHBoxLayout()
        self._btn_add = QPushButton(tr("新增视图"))
        self._btn_add.clicked.connect(self._on_add)
        btn_row.addWidget(self._btn_add)
        self._btn_rename = QPushButton(tr("重命名"))
        self._btn_rename.clicked.connect(self._on_rename)
        btn_row.addWidget(self._btn_rename)
        self._btn_delete = QPushButton(tr("删除视图"))
        self._btn_delete.clicked.connect(self._on_delete)
        btn_row.addWidget(self._btn_delete)
        btn_row.addStretch()
        # 上移/下移按钮
        self._btn_up = QPushButton("↑")
        self._btn_up.setFixedWidth(32)
        self._btn_up.setToolTip(tr("上移视图"))
        self._btn_up.clicked.connect(lambda: self._on_move(-1))
        btn_row.addWidget(self._btn_up)
        self._btn_down = QPushButton("↓")
        self._btn_down.setFixedWidth(32)
        self._btn_down.setToolTip(tr("下移视图"))
        self._btn_down.clicked.connect(lambda: self._on_move(1))
        btn_row.addWidget(self._btn_down)
        layout.addLayout(btn_row)

        bottom_row = QHBoxLayout()
        self._btn_disable = QPushButton(tr("取消多视图"))
        self._btn_disable.setToolTip(tr("仅剩基底视图时可用，取消后所有定义归为无视图区分"))
        self._btn_disable.clicked.connect(self._on_disable)
        bottom_row.addWidget(self._btn_disable)
        bottom_row.addStretch()
        btn_close = QPushButton(tr("关闭"))
        btn_close.clicked.connect(self.accept)
        bottom_row.addWidget(btn_close)
        apply_button_style(self._btn_add)
        apply_button_style(self._btn_rename, self._btn_up, self._btn_down,
                           btn_close, variant="neutral")
        apply_button_style(self._btn_delete, self._btn_disable,
                           variant="danger")
        fit_button_width(self._btn_up, self._btn_down, minimum=32)
        layout.addLayout(bottom_row)

        self._changed = False
        self._refresh()

    # ─── 数据 ────────────────────────────────────────────

    @staticmethod
    def _contract_title(text: str) -> QLabel:
        label = QLabel(text)
        label.setStyleSheet("font-weight: 700; color: palette(text);")
        return label

    @staticmethod
    def _replace_contract_lines(target: QVBoxLayout,
                                lines: list[tuple[str, str]]) -> None:
        while target.count():
            item = target.takeAt(0)
            assert item is not None
            widget = item.widget()
            if widget is not None:
                # deleteLater() 本身不会马上移除可见子控件；连续切换视图时旧文案
                # 会与新行重叠。先同步隐藏、脱离父控件，再安排销毁。
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()
        for text, tooltip in lines:
            label = QLabel(text)
            label.setTextFormat(Qt.TextFormat.PlainText)
            label.setWordWrap(False)
            label.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse)
            label.setIndent(12)
            label.setToolTip(tooltip)
            target.addWidget(label)

    def _refresh(self):
        selected = self._selected_view_key()
        self._list.clear()
        views = self._registry.get_scene_views(self._scene_key)
        if not views:
            item = QListWidgetItem(tr("基底  （单视图）"))
            item.setData(Qt.ItemDataRole.UserRole, BASE_VIEW_KEY)
            self._list.addItem(item)
        for v in views:
            label = f"{v.name}  ({v.key})"
            if v.key != BASE_VIEW_KEY and getattr(v, "same_layer", False):
                label += tr("  · 同层")
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, v.key)
            self._list.addItem(item)
        wanted = selected or BASE_VIEW_KEY
        selected_row = next(
            (row for row in range(self._list.count())
             if self._list.item(row).data(Qt.ItemDataRole.UserRole) == wanted),
            0,
        )
        self._list.setCurrentRow(selected_row)
        self._btn_disable.setEnabled(len(views) == 1)
        # 更新上移/下移按钮状态
        self._update_move_buttons()
        self._refresh_contract()

    def _refresh_contract(self, *_args):
        """展示选中视图的入口与转移。

        基底视图是场景入口，没有场景内按钮指向它是正常的；其余视图没有任何
        入口就是**死视图**——要么漏声明了入口，要么它根本不该存在。
        """
        view_key = self._selected_view_key()
        if not view_key:
            self._contract.setVisible(False)
            self._cb_same_layer.setVisible(False)
            return
        self._contract.setVisible(True)
        is_base = view_key == BASE_VIEW_KEY
        self._cb_same_layer.setVisible(not is_base)
        scenes = self._registry.all_scenes()
        entries = entries_of_view(scenes, self._scene_key, view_key)
        exits = exits_of_view(scenes, self._scene_key, view_key)

        def _scene_view_name(scene_key: str, view_key: str) -> str:
            scene = scenes.get(scene_key)
            scene_name = scene.name if scene is not None else scene_key
            if scene is None or not scene.views:
                return f"{scene_name} / {tr('基底')}"
            normalized = view_key or BASE_VIEW_KEY
            view = next((v for v in scene.views if v.key == normalized), None)
            return f"{scene_name} / {view.name if view else normalized}"

        def _entity_name(scene_key: str, entity_key: str) -> str:
            scene = scenes.get(scene_key)
            if scene is None:
                return entity_key
            entity = next(
                (item for item in (*scene.regions, *scene.points)
                 if item.key == entity_key), None)
            return entity.name if entity is not None else entity_key

        def _entry_line(t) -> tuple[str, str]:
            text = (f"{_scene_view_name(t.from_scene, t.from_view)} · "
                    f"{_entity_name(t.from_scene, t.entity)}")
            source = t.from_scene + (f"/{t.from_view}" if t.from_view else "")
            return text, f"{source} [{t.entity}]"

        def _exit_line(t) -> tuple[str, str]:
            text = (f"{_entity_name(t.from_scene, t.entity)} → "
                    f"{_scene_view_name(t.to_scene, t.to_view)}")
            target = t.to_scene + (f"/{t.to_view}" if t.to_view else "")
            return text, f"[{t.entity}] → {target}"

        view = next((v for v in self._registry.get_scene_views(self._scene_key)
                     if v.key == view_key), None)
        same_layer = bool(view is not None and not is_base
                          and getattr(view, "same_layer", False))
        self._cb_same_layer.blockSignals(True)
        self._cb_same_layer.setChecked(same_layer)
        self._cb_same_layer.blockSignals(False)
        if entries:
            entry_lines = [_entry_line(t) for t in entries]
        elif view_key == BASE_VIEW_KEY:
            entry_lines = [(tr("基底视图是场景入口"), "")]
        elif same_layer:
            # 同层视图只是同一图层滚过去的另一个取景，本就没有“进入”这回事。
            entry_lines = [(tr("同层视图与基底处于同一图层，无入口"), "")]
        else:
            entry_lines = [(
                tr("无入口（死视图：补 to: 声明，或改标为同层视图）"), "")]
        exit_lines = ([_exit_line(t) for t in exits] if exits
                      else [(tr("未声明"), "")])
        self._replace_contract_lines(self._entry_lines, entry_lines)
        self._replace_contract_lines(self._exit_lines, exit_lines)

    def _selected_view_key(self) -> str | None:
        item = self._list.currentItem()
        if item is None:
            return None
        return item.data(Qt.ItemDataRole.UserRole)

    # ─── 操作 ────────────────────────────────────────────

    def _on_add(self):
        """新增视图：单对话框同时输入 key 与名称，实时校验"""
        dialog = QDialog(self)
        dialog.setWindowTitle(tr("新增视图"))
        form = QFormLayout(dialog)
        key_edit = QLineEdit()
        key_edit.setPlaceholderText(tr("小写字母开头，仅含小写字母/数字/下划线"))
        form.addRow(tr("视图 Key:"), key_edit)
        error_label = QLabel()
        error_label.setStyleSheet("color: #c62828;")
        error_label.hide()
        form.addRow("", error_label)
        name_edit = QLineEdit()
        name_edit.setPlaceholderText(tr("留空则与 key 相同"))
        form.addRow(tr("视图名称:"), name_edit)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        apply_dialog_button_box_style(buttons)
        form.addRow(buttons)

        # 实时校验：key 格式 + 保留 key + 重复
        ok_btn = buttons.button(QDialogButtonBox.StandardButton.Ok)
        existing = {v.key for v in
                    self._registry.get_scene_views(self._scene_key)}

        def _validate():
            k = key_edit.text().strip()
            if not k:
                ok_btn.setEnabled(False)
                error_label.hide()
                return
            if not _RE_VIEW_KEY.match(k):
                ok_btn.setEnabled(False)
                error_label.setText(tr("key 必须以小写字母开头，仅含小写字母/数字/下划线"))
                error_label.show()
                return
            if k == BASE_VIEW_KEY:
                ok_btn.setEnabled(False)
                error_label.setText(f"{BASE_VIEW_KEY} 是基底视图的保留 key")
                error_label.show()
                return
            if k in existing:
                ok_btn.setEnabled(False)
                error_label.setText(f"视图 key 已存在: {k}")
                error_label.show()
                return
            ok_btn.setEnabled(True)
            error_label.hide()

        key_edit.textChanged.connect(_validate)
        _validate()

        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        key = key_edit.text().strip()
        name = name_edit.text().strip() or key
        try:
            self._registry.add_scene_view(self._scene_key, key, name)
        except ValueError as e:
            QMessageBox.warning(self, tr("新增失败"), str(e))
            return
        sync_scene_cache(self._scene_key)
        self._changed = True
        self._refresh()

    def _on_rename(self):
        """重命名视图：支持修改 key 和名称"""
        old_key = self._selected_view_key()
        if old_key is None:
            return
        views = self._registry.get_scene_views(self._scene_key)
        old_view = next((v for v in views if v.key == old_key), None)
        if old_view is None:
            return
        # 构建重命名对话框
        dialog = QDialog(self)
        dialog.setWindowTitle(tr("重命名视图"))
        form = QFormLayout(dialog)
        key_edit = QLineEdit(old_key)
        key_edit.setPlaceholderText(tr("小写字母开头，仅含小写字母/数字/下划线"))
        form.addRow(tr("视图 Key:"), key_edit)
        error_label = QLabel()
        error_label.setStyleSheet("color: #c62828;")
        error_label.hide()
        form.addRow("", error_label)
        name_edit = QLineEdit(old_view.name)
        name_edit.setPlaceholderText(tr("留空则与 key 相同"))
        form.addRow(tr("视图名称:"), name_edit)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        apply_dialog_button_box_style(buttons)
        form.addRow(buttons)
        # 实时校验
        ok_btn = buttons.button(QDialogButtonBox.StandardButton.Ok)
        existing = {v.key for v in views if v.key != old_key}

        def _validate():
            k = key_edit.text().strip()
            if not k:
                ok_btn.setEnabled(False)
                error_label.hide()
                return
            if not _RE_VIEW_KEY.match(k):
                ok_btn.setEnabled(False)
                error_label.setText(tr("key 必须以小写字母开头，仅含小写字母/数字/下划线"))
                error_label.show()
                return
            if k in existing:
                ok_btn.setEnabled(False)
                error_label.setText(f"视图 key 已存在: {k}")
                error_label.show()
                return
            ok_btn.setEnabled(True)
            error_label.hide()

        key_edit.textChanged.connect(_validate)
        _validate()
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        new_key = key_edit.text().strip()
        new_name = name_edit.text().strip() or new_key
        key_changed = new_key != old_key
        try:
            self._registry.rename_scene_view_key(self._scene_key, old_key, new_key, new_name)
        except ValueError as e:
            QMessageBox.warning(self, tr("重命名失败"), str(e))
            return
        # key 变更时同步重命名截图文件
        if key_changed:
            rename_view_screenshots(self._scene_key, old_key, new_key)
        sync_scene_cache(self._scene_key)
        self._changed = True
        self._refresh()

    def _on_delete(self):
        key = self._selected_view_key()
        if key is None:
            return
        try:
            self._registry.delete_scene_view(self._scene_key, key)
        except ValueError as e:
            QMessageBox.warning(self, tr("删除失败"), str(e))
            return
        sync_scene_cache(self._scene_key)
        self._changed = True
        self._refresh()

    def _on_disable(self):
        try:
            self._registry.disable_scene_views(self._scene_key)
        except ValueError as e:
            QMessageBox.warning(self, tr("无法取消"), str(e))
            return
        sync_scene_cache(self._scene_key)
        self._changed = True
        self._refresh()

    def _update_move_buttons(self):
        """根据当前选中视图的位置更新上移/下移按钮状态"""
        views = self._registry.get_scene_views(self._scene_key)
        key = self._selected_view_key()
        if key is None or len(views) <= 1:
            self._btn_up.setEnabled(False)
            self._btn_down.setEnabled(False)
            return
        idx = next((i for i, v in enumerate(views) if v.key == key), -1)
        self._btn_up.setEnabled(idx > 0)
        self._btn_down.setEnabled(0 <= idx < len(views) - 1)

    def _on_move(self, direction: int):
        """调整视图顺序（direction: -1=上移, +1=下移）"""
        key = self._selected_view_key()
        if key is None:
            return
        try:
            self._registry.move_scene_view(self._scene_key, key, direction)
        except ValueError as e:
            QMessageBox.warning(self, tr("移动失败"), str(e))
            return
        self._changed = True
        self._refresh()
        # 重新选中移动后的项
        views = self._registry.get_scene_views(self._scene_key)
        idx = next((i for i, v in enumerate(views) if v.key == key), -1)
        if idx >= 0:
            self._list.setCurrentRow(idx)

    def _on_toggle_same_layer(self, checked: bool):
        """切换选中视图的同层属性并落盘。"""
        view_key = self._selected_view_key()
        if not view_key or view_key == BASE_VIEW_KEY:
            return
        views = self._registry.get_scene_views(self._scene_key)
        view = next((v for v in views if v.key == view_key), None)
        if view is None or view.same_layer == checked:
            return
        view.same_layer = checked
        self._registry.save_scene_views(self._scene_key)
        self._changed = True
        # 列表行文本带「· 同层」后缀，只刷契约区会让它停在勾选前的状态。
        self._refresh()
