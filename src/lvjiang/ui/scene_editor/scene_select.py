"""场景选择下拉框 helper - 供编辑弹窗做跨场景迁移、视图归属使用"""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ...core.scene_definition import BASE_VIEW_KEY
from ...core.scene_registry import get_registry, get_scene_views, is_view_visible
from ...i18n import tr
from ..button_styles import apply_button_style


def add_scene_combo_row(form: QFormLayout, current_scene_key: str) -> QComboBox:
    """向表单添加「场景」下拉框行，返回该下拉框

    条目显示 `场景名 (key)`，userData 存 scene_key，默认选中当前场景。
    """
    combo = QComboBox()
    registry = get_registry()
    for key, scene in registry.all_scenes().items():
        combo.addItem(f"{scene.name} ({key})", userData=key)
        if key == current_scene_key:
            combo.setCurrentIndex(combo.count() - 1)
    form.addRow(tr("场景:"), combo)
    return combo


def add_view_combo_row(
    form: QFormLayout, scene_key: str, selected_view: str
) -> QComboBox | None:
    """多视图场景才添加「视图」下拉框行，否则返回 None

    条目显示视图名，userData 存 view key（基底视图为 BASE_VIEW_KEY）。
    selected_view 空视为基底。
    """
    combo = QComboBox()
    _populate_view_combo(combo, scene_key, selected_view)
    form.addRow(tr("视图:"), combo)
    return combo


def _populate_view_combo(combo: QComboBox, scene_key: str, selected_view: str):
    """填充视图下拉框：无多视图则清空并禁用"""
    combo.blockSignals(True)
    combo.clear()
    views = get_scene_views(scene_key)
    if not views:
        combo.addItem(tr("单视图"), userData="")
        combo.setEnabled(False)
    else:
        combo.setEnabled(True)
        for v in views:
            combo.addItem(v.name, userData=v.key)
            is_base = v.key == BASE_VIEW_KEY
            if v.key == selected_view or (not selected_view and is_base):
                combo.setCurrentIndex(combo.count() - 1)
    combo.blockSignals(False)


def connect_scene_view_sync(scene_combo: QComboBox, view_combo: QComboBox | None):
    """连接场景切换信号，同步更新视图下拉框"""
    if view_combo is None:
        return

    def _on_scene_changed(index: int):
        new_scene_key = scene_combo.currentData()
        if new_scene_key:
            _populate_view_combo(view_combo, new_scene_key, "")

    scene_combo.currentIndexChanged.connect(_on_scene_changed)


def combo_view_value(combo: QComboBox | None, fallback: str) -> str:
    """从视图下拉框取归属视图 key（基底归一化为空串），无下拉框时用 fallback"""
    if combo is None:
        view = fallback
    else:
        view = combo.currentData() or ""
    return "" if view == BASE_VIEW_KEY else view


# ─── 多视图归属 ──────────────────────────────────────────

class ViewChecklist(QListWidget):
    """归属视图多选清单。

    同一个按钮出现在多个视图是常态（``close_btn`` 在结果视图和返还视图都在）。
    坐标只有一份、跟布局走，所以多归属不影响坐标，只影响编辑器里哪个视图下
    看得见它。
    """

    def __init__(self, scene_key: str, selected: list[str] | None = None,
                 parent=None):
        super().__init__(parent)
        self.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        self.setMaximumHeight(120)
        self.repopulate(scene_key, selected or [])

    def repopulate(self, scene_key: str, selected: list[str]) -> None:
        self.clear()
        views = get_scene_views(scene_key)
        self._multi = bool(views)
        if not views:
            item = QListWidgetItem(tr("单视图"))
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            self.addItem(item)
            self.setEnabled(False)
            return
        self.setEnabled(True)
        chosen = {v or BASE_VIEW_KEY for v in selected} or {BASE_VIEW_KEY}
        for v in views:
            item = QListWidgetItem(v.name)
            item.setData(Qt.ItemDataRole.UserRole, v.key)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if v.key in chosen
                               else Qt.CheckState.Unchecked)
            self.addItem(item)

    def values(self) -> list[str]:
        """选中的视图 key 列表；未开启多视图返回空列表（= 基底）。"""
        if not getattr(self, "_multi", False):
            return []
        out = []
        for i in range(self.count()):
            item = self.item(i)
            if item is not None and item.checkState() == Qt.CheckState.Checked:
                out.append(str(item.data(Qt.ItemDataRole.UserRole)))
        return out


