"""袅袅之音清单窗口：按地区勾选常驻来源，记录每个角色已拿多少枚。

记录写 ``users/<用户名>.linger.json``；勾选同时按差额入账到 Profile（开关可关），
细节见 ``docs/20-plans/02-profile/04-linger-checklist.md``。

「每一枚对应一个具体对手」的条目（``named``）点行即可原地展开子项，逐位勾选并填
名字——不另开窗口，展开后仍能对照上下条目。

窗口是非模态的，由菜单持有实例（与「智能调律」一致）：记录是随手翻看的操作，
不该像配置对话框那样阻塞主界面。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from loguru import logger
from PyQt6.QtCore import QItemSelectionModel, QPointF, QRect, Qt, QTimer
from PyQt6.QtGui import QColor, QPainter, QPalette, QPolygonF
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSplitter,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .....i18n import tr, tr_label
from .....ui.widgets.button_styles import apply_button_style
from .....ui.widgets.combo_box import AutoWidthComboBox, ComboWidthMode
from .....ui.widgets.layout_helpers import (
    configure_navigation_list,
    mark_navigation_list,
)
from ...core.linger import (
    ITEM_ROSTER,
    ITEM_SINGLE,
    ItemSlot,
    LingerCatalog,
    LingerItem,
    LingerRegion,
    LingerState,
    LingerStore,
    booking,
    item_key,
    load_effective_catalog,
    now_iso,
    overrides,
)

COL_REGION = 0
COL_CATEGORY = 1
COL_ITEM = 2
COL_COUNT = 3
COL_DONE = 4
COL_KIND = 5
COL_DETAIL = 6
COL_TIME = 7

_ALL_REGIONS = ""
_MUTED_STYLE = "color: palette(mid); font-size: 11px;"
#: 折叠三角的绘制参数（行首缩进 / 文字起点 / 三角半宽）
_ARROW_INDENT = 10
_ARROW_TEXT_GAP = 20
_ARROW_HALF = 4


class _ExpandArrowDelegate(QStyledItemDelegate):
    """在「条目」列行首画折叠三角（展开朝下、收起朝右，两态大小与颜色一致）。

    自己画而不是用 ``▸``/``▶`` 字符：这些字形在微软雅黑里没有，靠字体回退在部分
    机器上会渲染成方块；手画顺带保证两种状态完全对称，也跟着主题变色。
    """

    def __init__(self, dialog: "LingerDialog") -> None:
        super().__init__(dialog.table)
        self._dialog = dialog

    def paint(self, painter, option, index):  # noqa: N802 — Qt 接口
        ref = self._dialog.row_ref(index.row())
        if ref is None or ref.slot is not None or not ref.item.named:
            super().paint(painter, option, index)
            return
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        label = opt.text
        opt.text = ""  # 背景照常画，文字我们自己排，好给三角让位
        style = opt.widget.style() if opt.widget is not None else QApplication.style()
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, opt.widget)
        rect = opt.rect
        if rect.width() <= _ARROW_TEXT_GAP:
            return
        selected = bool(opt.state & QStyle.StateFlag.State_Selected)
        colour = opt.palette.color(
            QPalette.ColorRole.HighlightedText if selected else QPalette.ColorRole.Text)

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(colour))
        cx = float(rect.left() + _ARROW_INDENT)
        cy = float(rect.center().y()) + 0.5
        half = float(_ARROW_HALF)
        if self._dialog.is_expanded(ref):
            points = [
                QPointF(cx - half, cy - half + 1), QPointF(cx + half, cy - half + 1),
                QPointF(cx, cy + half),
            ]
        else:
            points = [
                QPointF(cx - half + 1, cy - half), QPointF(cx - half + 1, cy + half),
                QPointF(cx + half + 1, cy),
            ]
        painter.drawPolygon(QPolygonF(points))

        painter.setFont(opt.font)
        text_rect = QRect(rect.left() + _ARROW_TEXT_GAP, rect.top(),
                          rect.width() - _ARROW_TEXT_GAP - 4, rect.height())
        metrics = painter.fontMetrics()
        elided = metrics.elidedText(label, Qt.TextElideMode.ElideRight, text_rect.width())
        painter.setPen(QColor(colour))
        painter.drawText(text_rect,
                         Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                         elided)
        painter.restore()


def _excerpt(text: str, limit: int = 22) -> str:
    """详情列只放第一行摘要，超出用省略号；全文在双击后的窗口里看。"""
    body = " ".join(line.strip() for line in str(text or "").splitlines() if line.strip())
    if not body:
        return ""
    return body if len(body) <= limit else body[: limit - 1] + "…"


def _format_time(value: str) -> str:
    """UTC ISO → 本地 ``MM-DD HH:MM``；解析不了就原样返回，不吞成空白。"""
    if not value:
        return ""
    try:
        return datetime.fromisoformat(value).astimezone().strftime("%m-%d %H:%M")
    except ValueError:
        return value


@dataclass(frozen=True)
class _RowRef:
    """表格行对应的对象：``slot`` 为 ``None`` 表示条目行，否则是它的第几位子项。"""

    region: LingerRegion
    item: LingerItem
    slot: int | None = None


class LingerDialog(QDialog):
    """清单主窗口。切换用户只影响本窗口，不改主界面的当前用户。"""

    def __init__(self, host, parent: QWidget | None = None):
        super().__init__(parent if parent is not None else host)
        self._host = host
        self._catalog = LingerCatalog(())
        self._store: LingerStore | None = None
        self._state = LingerState()
        self._rows: dict[int, _RowRef] = {}
        self._region = _ALL_REGIONS
        self._expanded: set[str] = set()
        self._updating = False
        self.setWindowTitle(tr("袅袅之音"))
        self.setModal(False)
        self.setMinimumSize(900, 560)
        self.resize(1080, 720)
        self._build_ui()
        self._connect_host()
        self.reload()

    # ─── 构建 ────────────────────────────────────────────────

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 16, 16, 12)
        outer.setSpacing(12)

        header = QHBoxLayout()
        header.setSpacing(8)
        header.addWidget(QLabel(tr_label("用户")))
        self.user_combo = AutoWidthComboBox(
            self, width_mode=ComboWidthMode.POPUP, minimum_width=150)
        self.user_combo.currentIndexChanged.connect(self._on_user_changed)
        header.addWidget(self.user_combo)
        header.addSpacing(12)
        header.addWidget(QLabel(tr_label("同步模型")))
        self.target_combo = QComboBox(self)
        self.target_combo.setMinimumWidth(180)
        self.target_combo.currentIndexChanged.connect(self._on_default_target_changed)
        header.addWidget(self.target_combo)
        header.addStretch(1)
        # 默认关闭：点一下只记录，记账必须是显式选择
        self.booking_switch = QCheckBox(tr("同步记账"))
        self.booking_switch.setChecked(False)
        self.booking_switch.toggled.connect(self._on_booking_switch_toggled)
        header.addWidget(self.booking_switch)
        for label, name, callback, variant in (
            (tr("新增"), "add_button", self._create_item, "neutral"),
            (tr("编辑"), "edit_button", self._edit_item, "neutral"),
            (tr("删除"), "delete_button", self._delete_item, "danger"),
            (tr("批量绑定"), "batch_button", self._batch_bind, "neutral"),
            (tr("管理分类"), "category_button", self._manage_categories, "neutral"),
        ):
            button = QPushButton(label)
            apply_button_style(button, variant=variant)
            button.clicked.connect(callback)
            button.setEnabled(False)
            setattr(self, name, button)
            header.addWidget(button)
        self.refresh_button = QPushButton(tr("刷新"))
        apply_button_style(self.refresh_button, variant="neutral")
        self.refresh_button.clicked.connect(self.reload)
        header.addWidget(self.refresh_button)
        outer.addLayout(header)

        notice = QFrame()
        notice.setProperty("surface", "card")
        notice_layout = QVBoxLayout(notice)
        notice_layout.setContentsMargins(12, 8, 12, 8)
        notice_layout.setSpacing(4)
        summary_row = QHBoxLayout()
        summary_row.setSpacing(12)
        self.summary = QLabel("")
        summary_row.addWidget(self.summary)
        summary_row.addStretch(1)
        self.booked_label = QLabel("")
        self.booked_label.setStyleSheet(_MUTED_STYLE)
        summary_row.addWidget(self.booked_label)
        notice_layout.addLayout(summary_row)
        hint = QLabel(tr("勾选即保存；双击「详情」列看全文，多枚条目双击「已得」填数，"
                         "行首带三角的条目点一下就地展开逐位记录。"))
        hint.setWordWrap(True)
        hint.setStyleSheet(_MUTED_STYLE)
        notice_layout.addWidget(hint)
        outer.addWidget(notice)

        self.splitter = QSplitter()
        self.splitter.setChildrenCollapsible(False)
        outer.addWidget(self.splitter, 1)

        self.region_list = QListWidget()
        configure_navigation_list(self.region_list, minimum_width=120)
        # 左栏只为放下文字而存在，永远不需要横向滚动条
        self.region_list.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.region_list.currentItemChanged.connect(self._on_region_changed)
        self.splitter.addWidget(self.region_list)

        self.table = QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels([
            tr("地区"), tr("类型"), tr("条目"), tr("枚数"), tr("已得"),
            tr("记法"), tr("详情"), tr("更新时间"),
        ])
        header_view = self.table.horizontalHeader()
        assert header_view is not None
        header_view.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header_view.setHighlightSections(False)
        # 数字列居中：表头与单元格对齐同一基准，扫读时不会左右跳。
        for column in (COL_COUNT, COL_DONE, COL_KIND, COL_TIME):
            header_cell = self.table.horizontalHeaderItem(column)
            if header_cell is not None:
                header_cell.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        vertical = self.table.verticalHeader()
        assert vertical is not None
        vertical.setVisible(False)
        vertical.setDefaultSectionSize(
            max(28, self.table.fontMetrics().height() + 10))
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.setWordWrap(False)
        # 允许多选：批量绑定时按选中的行一次改完
        self.table.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        # 只有「已得」与子项名字可编辑：单击勾选/展开，双击填数。
        self.table.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked
            | QAbstractItemView.EditTrigger.EditKeyPressed)
        self.table.itemChanged.connect(self._on_item_changed)
        self.table.cellClicked.connect(self._on_cell_clicked)
        self.table.cellDoubleClicked.connect(self._on_cell_double_clicked)
        self.table.itemSelectionChanged.connect(self._update_actions)
        self.table.setItemDelegateForColumn(COL_ITEM, _ExpandArrowDelegate(self))
        # 条目间拖动调整顺序：按下即可拖，行间显示插入线，松手写入 __order__。
        # 只在条目行之间有效；子项行、跨地区拖动都忽略。
        self._drag_row = -1
        self.setAcceptDrops(True)
        self.table.setDragEnabled(True)
        self.table.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.table.setDropIndicatorShown(True)
        self.table.dragEnterEvent = self._drag_enter  # type: ignore[assignment]
        self.table.dragMoveEvent = self._drag_move  # type: ignore[assignment]
        self.table.dropEvent = self._drop  # type: ignore[method-assign]
        self.splitter.addWidget(self.table)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)

        bottom = QHBoxLayout()
        self.status = QLabel("")
        self.status.setStyleSheet(_MUTED_STYLE)
        bottom.addWidget(self.status)
        bottom.addStretch(1)
        close_button = QPushButton(tr("关闭"))
        apply_button_style(close_button, variant="neutral")
        close_button.clicked.connect(self.close)
        bottom.addWidget(close_button)
        outer.addLayout(bottom)

    def _connect_host(self) -> None:
        """跟随主界面切用户；窗口没打开时不用重建。"""
        signal = getattr(self._host, "user_changed", None)
        if signal is not None:
            signal.connect(self._on_host_user_changed)

    # ─── 数据 ────────────────────────────────────────────────

    def reload(self) -> None:
        """完整重置：窗口初次打开与手动「刷新」用。"""
        self._region = _ALL_REGIONS
        self._expanded.clear()
        self._refresh_data()
        self._refresh_region_list()

    def _refresh_data(self) -> None:
        """只换数据不动视图：保留地区筛选、选中、展开与滚动位置。

        编辑/删除/新增/批量绑定之后的重载都走这里——用户正在看的上下文
        不该因为保存一次就被扔回「全部」。
        """
        focused_key = self._selected_item_key()
        scroll = self.table.verticalScrollBar()
        offset = scroll.value() if scroll is not None else 0
        self._catalog = load_effective_catalog()
        self._reload_users()
        self._reload_targets()
        self._load_user_state()
        self._refresh_region_list()
        self._fill_table()
        self._refresh_summary()
        if focused_key:
            self._select_item(focused_key)
        if scroll is not None:
            scroll.setValue(min(offset, scroll.maximum()))
        self._update_actions()

    def _reload_users(self) -> None:
        names: list[str] = []
        combo = getattr(self._host, "user_combo", None)
        if combo is not None:
            names = [combo.itemText(i) for i in range(combo.count())]
        if not names:
            active_name = getattr(self._host, "active_user_name", None)
            active = str(active_name() or "") if callable(active_name) else ""
            if active:
                names = [active]
        current = self.user_combo.currentText()
        self._updating = True
        self.user_combo.clear()
        self.user_combo.addItems(names)
        target = current if current in names else ""
        if not target:
            preferred = getattr(self._host, "active_user_name", None)
            preferred_name = str(preferred() or "") if callable(preferred) else ""
            target = preferred_name if preferred_name in names else (names[0] if names else "")
        index = self.user_combo.findText(target)
        if index >= 0:
            self.user_combo.setCurrentIndex(index)
        self.user_combo.setEnabled(bool(names))
        self._updating = False

    def _current_user(self) -> str:
        return self.user_combo.currentText().strip()

    def _load_user_state(self) -> None:
        username = self._current_user()
        if not username:
            self._store = None
            self._state = LingerState()
            self.table.setEnabled(False)
            self._sync_booking_switch()
            self._update_actions()
            return
        self._store = LingerStore(username)
        self._state = self._store.load()
        self.table.setEnabled(True)
        self._sync_booking_switch()
        self._update_actions()

    def _sync_booking_switch(self) -> None:
        """把该用户的「同步记账」状态回显到开关上（不触发写入）。"""
        username = self._current_user()
        enabled = bool(username) and booking.booking_enabled(username)
        self._updating = True
        self.booking_switch.setChecked(enabled)
        self.booking_switch.setEnabled(bool(username))
        self._updating = False

    def _on_booking_switch_toggled(self, checked: bool) -> None:
        if self._updating:
            return
        username = self._current_user()
        if not username:
            return
        booking.set_booking_enabled(username, checked)
        self._refresh_summary()

    @property
    def _booking_enabled(self) -> bool:
        username = self._current_user()
        return bool(username) and booking.booking_enabled(username)

    def _reload_targets(self) -> None:
        """同步模型下拉：固定/逐位条目共用的入账目标。"""
        from .item_dialog import target_choices

        current = overrides.read_default_target()
        self._updating = True
        self.target_combo.clear()
        for value, label in target_choices(booking.MODEL_STOCK):
            self.target_combo.addItem(label, value)
        index = self.target_combo.findData(current)
        if index < 0:
            self.target_combo.addItem(current, current)
            index = self.target_combo.count() - 1
        self.target_combo.setCurrentIndex(index)
        self._updating = False

    def _on_default_target_changed(self, _index: int) -> None:
        if self._updating:
            return
        target = str(self.target_combo.currentData() or "")
        if not target:
            return
        overrides.set_default_target(target)
        self._catalog = load_effective_catalog()
        self._refresh_region_list()
        self._fill_table()
        self._refresh_summary()

    def _on_user_changed(self, _index: int) -> None:
        if self._updating:
            return
        self._expanded.clear()
        self._load_user_state()
        self._refresh_region_list()
        self._fill_table()
        self._refresh_summary()

    def _on_host_user_changed(self, _username: str) -> None:
        self._reload_users()
        self._expanded.clear()
        self._load_user_state()
        self._refresh_region_list()
        self._fill_table()
        self._refresh_summary()

    # ─── 左栏 ────────────────────────────────────────────────

    def _refresh_region_list(self) -> None:
        selected = self._region
        self._updating = True
        self.region_list.clear()
        all_item = QListWidgetItem(tr("全部"))
        all_item.setData(Qt.ItemDataRole.UserRole, _ALL_REGIONS)
        self.region_list.addItem(all_item)
        for region in self._catalog.regions:
            done = sum(
                self._state.resolve_done(item_key(region.id, item.id), item)
                for item in region.items)
            region_entry = QListWidgetItem(f"{region.label}  {done}/{region.fixed_total}")
            region_entry.setData(Qt.ItemDataRole.UserRole, region.id)
            region_entry.setToolTip(region.label)
            self.region_list.addItem(region_entry)
        found = False
        for row in range(self.region_list.count()):
            entry = self.region_list.item(row)
            if entry is None:
                continue
            if entry.data(Qt.ItemDataRole.UserRole) == selected:
                self.region_list.setCurrentRow(row)
                found = True
                break
        if not found:
            self.region_list.setCurrentRow(0)
            self._region = _ALL_REGIONS
        self._updating = False
        self._fit_navigation_width()

    def _fit_navigation_width(self) -> None:
        """左栏按内容实宽分配：够放下最长的「地区 + 已得/总数」就行，不预设宽窄。"""
        mark_navigation_list(self.region_list)
        metrics = self.region_list.fontMetrics()
        width = 0
        for row in range(self.region_list.count()):
            entry = self.region_list.item(row)
            if entry is not None:
                width = max(width, metrics.horizontalAdvance(entry.text() or ""))
        # 条目左右内边距 20 + 列表内边距 8 + 竖向滚动条 18 + 边框，都算进去才不会被截断
        needed = width + 48
        self.region_list.setMinimumWidth(needed)
        self.splitter.setSizes([needed, max(320, self.splitter.width() - needed)])

    def _on_region_changed(self, current: QListWidgetItem | None, _previous) -> None:
        if self._updating or current is None:
            return
        self._region = str(current.data(Qt.ItemDataRole.UserRole) or _ALL_REGIONS)
        self._fill_table()

    # ─── 表格 ────────────────────────────────────────────────

    def _visible_regions(self) -> list[LingerRegion]:
        if self._region:
            region = self._catalog.region(self._region)
            return [region] if region else []
        return list(self._catalog.regions)

    # ─── 列宽 ────────────────────────────────────────────────

    #: 每列的（最小, 上限）宽度。内容本身长度稳定，先按内容量出实际所需，
    #: 再把窗口富余按比例摊给所有列——不让某一列独占空白。
    _COLUMN_LIMITS: dict[int, tuple[int, int]] = {
        COL_REGION: (84, 120),
        COL_CATEGORY: (76, 120),
        COL_ITEM: (190, 330),
        COL_COUNT: (54, 76),
        COL_DONE: (62, 96),
        COL_KIND: (56, 72),
        COL_DETAIL: (100, 180),
        COL_TIME: (104, 128),
    }
    _HEADER_PADDING = 24
    _CELL_PADDING = 16

    def _text_width(self, text: str) -> int:
        return self.table.fontMetrics().horizontalAdvance(text or "")

    def _content_width(self, column: int) -> int:
        header_cell = self.table.horizontalHeaderItem(column)
        width = self._text_width(header_cell.text() if header_cell else "")
        width += self._HEADER_PADDING
        for row in range(self.table.rowCount()):
            cell = self.table.item(row, column)
            if cell is not None:
                width = max(width, self._text_width(cell.text()) + self._CELL_PADDING)
        return width

    def _apply_column_widths(self) -> None:
        if self.table.rowCount() == 0:
            return
        widths: dict[int, int] = {}
        for column, (low, high) in self._COLUMN_LIMITS.items():
            raw = self._content_width(column)
            if column == COL_ITEM:
                raw += _ARROW_TEXT_GAP  # 给折叠三角留位
            widths[column] = max(low, min(high, raw))
        viewport_widget = self.table.viewport()
        viewport = viewport_widget.width() if viewport_widget is not None else 0
        spare = viewport - sum(widths.values())
        if spare > 0 and viewport > 0:
            total = sum(widths.values())
            for column, width in widths.items():
                widths[column] = width + int(spare * width / total)
        elif spare < 0:
            # 放不下时先让步长文本列（详情 → 来源 → 条目），别把数字列挤掉
            overflow = -spare
            for column in (COL_DETAIL, COL_ITEM):
                if overflow <= 0:
                    break
                low = self._COLUMN_LIMITS[column][0]
                take = min(widths[column] - low, overflow)
                if take > 0:
                    widths[column] -= take
                    overflow -= take
        for column, width in widths.items():
            self.table.setColumnWidth(column, width)

    def resizeEvent(self, event) -> None:  # noqa: N802 — Qt 接口
        super().resizeEvent(event)
        self._apply_column_widths()

    def _fill_table(self) -> None:
        """重建表格，保住滚动位置与选中目标。

        展开/收起、切换过滤都会整表重建；之前重建后 currentRow 归 -1，
        「编辑/删除」跟着灰掉——用户点了一下展开，选中就没了。
        """
        scroll = self.table.verticalScrollBar()
        offset = scroll.value() if scroll is not None else 0
        focused_key = self._selected_item_key()
        self._updating = True
        self.table.setRowCount(0)
        self._rows.clear()
        if not self._catalog.regions:
            self.status.setText(tr("清单未安装"))
            self._updating = False
            return
        self.status.setText("")
        for region in self._visible_regions():
            for item in region.items:
                self._append_item_row(region, item)
                if item.named and item_key(region.id, item.id) in self._expanded:
                    for index in range(item.count):
                        self._append_slot_row(region, item, index)
        self._updating = False
        self._apply_column_widths()
        if scroll is not None:
            scroll.setValue(min(offset, scroll.maximum()))
        if focused_key:
            self._select_item(focused_key)

    def _selected_item_key(self) -> str | None:
        """当前选中行对应的条目键（子项行归到父条目）。"""
        ref = self._current_ref()
        if ref is None:
            return None
        return item_key(ref.region.id, ref.item.id)

    def _select_item(self, key: str) -> None:
        """按条目键恢复选中（子项行展开后不占位，选回父行）。"""
        for row, ref in self._rows.items():
            if ref.slot is None and item_key(ref.region.id, ref.item.id) == key:
                self.table.setCurrentCell(
                    row, COL_ITEM,
                    QItemSelectionModel.SelectionFlag.SelectCurrent
                    | QItemSelectionModel.SelectionFlag.Rows)
                return

    def _append_item_row(self, region: LingerRegion, item: LingerItem) -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)
        self._rows[row] = _RowRef(region, item)
        key = item_key(region.id, item.id)
        username = self._current_user()
        if item.periodic:
            done, cap = (booking.periodic_state(username, item.target)
                         if username else (0, item.count))
            shown_count = cap or item.count
        else:
            done = self._state.resolve_done(key, item)
            shown_count = item.count

        region_cell = QTableWidgetItem(region.label)
        region_cell.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
        self.table.setItem(row, COL_REGION, region_cell)

        category_cell = QTableWidgetItem(item.category)
        category_cell.setFlags(
            Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
        self.table.setItem(row, COL_CATEGORY, category_cell)

        item_cell = QTableWidgetItem(item.label)
        if item.kind == ITEM_SINGLE:
            item_cell.setFlags(
                Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
                | Qt.ItemFlag.ItemIsUserCheckable)
            item_cell.setCheckState(
                Qt.CheckState.Checked if done >= shown_count
                else Qt.CheckState.Unchecked)
        else:
            # 逐位与周期都没有「勾一半」的语义，不用勾选框
            item_cell.setFlags(
                Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
        self.table.setItem(row, COL_ITEM, item_cell)

        count_cell = QTableWidgetItem(str(shown_count))
        count_cell.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
        count_cell.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        self.table.setItem(row, COL_COUNT, count_cell)

        if item.named:
            text = f"{done}/{shown_count}"
            flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        else:
            text = str(done)
            flags = (Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
                     | Qt.ItemFlag.ItemIsEditable)
        done_cell = QTableWidgetItem(text)
        done_cell.setFlags(flags)
        done_cell.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        self.table.setItem(row, COL_DONE, done_cell)

        kind_cell = QTableWidgetItem(
            tr("逐位") if item.kind == ITEM_ROSTER
            else tr("周期") if item.periodic else tr("固定"))
        kind_cell.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
        kind_cell.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        self.table.setItem(row, COL_KIND, kind_cell)

        detail_cell = QTableWidgetItem(_excerpt(item.detail))
        detail_cell.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
        detail_cell.setToolTip(
            f"{item.detail}\n\n{tr('双击查看完整详情')}" if item.detail
            else tr("双击查看完整详情"))
        self.table.setItem(row, COL_DETAIL, detail_cell)

        time_cell = QTableWidgetItem(
            "" if item.periodic else _format_time(self._progress_at(key)))
        time_cell.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
        self.table.setItem(row, COL_TIME, time_cell)

    def _append_slot_row(self, region: LingerRegion, item: LingerItem,
                         index: int) -> None:
        """展开后的子项行：序号 / 已得勾选 / 名字可编辑。"""
        row = self.table.rowCount()
        self.table.insertRow(row)
        self._rows[row] = _RowRef(region, item, index)
        slots = self._state.slot_view(item_key(region.id, item.id), item.count)
        slot = slots[index] if index < len(slots) else ItemSlot()

        index_cell = QTableWidgetItem(tr("第 {index} 位").format(index=index + 1))
        index_cell.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
        self.table.setItem(row, COL_REGION, index_cell)

        name_cell = QTableWidgetItem(slot.name)
        name_cell.setFlags(
            Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
            | Qt.ItemFlag.ItemIsEditable)
        self.table.setItem(row, COL_ITEM, name_cell)

        empty_count = QTableWidgetItem("")
        empty_count.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
        self.table.setItem(row, COL_COUNT, empty_count)

        done_cell = QTableWidgetItem("")
        done_cell.setFlags(
            Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
            | Qt.ItemFlag.ItemIsUserCheckable)
        done_cell.setCheckState(
            Qt.CheckState.Checked if slot.done else Qt.CheckState.Unchecked)
        done_cell.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        self.table.setItem(row, COL_DONE, done_cell)

        for column in (COL_CATEGORY, COL_KIND, COL_DETAIL):
            empty = QTableWidgetItem("")
            empty.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            self.table.setItem(row, column, empty)

        time_cell = QTableWidgetItem(_format_time(slot.at))
        time_cell.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
        self.table.setItem(row, COL_TIME, time_cell)

    def _progress_at(self, key: str) -> str:
        progress = self._state.items.get(key)
        return progress.at if progress else ""

    # ─── 增删改 ──────────────────────────────────────────────

    def _current_ref(self) -> _RowRef | None:
        row = self.table.currentRow()
        return self._rows.get(row) if row >= 0 else None

    def _update_actions(self) -> None:
        has_list = bool(self._catalog.regions)
        has_user = bool(self._current_user())
        ref = self._current_ref()
        editable = (has_user and ref is not None and ref.slot is None
                    and has_list)
        selected_count = len(self._selected_refs())
        self.add_button.setEnabled(has_user and has_list)
        self.edit_button.setEnabled(editable)
        self.delete_button.setEnabled(editable)
        # 批量 = 对多条统一改绑定，天然需要 ≥2；禁用时说明原因，不让用户猜
        self.batch_button.setEnabled(has_user and selected_count >= 2)
        self.batch_button.setToolTip(
            "" if selected_count >= 2 else tr("选中两条以上条目后可用"))
        # 管理分类作用于全清单，与选中无关
        self.category_button.setEnabled(has_list)

    def _selected_refs(self) -> list[_RowRef]:
        """表格里选中的条目行（子项行不算）。"""
        model = self.table.selectionModel()
        if model is None:
            return []
        rows = sorted({index.row() for index in model.selectedIndexes()})
        refs: list[_RowRef] = []
        for row in rows:
            ref = self._rows.get(row)
            if ref is not None and ref.slot is None:
                refs.append(ref)
        return refs

    def _batch_bind(self) -> None:
        from .batch_target_dialog import LingerBatchTargetDialog

        refs = self._selected_refs()
        if len(refs) < 2:
            return
        current = refs[0].item.target if refs[0].item.periodic else ""
        dialog = LingerBatchTargetDialog(len(refs), current, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        keys = [item_key(ref.region.id, ref.item.id) for ref in refs]
        overrides.set_batch_target(keys, dialog.target())
        # 绑定/解绑会改记法（逐位↔固定），展开的子项行不再成立
        self._expanded.clear()
        self._refresh_data()

    def _manage_categories(self) -> None:
        from .category_dialog import LingerCategoryDialog

        LingerCategoryDialog(self).exec()
        self._refresh_data()

    def _create_item(self) -> None:
        from .item_dialog import LingerItemDialog

        region_id = self._region or (self._catalog.regions[0].id
                                     if self._catalog.regions else "")
        dialog = LingerItemDialog(list(self._catalog.regions),
                                  region_id=region_id, parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        values = dialog.values()
        if not values.get("label"):
            return
        key = overrides.add_item(dialog.region_id(), values)
        self._refresh_data()
        self._select_item(key)

    def _edit_item(self) -> None:
        from .item_dialog import LingerItemDialog

        ref = self._current_ref()
        if ref is None or ref.slot is not None:
            return
        dialog = LingerItemDialog(list(self._catalog.regions), item=ref.item,
                                  region_id=ref.region.id, parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        overrides.upsert_item(item_key(ref.region.id, ref.item.id), dialog.values())
        self._refresh_data()

    def _delete_item(self) -> None:
        ref = self._current_ref()
        if ref is None or ref.slot is not None:
            return
        answer = QMessageBox.question(
            self, tr("删除"),
            tr("删除后列表里不再显示这一条，已记录的历史保留。"))
        if answer != QMessageBox.StandardButton.Yes:
            return
        overrides.remove_item(item_key(ref.region.id, ref.item.id))
        self._refresh_data()

    def row_ref(self, row: int) -> _RowRef | None:
        """行号 → 该行对应的条目或子项；供折叠三角的绘制委托读取。"""
        return self._rows.get(row)

    def is_expanded(self, ref: _RowRef) -> bool:
        return item_key(ref.region.id, ref.item.id) in self._expanded

    # ─── 交互 ────────────────────────────────────────────────

    def _on_cell_clicked(self, row: int, column: int) -> None:
        """只有点「条目」列才展开/收起子项；其它列就是普通选中。"""
        ref = self._rows.get(row)
        if ref is None or ref.slot is not None or not ref.item.named:
            return
        if column != COL_ITEM:
            return
        key = item_key(ref.region.id, ref.item.id)
        if key in self._expanded:
            self._expanded.discard(key)
        else:
            self._expanded.add(key)
        # 重建表格会把当前被点击的行一起销毁，延迟到事件返回后再做。
        QTimer.singleShot(0, self._fill_table)

    # ─── 拖动排序 ──────────────────────────────────────────────

    def _drag_enter(self, event) -> None:
        """只接受本表格发起的内部移动；外来拖放（文件等）一律忽略。"""
        if event.source() is self.table:
            event.acceptProposedAction()
        else:
            event.ignore()

    def _drag_move(self, event) -> None:
        if event.source() is self.table:
            event.acceptProposedAction()
        else:
            event.ignore()

    def _drag_source_key(self) -> str | None:
        row = self.table.currentRow()
        ref = self._rows.get(row)
        if ref is None or ref.slot is not None:
            return None
        return item_key(ref.region.id, ref.item.id)

    def _drop(self, event) -> None:
        """松手：把拖动条目挪到落点条目前面（地区末尾则传 None）。

        落在子项行上按其父条目算；跨地区、拖到自己身上都直接忽略，不重载。
        """
        if event.source() is not self.table:
            event.ignore()
            return
        source_key = self._drag_source_key()
        if source_key is None:
            event.ignore()
            return
        pos = event.position().toPoint()
        target_row = self.table.rowAt(pos.y())
        ref = self._rows.get(target_row)
        if ref is None:
            event.ignore()
            return
        target_key = item_key(ref.region.id, ref.item.id)
        region_a, _, _ = source_key.partition("/")
        region_b, _, _ = target_key.partition("/")
        if region_a != region_b:
            event.ignore()
            return

        below = pos.y() > self.table.visualItemRect(
            self.table.item(target_row, COL_ITEM)).center().y()
        if below:
            # 落点在行下半：插到下一条目行前面；本地区最后一条则挪到末尾
            nxt = self._next_item_row(target_row)
            before_key = (item_key(self._rows[nxt].region.id, self._rows[nxt].item.id)
                          if nxt is not None else None)
        else:
            before_key = target_key if target_key != source_key else None
        if before_key == source_key:
            event.ignore()
            return
        if overrides.move_item(source_key, before_key):
            event.acceptProposedAction()
            self._expanded.clear()
            self._catalog = load_effective_catalog()
            self._refresh_region_list()
            self._fill_table()
            self._refresh_summary()
            if before_key:
                self._select_item(before_key)
        else:
            event.ignore()

    def _next_item_row(self, row: int) -> int | None:
        """某行之后的下一个条目行（跳过子项行）；没有则 None。"""
        for candidate in range(row + 1, self.table.rowCount()):
            ref = self._rows.get(candidate)
            if ref is not None and ref.slot is None:
                return candidate
        return None

    def _on_cell_double_clicked(self, row: int, column: int) -> None:
        """只有「详情」列双击才开详情窗口；其它列保持单元格自己的语义。"""
        if column != COL_DETAIL:
            return
        ref = self._rows.get(row)
        if ref is not None:
            self._show_detail(ref)

    def _show_detail(self, ref: _RowRef) -> None:
        from .detail_dialog import LingerDetailDialog

        dialog = LingerDetailDialog(ref.region, ref.item, self)
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _on_item_changed(self, cell: QTableWidgetItem) -> None:
        if self._updating:
            return
        ref = self._rows.get(cell.row())
        if ref is None:
            return
        if ref.slot is None:
            if ref.item.named:
                return  # 逐位条目由子项决定，主行不直接改
            if cell.column() == COL_ITEM:
                if ref.item.periodic:
                    return  # 周期型没有「勾满」语义，改「已得」即可
                done = (ref.item.count
                        if cell.checkState() == Qt.CheckState.Checked else 0)
                self._commit(ref.region, ref.item, done)
            elif cell.column() == COL_DONE:
                self._commit(ref.region, ref.item, self._parse_done(cell.text()))
        else:
            if cell.column() == COL_DONE:
                self._update_slot(ref, done=cell.checkState() == Qt.CheckState.Checked)
            elif cell.column() == COL_ITEM:
                self._update_slot(ref, name=cell.text())

    @staticmethod
    def _parse_done(text: str) -> int:
        """非法输入按 0 处理：写入时再按条目枚数收紧，页面回显真实值。"""
        try:
            return int(str(text).strip())
        except (TypeError, ValueError):
            return 0

    def _update_slot(self, ref: _RowRef, *, done: bool | None = None,
                     name: str | None = None) -> None:
        if self._store is None or ref.slot is None:
            return
        key = item_key(ref.region.id, ref.item.id)
        slots = self._state.slot_view(key, ref.item.count)
        if ref.slot >= len(slots):
            return
        current = slots[ref.slot]
        done_value = current.done if done is None else done
        name_value = current.name if name is None else name.strip()
        # 名字与已得是两件事：填名字只记名字，是否拿到由用户勾选，不代做决定
        slots[ref.slot] = ItemSlot(
            done=done_value,
            name=name_value,
            at=current.at or (now_iso() if done_value else ""),
        )
        try:
            self._state = self._store.set_slots(key, slots, ref.item.count)
        except Exception as exc:
            logger.error(f"保存袅袅之音子项失败 {self._store.username}.{key}：{exc}")
            self._set_status(tr("保存失败"), error=True)
            self._refresh_item(ref.region, ref.item)
            return
        self._set_status("")
        self._settle_booking(ref.region, ref.item)
        self._refresh_item(ref.region, ref.item)
        self._refresh_region_labels(ref.region)
        self._refresh_summary()

    def _commit(self, region: LingerRegion, item: LingerItem, value: int) -> None:
        if self._store is None:
            return
        if item.periodic:
            self._commit_periodic(region, item, value)
            return
        key = item_key(region.id, item.id)
        booked = self._state.booked(key)
        # 改小到低于已入账数量时先冲销账本：账本不能凭空多出来，
        # 冲销失败就这次改动整体不生效，避免「记录少了、账本没动」。
        if value < booked:
            error = self._reverse_booking(region, item, booked, value)
            if error is not None:
                self._set_status(tr("保存失败"), error=True)
                self._refresh_item(region, item)
                return
        try:
            self._state = self._store.set_done(key, value, item.count)
        except Exception as exc:  # 写盘失败要让人看见，不能静默
            logger.error(f"保存袅袅之音记录失败 {self._store.username}.{key}：{exc}")
            self._set_status(tr("保存失败"), error=True)
            self._refresh_item(region, item)
            return
        self._set_status("")
        self._settle_booking(region, item)
        self._refresh_item(region, item)
        self._refresh_region_labels(region)
        self._refresh_summary()

    def _commit_periodic(self, region: LingerRegion, item: LingerItem,
                         value: int) -> None:
        """周期型：直接改配额 key 的值，记录文件里一条都不留。

        「已得」就是配额本周期当前值，跨周期自动归零，所以这里只把差额写进账本。
        """
        if self._store is None:
            return
        current, cap = booking.periodic_state(self._store.username, item.target)
        target = max(0, min(value, cap)) if cap else max(0, value)
        delta = target - current
        if delta:
            error = booking.write_delta(
                self._store.username, item.target, delta, item.source)
            if error is not None:
                self._set_status(tr("保存失败"), error=True)
                self._refresh_item(region, item)
                return
        self._set_status("")
        self._refresh_item(region, item)
        self._refresh_region_labels(region)
        self._refresh_summary()

    def _reverse_booking(self, region: LingerRegion, item: LingerItem,
                         booked: int, target: int) -> str | None:
        """把账本冲销到 ``target`` 枚；成功返回 ``None``。"""
        if self._store is None or not booking.target_available(item.target):
            return None
        key = item_key(region.id, item.id)
        error = booking.write_delta(
            self._store.username, item.target, target - booked, f"撤销：{item.source}")
        if error is not None:
            logger.warning(f"冲销袅袅之音入账失败：{error}")
            return error
        done = max(self._state.resolve_done(key, item), booked)
        self._state = self._store.set_booked(key, target, done)
        return None

    def _settle_booking(self, region: LingerRegion, item: LingerItem) -> None:
        """把「已得」与「已入账」的差额写进账本；失败留在待入账。

        先记录后入账：这里读的是刚写好的记录，写账本成功才推进 ``booked``，
        所以任何失败都表现为「待入账」，不会出现账本有、记录没有。
        """
        if self._store is None or not self._booking_enabled:
            return
        if not booking.target_available(item.target):
            logger.warning(f"入账目标 {item.target} 不在定义里，本次只记录")
            return
        key = item_key(region.id, item.id)
        done = self._state.resolve_done(key, item)
        booked = self._state.booked(key)
        if done <= booked:
            return  # 减少已在 _commit 里预先冲销，这里只管新增
        error = booking.write_delta(
            self._store.username, item.target, done - booked, item.source)
        if error is not None:
            self._set_status(tr("保存失败"), error=True)
            return
        self._state = self._store.set_booked(key, done, done)

    def _refresh_item(self, region: LingerRegion, item: LingerItem) -> None:
        """把某个条目及其已展开的子项写回控件，期间屏蔽信号。"""
        key = item_key(region.id, item.id)
        if item.periodic:
            username = self._current_user()
            done, cap = (booking.periodic_state(username, item.target)
                         if username else (0, item.count))
            shown_count = cap or item.count
        else:
            done = self._state.resolve_done(key, item)
            shown_count = item.count
        slots = self._state.slot_view(key, item.count)
        self._updating = True
        for row, ref in self._rows.items():
            if ref.region.id != region.id or ref.item.id != item.id:
                continue
            if ref.slot is None:
                cell = self.table.item(row, COL_DONE)
                if cell is not None:
                    cell.setText(f"{done}/{shown_count}" if item.named else str(done))
                count_cell = self.table.item(row, COL_COUNT)
                if count_cell is not None and item.periodic:
                    count_cell.setText(str(shown_count))
                if item.kind == ITEM_SINGLE:
                    check = self.table.item(row, COL_ITEM)
                    if check is not None:
                        check.setCheckState(
                            Qt.CheckState.Checked if done >= shown_count
                            else Qt.CheckState.Unchecked)
                time_cell = self.table.item(row, COL_TIME)
                if time_cell is not None and not item.periodic:
                    time_cell.setText(_format_time(self._progress_at(key)))
            else:
                if ref.slot >= len(slots):
                    continue
                slot = slots[ref.slot]
                name_cell = self.table.item(row, COL_ITEM)
                if name_cell is not None and name_cell.text() != slot.name:
                    name_cell.setText(slot.name)
                done_cell = self.table.item(row, COL_DONE)
                if done_cell is not None:
                    done_cell.setCheckState(
                        Qt.CheckState.Checked if slot.done
                        else Qt.CheckState.Unchecked)
                time_cell = self.table.item(row, COL_TIME)
                if time_cell is not None:
                    time_cell.setText(_format_time(slot.at))
        self._updating = False

    def _set_status(self, text: str, *, error: bool = False) -> None:
        self.status.setText(text)
        self.status.setStyleSheet(
            "color: #c62828; font-size: 11px;" if error else _MUTED_STYLE)

    def _refresh_region_labels(self, region: LingerRegion) -> None:
        """只更新左栏里这一个地区的计数，避免整表重建丢焦点。"""
        done = sum(
            self._state.resolve_done(item_key(region.id, item.id), item)
            for item in region.items)
        self._updating = True
        for row in range(self.region_list.count()):
            entry = self.region_list.item(row)
            if entry is not None and entry.data(Qt.ItemDataRole.UserRole) == region.id:
                entry.setText(f"{region.label}  {done}/{region.fixed_total}")
                break
        self._updating = False

    def _refresh_summary(self) -> None:
        if not self._catalog.regions:
            self.summary.setText("")
            self.booked_label.setText("")
            return
        done, total = self._state.recorded_total(self._catalog)
        items, item_total = self._state.completed_items(self._catalog)
        self.summary.setText(tr("已记录 {done}/{total} 枚 · 完成 {items}/{item_total} 条").format(
            done=done, total=total, items=items, item_total=item_total))
        booked = self._state.booked_total(self._catalog)
        pending = self._state.pending_total(self._catalog)
        self.booked_label.setText(
            tr("已入账 {booked}/{total} 枚 · 待入账 {pending} 枚").format(
                booked=booked, total=total, pending=pending))