def add_views_checklist_row(
    form: QFormLayout, scene_key: str, selected: list[str],
) -> ViewChecklist:
    """向表单添加「视图」多选行。"""
    widget = ViewChecklist(scene_key, selected)
    form.addRow(tr("视图:"), widget)
    return widget


def connect_scene_views_sync(scene_combo: QComboBox,
                             checklist: ViewChecklist | None) -> None:
    """场景切换时重填视图清单（跨场景迁移会换一套视图）。"""
    if checklist is None:
        return

    def _on_scene_changed(_index: int):
        new_scene_key = scene_combo.currentData()
        if new_scene_key:
            checklist.repopulate(new_scene_key, [])

    scene_combo.currentIndexChanged.connect(_on_scene_changed)


def checklist_views_value(checklist: ViewChecklist | None,
                          fallback: str) -> list[str]:
    """取归属视图列表；无清单或一个都没勾时退回 fallback。

    一个都不勾等于"哪个视图下都看不见"，那是误操作而不是意图，所以退回。
    """
    if checklist is None:
        return [fallback] if fallback else []
    values = checklist.values()
    if values:
        return values
    return [fallback] if fallback else []


# ─── 跨场景 area 引用 ───────────────────────────────────

class SceneAreaReferenceBatchPicker(QWidget):
    """引用来源：分组 + 场景 + 视图筛选，下方列表多选。

    一次要引十几个 area 是常态（通用控件、公共弹窗），一条一条开对话框
    不现实。所以筛选与选取分开：上面三个下拉只管**缩小范围**，下面的
    列表才是选取，勾几个就一次加几个。

    列表里区域和坐标混排——它们本质都是 area，``add_scene_reference``
    也一视同仁，按类型拆成两处只会让人来回切。
    """

    def __init__(self, current_scene: str, taken_keys: set[str], parent=None):
        super().__init__(parent)
        self._registry = get_registry()
        self._taken = taken_keys
        self._current_scene = current_scene
        self._scenes = self._collect_scenes()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        filters = QHBoxLayout()
        self.group = QComboBox()
        self.scene = QComboBox()
        self.view = QComboBox()
        self.group.setToolTip(tr("分组"))
        self.scene.setToolTip(tr("场景"))
        self.view.setToolTip(tr("视图"))
        for widget, stretch in ((self.group, 2), (self.scene, 3), (self.view, 2)):
            filters.addWidget(widget, stretch)
        layout.addLayout(filters)

        self.items = QListWidget()
        self.items.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        self.items.setMinimumHeight(220)
        layout.addWidget(self.items)

        self._summary = QLabel()
        self._summary.setStyleSheet("color: palette(mid); font-size: 11px;")
        row = QHBoxLayout()
        btn_all = QPushButton(tr("全选"))
        btn_none = QPushButton(tr("全不选"))
        for button, checked in ((btn_all, True), (btn_none, False)):
            apply_button_style(button)
            button.clicked.connect(lambda _c, v=checked: self._set_all(v))
            row.addWidget(button)
        row.addWidget(self._summary, 1)
        layout.addLayout(row)

        for group_key, group_name in self._registry.get_groups():
            if any(scene_key in self._scenes for scene_key in
                   self._registry.get_group_scenes(group_key)):
                self.group.addItem(group_name, userData=group_key)
        self.group.currentIndexChanged.connect(self._on_group_changed)
        self.scene.currentIndexChanged.connect(self._on_scene_changed)
        self.view.currentIndexChanged.connect(self._fill_items)
        self.items.itemChanged.connect(lambda _item: self._refresh_summary())
        self._on_group_changed(self.group.currentIndex())

    def _collect_scenes(self) -> dict[str, list]:
        """候选场景 → 可引用的 area 列表（区域在前，坐标在后）。

        同名 key 会与本场景定义抢命名空间，直接不给选——引用项的 key 恒等于
        源实体 key，改不了。
        """
        visible = {
            scene_key
            for group_key, _name in self._registry.get_groups()
            for scene_key in self._registry.get_group_scenes(group_key)
        }
        found: dict[str, list] = {}
        for scene_key, scene in self._registry.all_scenes().items():
            if (scene_key not in visible or scene_key == self._current_scene
                    or scene.is_subscene):
                continue
            areas = [item for item in (*scene.regions, *scene.points)
                     if item.key not in self._taken]
            if areas:
                found[scene_key] = areas
        return found

    @property
    def has_candidates(self) -> bool:
        return bool(self._scenes)

    def _on_group_changed(self, _index: int) -> None:
        self.scene.blockSignals(True)
        self.scene.clear()
        for scene_key in self._registry.get_group_scenes(self.group.currentData()):
            if scene_key not in self._scenes:
                continue
            scene = self._registry.get_scene(scene_key)
            if scene is None:
                continue
            self.scene.addItem(scene.name, userData=scene_key)
            self.scene.setItemData(self.scene.count() - 1, scene_key,
                                   Qt.ItemDataRole.ToolTipRole)
        self.scene.blockSignals(False)
        self._on_scene_changed(self.scene.currentIndex())

    def _on_scene_changed(self, _index: int) -> None:
        """换场景要重填视图下拉：每个场景有自己的一套视图。"""
        self.view.blockSignals(True)
        self.view.clear()
        self.view.addItem(tr("全部视图"), userData="")
        scene_key = self.scene.currentData()
        views = get_scene_views(scene_key) if scene_key else []
        for view in views:
            self.view.addItem(view.name, userData=view.key)
        self.view.setEnabled(bool(views))
        self.view.blockSignals(False)
        self._fill_items()

    def _fill_items(self) -> None:
        self.items.blockSignals(True)
        self.items.clear()
        scene_key = self.scene.currentData()
        current_view = str(self.view.currentData() or "")
        for area in self._scenes.get(scene_key, []):
            if not is_view_visible(area.views, current_view):
                continue
            kind = tr("区域") if hasattr(area, "is_clickable") else tr("坐标")
            item = QListWidgetItem(f"[{kind}] {area.name}　{area.key}")
            item.setData(Qt.ItemDataRole.UserRole, (scene_key, area.key))
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked)
            item.setToolTip(f"{scene_key}.{area.key}")
            self.items.addItem(item)
        self.items.blockSignals(False)
        self._refresh_summary()

    def _set_all(self, checked: bool) -> None:
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        self.items.blockSignals(True)
        for index in range(self.items.count()):
            item = self.items.item(index)
            if item is not None:
                item.setCheckState(state)
        self.items.blockSignals(False)
        self._refresh_summary()

    def _refresh_summary(self) -> None:
        total = self.items.count()
        if not total:
            self._summary.setText(tr("该场景在此视图下没有可引用的 area"))
            return
        self._summary.setText(
            tr("已选 {n}/{total}").format(n=len(self.values()), total=total))

    def values(self) -> list[tuple[str, str]]:
        """勾选的 (场景 key, 实体 key) 列表，按列表顺序。"""
        chosen = []
        for index in range(self.items.count()):
            item = self.items.item(index)
            if item is not None and item.checkState() == Qt.CheckState.Checked:
                scene_key, entity = item.data(Qt.ItemDataRole.UserRole)
                chosen.append((str(scene_key), str(entity)))
        return chosen


def prompt_scene_area_references(
    parent, scene_key: str, current_view: str, taken_keys: set[str],
    *, title: str,
) -> list[tuple[str, str, list[str]]]:
    """弹出引用选择对话框，返回 (源场景, 实体, 归属视图) 列表。

    取消或没勾任何一项都返回空列表。归属视图对本次选中的全部条目共用——
    一次引十几个 area 时它们几乎总是落在同一个视图，逐条问反而更烦；个别
    要挪的，加完在列表里双击那一行改。
    """
    from PyQt6.QtWidgets import QDialog, QDialogButtonBox, QMessageBox

    from ..button_styles import apply_dialog_button_box_style

    picker = SceneAreaReferenceBatchPicker(scene_key, taken_keys)
    if not picker.has_candidates:
        QMessageBox.information(
            parent, title,
            tr("没有可引用的 area：一级场景中同名的 key 已被本场景占用。"))
        return []

    dialog = QDialog(parent)
    dialog.setWindowTitle(title)
    dialog.resize(560, 520)
    form = QFormLayout(dialog)
    form.addRow(tr("来源:"), picker)
    view_list = add_views_checklist_row(form, scene_key, [current_view])
    buttons = QDialogButtonBox(
        QDialogButtonBox.StandardButton.Ok
        | QDialogButtonBox.StandardButton.Cancel)
    apply_dialog_button_box_style(buttons)
    form.addRow(buttons)
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return []
    views = checklist_views_value(view_list, current_view)
    return [(source, entity, list(views)) for source, entity in picker.values()]


def prompt_reference_views(parent, scene_key: str,
                           current: list[str]) -> list[str] | None:
    """只改归属视图的小对话框；取消返回 None。"""
    from PyQt6.QtWidgets import QDialog, QDialogButtonBox

    from ..button_styles import apply_dialog_button_box_style

    dialog = QDialog(parent)
    dialog.setWindowTitle(tr("引用的归属视图"))
    form = QFormLayout(dialog)
    note = QLabel(tr("坐标、类型与名字都在源场景，这里只改它在本场景的哪些"
                      "视图下看得见。"))
    note.setWordWrap(True)
    note.setStyleSheet("color: palette(mid); font-size: 11px;")
    form.addRow(note)
    checklist = add_views_checklist_row(form, scene_key, list(current))
    buttons = QDialogButtonBox(
        QDialogButtonBox.StandardButton.Ok
        | QDialogButtonBox.StandardButton.Cancel)
    apply_dialog_button_box_style(buttons)
    form.addRow(buttons)
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return None
    return checklist_views_value(checklist, current[0] if current else "")


# ─── 转移目标（页面切换契约） ────────────────────────────

_NO_TRANSITION = "\u2014"   # 破折号表示"不产生页面切换"


class TransitionPicker(QWidget):
    """选择点击该 area 后到达的场景/视图。

    三级级联：**分组 → 场景 → 视图**。不平铺全部场景视图——29 个场景已经能凑出
    上百个组合，平铺的下拉框选起来找不到东西，而且场景只会越加越多。

    选定场景后视图默认基底；选「不跳转」时后两级禁用。

    只声明不驱动执行：现有 wf 与 Python 编排不读它。它的价值是让"点这个按钮
    会去哪"从散落在代码里变成可校验、可展示的契约。
    """

    _NONE = "\u2014"        # 分组栏的"不跳转"占位

    def __init__(self, current_scene: str, value: str = "", parent=None):
        super().__init__(parent)
        self._current_scene = current_scene
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        self._local_views_button = QPushButton(tr("本场景视图"))
        self._local_views_button.setToolTip(
            tr("快速选择当前场景内的目标视图"))
        apply_button_style(self._local_views_button, variant="neutral")
        self._group = QComboBox()
        self._scene = QComboBox()
        self._view = QComboBox()
        for combo, stretch in ((self._group, 2), (self._scene, 3),
                               (self._view, 2)):
            row.addWidget(combo, stretch)

        self._group.addItem(tr("不跳转"), userData=self._NONE)
        registry = get_registry()
        for gk, gname in registry.get_groups():
            self._group.addItem(gname, userData=gk)
        self._group.currentIndexChanged.connect(self._on_group_changed)
        self._scene.currentIndexChanged.connect(self._on_scene_changed)
        self._build_local_views_menu()
        self.set_value(value)

    # ─── 级联 ────────────────────────────────────────────

    def _build_local_views_menu(self) -> None:
        views = get_scene_views(self._current_scene)
        menu = QMenu(self._local_views_button)
        for view in views:
            action = menu.addAction(view.name)
            assert action is not None
            action.setData(view.key)
            action.triggered.connect(
                lambda _checked=False, key=view.key:
                self._select_local_view(key)
            )
        self._local_views_button.setMenu(menu)
        self._local_views_button.setEnabled(bool(views))

    def _select_local_view(self, view_key: str) -> None:
        registry = get_registry()
        group_key = next(
            (key for key, _name in registry.get_groups()
             if self._current_scene in registry.get_group_scenes(key)), None)
        group_index = self._group.findData(group_key) if group_key else -1
        if group_index < 0:
            return
        self._group.setCurrentIndex(group_index)
        scene_index = self._scene.findData(self._current_scene)
        if scene_index < 0:
            return
        self._scene.setCurrentIndex(scene_index)
        view_index = self._view.findData(view_key)
        if view_index >= 0:
            self._view.setCurrentIndex(view_index)

    def _on_group_changed(self, _index: int) -> None:
        gk = self._group.currentData()
        self._scene.blockSignals(True)
        self._scene.clear()
        enabled = gk != self._NONE
        if enabled:
            registry = get_registry()
            for scene_key in registry.get_group_scenes(gk):
                scene = registry.get_scene(scene_key)
                if scene is None or scene.is_subscene:
                    continue
                self._scene.addItem(f"{scene.name} ({scene_key})",
                                    userData=scene_key)
        self._scene.blockSignals(False)
        self._scene.setEnabled(enabled)
        self._on_scene_changed(0)

    def _on_scene_changed(self, _index: int) -> None:
        scene_key = self._scene.currentData()
        self._view.blockSignals(True)
        self._view.clear()
        views = get_scene_views(scene_key) if scene_key else []
        if views:
            for v in views:
                self._view.addItem(v.name, userData=v.key)
            self._view.setCurrentIndex(0)     # 选了场景默认基底
        elif scene_key:
            self._view.addItem(tr("单视图"), userData="")
        self._view.blockSignals(False)
        self._view.setEnabled(bool(views))

    # ─── 取值 / 赋值 ─────────────────────────────────────

    def set_value(self, value: str) -> None:
        text = str(value or "").strip()
        if not text:
            self._group.setCurrentIndex(0)
            self._on_group_changed(0)
            return
        scene_key, _, view_key = text.partition("/")
        scene_key = scene_key.strip() or self._current_scene
        registry = get_registry()
        group_key = next(
            (gk for gk, _ in registry.get_groups()
             if scene_key in registry.get_group_scenes(gk)), None)
        idx = self._group.findData(group_key) if group_key else -1
        self._group.setCurrentIndex(idx if idx >= 0 else 0)
        self._on_group_changed(0)
        idx = self._scene.findData(scene_key)
        if idx >= 0:
            self._scene.setCurrentIndex(idx)
            self._on_scene_changed(0)
        idx = self._view.findData(view_key.strip())
        if idx >= 0:
            self._view.setCurrentIndex(idx)

    def value(self) -> str:
        if self._group.currentData() == self._NONE:
            return ""
        scene_key = self._scene.currentData()
        if not scene_key:
            return ""
        view_key = self._view.currentData() or ""
        if scene_key == self._current_scene:
            # 本场景内跳转必须带视图：空串是"不跳转"，两者不能混。
            return f"/{view_key}" if view_key else ""
        # 跨场景且目标是基底时省掉 "/base"，与不带视图的写法归一到同一个串。
        # 否则没动过转移的 area 一经编辑就会把 to: bag_detail 改写成
        # to: bag_detail/base，制造纯噪声 diff。
        if not view_key or view_key == BASE_VIEW_KEY:
            return scene_key
        return f"{scene_key}/{view_key}"

    def set_transition_enabled(self, enabled: bool) -> None:
        """按实体是否可点击控制跳转；快捷按钮位于父表单，需单独同步。"""
        self.setEnabled(enabled)
        menu = self._local_views_button.menu()
        self._local_views_button.setEnabled(
            enabled and menu is not None and bool(menu.actions()))


def add_transition_row(form: QFormLayout, current_scene: str,
                       value: str = "") -> TransitionPicker:
    picker = TransitionPicker(current_scene, value)
    form.addRow(tr("跳转:"), picker)
    return picker
