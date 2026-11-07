"""变更记录对话框（全局 / 键级 / 键+用户级共用一个实现）。

三个入口的区别只是"预先填好的筛选条件"，界面本身完全一致：
顶部条件条显示当前生效的范围，每个标签都能取消，所以从"键+用户"进来之后
也可以放宽到全部用户或全部键，不需要关掉重开。

筛选分两处：
- 放不进某一列的维度（关键词、时间范围）在顶部筛选条；
- 每列一个聚合候选筛选，默认只列出现次数最多的 10 个取值，其余靠弹层里
  输入搜索——候选的模糊查找在数据库里做，不是新的查询条件。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import TypeVar

from PyQt6.QtCore import QEvent, QObject, QSize, Qt, QTimer
from PyQt6.QtGui import QBrush, QColor, QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from lvjiang.i18n import tr
from lvjiang.ui.button_styles import apply_compact_button_style, fit_button_width
from lvjiang.ui.combo_box import AutoWidthComboBox, ComboWidthMode

from ...core.config import load_ui_page_state, update_ui_page_state
from ...core.profile.history_query import HistoryQuery
from ...core.profile.models import format_sync_label
from ...core.profile.repository import (
    db_history_count,
    db_history_delta_totals,
    db_history_facet_total,
    db_history_facets,
    db_history_page,
    db_update_history_source,
)
from ...core.profile.schema import get_profile_config
from ...core.profile.service import (
    ProfileWriteConflict,
    delete_profile_history,
    profile_history_undo_unavailable_reason,
    undo_profile_history,
)
from ...errors import log_failure
from .history_filters import (
    FACET_VISIBLE_LIMIT,
    ConditionBar,
    ElidedLabel,
    FacetFilterButton,
    KeywordFilterBar,
    build_scope_combo,
    build_text_filter,
)

#: 来源列至少要完整显示 9 个全角字，覆盖「限时活动：XXXX」这类取值。
_SOURCE_CHARACTER_CAPACITY = 9
#: 单元左右内边距与边框；不量进来的话文字会贴边或被省略号吃掉。
_COLUMN_CHROME_WIDTH = 24
#: 关键词输入到真正查询之间的等待，避免每敲一个字扫一遍账本。
_KEYWORD_DEBOUNCE_MS = 250
#: 关键词命中标签时最多展开多少个键，防止一次点击生成上千个 OR 条件。
_MAX_KEY_MATCHES = 50

_UI_STATE_PAGE = "profile_history"

_HISTORY_ID_ROLE = int(Qt.ItemDataRole.UserRole)
_ORIGINAL_SOURCE_ROLE = _HISTORY_ID_ROLE + 1

_T = TypeVar("_T")


def _cjk_column_width(widget: QWidget, characters: int) -> int:
    """量出 N 个全角汉字所需的列宽（含单元留白）。

    不写死像素宽：来源允许用户输入任意文本，同一个 9 字来源在 Windows 的 UI
    字体下比别处宽。列宽不够时表格不会报错，只把「限时活动：XXXX」截成前半截，
    让人以为来源本来就短。
    """
    metrics = widget.fontMetrics()
    return metrics.horizontalAdvance("汉" * characters) + _COLUMN_CHROME_WIDTH


@dataclass(frozen=True)
class HistoryScope:
    """入口带来的默认范围。空条件就是全局入口。"""

    model_type: str = ""
    key: str = ""
    key_label: str = ""
    username: str | None = None

    @property
    def has_key(self) -> bool:
        return bool(self.key and self.model_type)

    def initial_query(self) -> HistoryQuery:
        return HistoryQuery(
            usernames=(self.username,) if self.username else (),
            keys=((self.model_type, self.key),) if self.has_key else (),
        )

    def title(self) -> str:
        parts: list[str] = []
        if self.username:
            parts.append(tr("用户 {name}").format(name=self.username))
        if self.has_key:
            parts.append(
                tr("键：{label}").format(label=self.key_label or self.key))
        title = tr("变更记录")
        return title + (" · " + " · ".join(parts) if parts else "")


def _local_author() -> str:
    """本机账本作者标记（实例出生标识，与上行/回放写入的 device_id 同源）。"""
    from ...core.account.identity import device_author_id

    return device_author_id()


def _device_label(raw: str) -> str:
    """把账本的 ``device_id`` 变成人能读的设备名。

    账本里存的是实例标识，不是设备显示名：显示名会随用户改名而变，标识不会。
    本机直接标「本机」，其余显示前 8 位——足够在两三台设备之间分辨，完整
    标识留在 tooltip 与筛选里。
    """
    if not raw:
        return tr("未记录")
    if raw == _local_author():
        return tr("本机")
    return tr("其他设备") + " · " + raw[:8]


@dataclass(frozen=True)
class _Column:
    """一列的渲染 + 筛选规格；列号不再靠手写偏移量计算。"""

    key: str
    header: str
    resize: QHeaderView.ResizeMode
    width: int = 0
    facet: str = ""
    filter_kind: str = "none"   # none | facet | text | delta


class _RowActions(QWidget):
    """一行的撤销 / 删除按钮。

    每页刷新都新建控件会把旧控件留在视口里（Qt 替换索引控件时延迟销毁），
    翻几页就堆出一片幽灵按钮。这里按行复用同一个控件，只改它指向的记录。
    """

    def __init__(self, dialog: HistoryDialog, table: QTableWidget) -> None:
        super().__init__(table)
        self._record: dict = {}
        layout = QHBoxLayout(self)
        layout.setContentsMargins(2, 1, 6, 1)
        layout.setSpacing(4)
        # 操作列是拉伸列：按钮贴右边缘，宽屏时不会被一大片空白甩在左边。
        layout.addStretch(1)
        self._undo = QPushButton(tr("撤销"), self)
        self._delete = QPushButton(tr("删除"), self)
        apply_compact_button_style(self._undo, self._delete, variant="neutral")
        fit_button_width(self._undo, self._delete)
        self._delete.setToolTip(tr("仅删除历史，不修改当前值；会影响按历史统计的额度"))
        self._undo.clicked.connect(lambda: dialog._confirm_undo(self._record))
        self._delete.clicked.connect(lambda: dialog._confirm_delete(self._record))
        layout.addWidget(self._undo)
        layout.addWidget(self._delete)

    def set_record(self, record: dict) -> None:
        self._record = record
        reason = profile_history_undo_unavailable_reason(record)
        self._undo.setEnabled(reason is None)
        self._undo.setToolTip(reason or "")


class HistoryDialog(QDialog):
    """按条件浏览、筛选并撤销单条变更记录。"""

    _TYPE_LABEL = {
        "tick": tr("恢复"), "reset": tr("重置"),
        "set": tr("覆盖"), "delta": tr("增量"),
    }  # runtime tr()
    _PAGE_SIZES = (50, 100, 200, 500)

    def __init__(self, scope: HistoryScope | None = None, parent=None) -> None:
        super().__init__(parent)
        self._scope = scope or HistoryScope()
        self._query = self._scope.initial_query()
        self._facet_query: HistoryQuery | None = None
        self._page = 1
        self._page_size = 100
        self._page_count = 1
        self._total = 0
        self._shown = 0
        self._loading = False
        self._facet_buttons: dict[str, FacetFilterButton] = {}
        self._text_filters: dict[str, QLineEdit] = {}
        self._delta_combo: QComboBox | None = None
        self._empty_panel: QWidget | None = None
        self.values_changed = False

        self.setWindowTitle(self._scope.title())
        self.resize(1240, 700)
        self._build_ui()
        self._restore_state()
        # 入口范围先落到筛选控件上，再统一查一次：控件状态是唯一真值。
        self._reset_widgets(self._scope.initial_query(), apply=False)
        self._reload(reset_page=True)

    # ─── 界面搭建 ─────────────────────────────────────────────

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        scope_row = QHBoxLayout()
        scope_row.setSpacing(8)
        self._conditions = ConditionBar(self)
        self._conditions.removed.connect(self._drop_condition)
        scope_row.addWidget(self._conditions, 1)
        self._reset_scope_button = QPushButton(tr("恢复入口范围"), self)
        apply_compact_button_style(self._reset_scope_button, variant="neutral")
        self._reset_scope_button.setToolTip(tr("回到打开这个对话框时的默认范围"))
        self._reset_scope_button.clicked.connect(self._restore_scope)
        scope_row.addWidget(self._reset_scope_button)
        layout.addLayout(scope_row)

        bar_row = QHBoxLayout()
        bar_row.setSpacing(8)
        self._filter_bar = KeywordFilterBar(self)
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(_KEYWORD_DEBOUNCE_MS)
        self._debounce.timeout.connect(self._apply_filters)
        self._filter_bar.changed.connect(lambda: self._debounce.start())
        bar_row.addWidget(self._filter_bar, 1)
        self._clear_all_button = QPushButton(tr("清除全部筛选"), self)
        apply_compact_button_style(self._clear_all_button, variant="neutral")
        self._clear_all_button.clicked.connect(self._clear_all_filters)
        bar_row.addWidget(self._clear_all_button)
        layout.addLayout(bar_row)

        self._table = self._build_table()
        layout.addWidget(self._table, 1)
        layout.addLayout(self._build_pager())

        self._shortcut_find = QShortcut(QKeySequence("Ctrl+F"), self)
        self._shortcut_find.activated.connect(self._filter_bar.focus_keyword)

    def _build_table(self) -> QTableWidget:
        table = QTableWidget(self)
        self._columns = self._column_defs()
        table.setColumnCount(len(self._columns))
        table.setHorizontalHeaderLabels([column.header for column in self._columns])
        # 第 0 行固定是筛选行，数据从第 1 行开始。
        table.setRowCount(1)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked)
        table.setAlternatingRowColors(True)
        # 内容超出列宽时省略并留 tooltip，不让单元格自己换行把行高撑高。
        table.setWordWrap(False)
        table.setTextElideMode(Qt.TextElideMode.ElideRight)
        table.itemChanged.connect(self._on_item_changed)
        viewport = table.viewport()
        if viewport is not None:
            viewport.installEventFilter(self)

        vertical = table.verticalHeader()
        if vertical is not None:
            vertical.setVisible(False)
            vertical.setDefaultSectionSize(max(26, self.fontMetrics().height() + 14))
        header = table.horizontalHeader()
        if header is not None:
            # 最后一列（操作）吃掉剩余宽度，宽屏时不会在表格右侧留一块空白。
            header.setStretchLastSection(True)
            for index, column in enumerate(self._columns):
                header.setSectionResizeMode(index, column.resize)
                if column.width:
                    table.setColumnWidth(index, column.width)
        self._build_filter_cells(table)
        table.setRowHeight(0, self._filter_row_height())
        return table

    def _filter_row_height(self) -> int:
        return max(30, self.fontMetrics().height() + 16)

    def _column_defs(self) -> list[_Column]:
        fixed = QHeaderView.ResizeMode.Fixed
        metrics = self.fontMetrics()
        # 时间列按"完整日期 + 时分秒"的字宽量，不写死像素：
        # 窄一寸就会把年月日折成两行，整张表跟着变高。
        time_width = (
            metrics.horizontalAdvance("0000-00-00 00:00:00")
            + _COLUMN_CHROME_WIDTH + 8
        )
        # 用户名固定宽度：列宽不随页面内容变化，换页/换筛选时表格不会横向抖动；
        # 更长的名字省略显示，全文在 tooltip 与筛选弹层里。
        user_width = max(
            _cjk_column_width(self, 6),
            metrics.horizontalAdvance(tr("全部") + " ▾") + _COLUMN_CHROME_WIDTH + 8,
        )
        # 设备列：本机显示「本机」，他机显示「其他设备 · 前 8 位」——宽度按后者量。
        device_width = max(
            metrics.horizontalAdvance(tr("其他设备") + " · 00000000"),
            metrics.horizontalAdvance(tr("本机")),
        ) + _COLUMN_CHROME_WIDTH
        return [
            _Column("ts", tr("时间"), fixed, time_width),
            # 「键 → 用户 → 类型」：先看是哪个数据项，再看是谁、怎么变。
            _Column("key", tr("键"), fixed, _cjk_column_width(self, 10),
                    facet="key", filter_kind="facet"),
            _Column("username", tr("用户名"), fixed, user_width,
                    facet="username", filter_kind="facet"),
            _Column("change_type", tr("类型"), fixed, 92,
                    facet="change_type", filter_kind="facet"),
            _Column("old", tr("旧值"), fixed, 88, filter_kind="text"),
            _Column("new", tr("新值"), fixed, 88, filter_kind="text"),
            _Column("delta", tr("变动量"), fixed, 100, filter_kind="delta"),
            _Column("source", tr("来源"), fixed,
                    _cjk_column_width(self, _SOURCE_CHARACTER_CAPACITY),
                    facet="source", filter_kind="facet"),
            _Column("device", tr("设备"), fixed, device_width,
                    facet="device", filter_kind="facet"),
            _Column("sync_from", tr("同步来源"), fixed,
                    _cjk_column_width(self, _SOURCE_CHARACTER_CAPACITY),
                    facet="sync_from", filter_kind="facet"),
            _Column("actions", tr("操作"), fixed, 150),
        ]

    def _build_filter_cells(self, table: QTableWidget) -> None:
        for index, column in enumerate(self._columns):
            widget = self._build_filter_widget(column, table)
            if widget is None:
                continue
            holder = QWidget(table)
            holder_layout = QVBoxLayout(holder)
            holder_layout.setContentsMargins(2, 2, 2, 2)
            holder_layout.setSpacing(0)
            holder_layout.addWidget(widget)
            table.setCellWidget(0, index, holder)

    def _build_filter_widget(
        self, column: _Column, table: QTableWidget,
    ) -> QWidget | None:
        if column.filter_kind == "facet":
            button = FacetFilterButton(
                column.facet, column.header, table,
                display=self._facet_display(column.facet),
            )
            button.values_changed.connect(
                lambda values, dimension=column.facet:
                self._on_facet_values(dimension, values))
            button.search_requested.connect(
                lambda text, dimension=column.facet:
                self._on_facet_search(dimension, text))
            self._facet_buttons[column.facet] = button
            return button
        if column.filter_kind == "text":
            edit = build_text_filter(table, tr("包含"))
            edit.textEdited.connect(lambda _text: self._debounce.start())
            self._text_filters[column.key] = edit
            return edit
        if column.filter_kind == "delta":
            combo = build_scope_combo(table)
            combo.currentIndexChanged.connect(lambda _index: self._apply_filters())
            self._delta_combo = combo
            return combo
        return None

    def _build_pager(self) -> QHBoxLayout:
        pager = QHBoxLayout()
        pager.setSpacing(6)
        pager.addWidget(QLabel(tr("每页"), self))
        self._page_size_combo = AutoWidthComboBox(self, width_mode=ComboWidthMode.FULL)
        for size in self._PAGE_SIZES:
            self._page_size_combo.addItem(str(size), size)
        self._page_size_combo.setCurrentIndex(
            self._page_size_combo.findData(self._page_size))
        self._page_size_combo.currentIndexChanged.connect(self._on_page_size_changed)
        pager.addWidget(self._page_size_combo)
        pager.addWidget(QLabel(tr("条"), self))

        # 合计与页码随数据变长；用可省略标签，别让它决定对话框的最小宽度。
        self._summary_label = ElidedLabel(self)
        self._summary_label.setEnabled(False)
        pager.addWidget(self._summary_label)
        pager.addStretch(1)

        self._page_label = ElidedLabel(self)
        pager.addWidget(self._page_label)
        self._page_spin = QSpinBox(self)
        self._page_spin.setRange(1, 1)
        self._page_spin.setKeyboardTracking(False)
        self._page_spin.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._page_spin.editingFinished.connect(self._jump_to_page)
        pager.addWidget(self._page_spin)
        self._jump_button = QPushButton(tr("跳至"), self)
        self._jump_button.clicked.connect(self._jump_to_page)
        apply_compact_button_style(self._jump_button, variant="action")
        pager.addWidget(self._jump_button)

        self._first_button = QPushButton(tr("首页"), self)
        self._previous_button = QPushButton(tr("上一页"), self)
        self._next_button = QPushButton(tr("下一页"), self)
        self._last_button = QPushButton(tr("末页"), self)
        self._first_button.clicked.connect(lambda: self._go_to_page(1))
        self._previous_button.clicked.connect(lambda: self._go_to_page(self._page - 1))
        self._next_button.clicked.connect(lambda: self._go_to_page(self._page + 1))
        self._last_button.clicked.connect(lambda: self._go_to_page(self._page_count))
        for button in (
            self._first_button, self._previous_button,
            self._next_button, self._last_button,
        ):
            apply_compact_button_style(button, variant="neutral")
            pager.addWidget(button)
        fit_button_width(
            self._first_button, self._previous_button,
            self._next_button, self._last_button,
        )
        return pager

    # ─── 条件收集与查询 ───────────────────────────────────────

    def _collect_query(self) -> HistoryQuery:
        values = {
            dimension: button.values()
            for dimension, button in self._facet_buttons.items()
        }
        keyword = self._filter_bar.keyword()
        ts_from, ts_until = self._filter_bar.date_range()
        delta_scope = "all"
        if self._delta_combo is not None:
            delta_scope = str(self._delta_combo.currentData() or "all")
        return HistoryQuery(
            usernames=values.get("username", ()),
            keys=tuple(_split_key_value(raw) for raw in values.get("key", ())),
            change_types=values.get("change_type", ()),
            sources=values.get("source", ()),
            sync_froms=values.get("sync_from", ()),
            devices=values.get("device", ()),
            keyword=keyword,
            old_text=self._text_filters["old"].text().strip()
            if "old" in self._text_filters else "",
            new_text=self._text_filters["new"].text().strip()
            if "new" in self._text_filters else "",
            key_matches=self._key_matches(keyword),
            ts_from=ts_from,
            ts_until=ts_until,
            delta_scope=delta_scope,
        )

    def _key_matches(self, keyword: str) -> tuple[tuple[str, str], ...]:
        """关键词同时匹配键的展示标签：标签在配置文件里，SQL 看不到。"""
        if not keyword:
            return ()
        needle = keyword.casefold()
        matches: list[tuple[str, str]] = []
        try:
            config = get_profile_config()
            for key_def in config.get_all_keys():
                if needle not in (key_def.label or "").casefold():
                    continue
                model_type = config.get_model_type(key_def.key)
                if not model_type:
                    continue
                matches.append((model_type, key_def.key))
                if len(matches) >= _MAX_KEY_MATCHES:
                    break
        except Exception as error:  # noqa: BLE001 - 定义读不到不该挡住历史浏览
            log_failure("变更记录：读取数据模型定义失败", error)
            return ()
        return tuple(matches)

    def _apply_filters(self) -> None:
        self._reload(reset_page=True)

    def _reload(self, *, reset_page: bool, refresh_facets: bool = False) -> None:
        """按当前控件状态重新查询；页码只在条件变化时回到第一页。"""
        query = self._collect_query()
        if reset_page or query != self._query:
            self._page = 1
        if query != self._facet_query or refresh_facets:
            self._refresh_facets(query)
        self._query = query
        try:
            self._total = db_history_count(query)
        except Exception as error:  # noqa: BLE001 - 读取失败给出可行动提示，界面不崩
            self._show_read_error(error)
            return
        self._page_count = max(1, (self._total + self._page_size - 1) // self._page_size)
        self._page = min(max(1, self._page), self._page_count)
        try:
            rows = db_history_page(
                query, limit=self._page_size,
                offset=(self._page - 1) * self._page_size,
            )
        except Exception as error:  # noqa: BLE001
            self._show_read_error(error)
            return
        self._render(rows)
        self._refresh_summary()
        self._update_pager()
        self._conditions.set_conditions(self._condition_chips())

    def _refresh_facets(self, query: HistoryQuery) -> None:
        """刷新每列的聚合候选与"另有 N 项"提示。"""
        self._facet_query = query
        for dimension, button in self._facet_buttons.items():
            search = button.search_text()
            try:
                rows = db_history_facets(
                    query, dimension, limit=FACET_VISIBLE_LIMIT, search=search)
                total = db_history_facet_total(query, dimension, search=search)
            except Exception as error:  # noqa: BLE001
                log_failure(f"变更记录：刷新 {dimension} 聚合候选失败", error)
                continue
            button.apply_candidates(rows, total)

    def _refresh_summary(self) -> None:
        try:
            gained, spent = db_history_delta_totals(self._query)
        except Exception as error:  # noqa: BLE001 - 摘要失败不影响列表
            log_failure("变更记录：统计变动量合计失败", error)
            return
        parts = [tr("共 {total} 条").format(total=self._format_count(self._total))]
        if gained:
            parts.append(tr("增加 {value}").format(value=_format_amount(gained)))
        if spent:
            parts.append(tr("减少 {value}").format(value=_format_amount(spent)))
        self._summary_label.set_full_text(" · ".join(parts))

    @staticmethod
    def _format_count(value: int) -> str:
        return f"{value:,}"

    # ─── 表格渲染 ─────────────────────────────────────────────

    def _render(self, rows: list[dict]) -> None:
        table = self._table
        target = len(rows) + 1
        # 行数缩减时主动摘掉多余的按钮控件：只靠 setRowCount 让 Qt 延迟销毁，
        # 旧控件会在视口里留下未定位的「幽灵按钮」。
        actions_index = self._column_index("actions")
        if actions_index is not None:
            for row in range(target, table.rowCount()):
                widget = table.cellWidget(row, actions_index)
                if widget is not None:
                    table.removeCellWidget(row, actions_index)
                    # 先隐藏再排队销毁：延迟销毁发生在下一轮事件循环，
                    # 中间这段时间它仍然会画在视口左上角。
                    widget.hide()
                    widget.deleteLater()
        self._loading = True
        try:
            table.setRowCount(target)
            for index, record in enumerate(rows, start=1):
                self._fill_row(table, index, record)
        finally:
            self._loading = False
        self._shown = len(rows)
        self._update_empty_state()

    def _fill_row(self, table: QTableWidget, row: int, record: dict) -> None:
        for index, column in enumerate(self._columns):
            if column.key == "actions":
                self._apply_actions(table, row, index, record)
                continue
            item = QTableWidgetItem(self._cell_text(column.key, record))
            item.setData(_HISTORY_ID_ROLE, record.get("id"))
            if column.key != "source":
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            else:
                item.setData(_ORIGINAL_SOURCE_ROLE, record.get("source") or "")
                item.setToolTip(tr("双击修改来源"))
            self._decorate(item, column.key, record)
            table.setItem(row, index, item)

    def _decorate(self, item: QTableWidgetItem, key: str, record: dict) -> None:
        if key == "ts":
            item.setToolTip(str(record.get("ts", "")))
        elif key == "change_type":
            color = self._type_color(str(record.get("change_type") or ""))
            if color is not None:
                item.setForeground(QBrush(color))
            item.setToolTip(tr("变更类型：{label}").format(
                label=item.text()))
        elif key == "key":
            item.setToolTip(f"{record.get('type', '')}:{record.get('key', '')}")
        elif key == "old" or key == "new":
            item.setToolTip(item.text())
        elif key == "delta":
            item.setTextAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            color = self._delta_color(_delta_amount(record))
            if color is not None:
                item.setForeground(QBrush(color))
        elif key == "sync_from":
            raw = record.get("sync_from") or ""
            if raw:
                item.setToolTip(raw)
        elif key == "device":
            raw = record.get("device_id") or ""
            item.setToolTip(raw or tr("未记录设备"))
        elif key == "source":
            item.setToolTip(item.text() or tr("未记录"))

    def _cell_text(self, key: str, record: dict) -> str:
        if key == "ts":
            return _format_timestamp(str(record.get("ts") or ""))
        if key == "username":
            return str(record.get("username") or "")
        if key == "change_type":
            raw = str(record.get("change_type") or "")
            return self._TYPE_LABEL.get(raw, raw)
        if key == "key":
            raw = f"{record.get('type', '')}:{record.get('key', '')}"
            return format_sync_label(raw)
        if key in ("old", "new"):
            return _format_value(record, key, str(record.get("type") or ""))
        if key == "delta":
            amount = _delta_amount(record)
            return _format_amount(amount) if amount is not None else "—"
        if key == "source":
            return str(record.get("source") or "")
        if key == "device":
            return _device_label(str(record.get("device_id") or ""))
        if key == "sync_from":
            raw = record.get("sync_from") or ""
            return format_sync_label(str(raw)) if raw else ""
        return ""

    def _apply_actions(
        self, table: QTableWidget, row: int, index: int, record: dict,
    ) -> None:
        cell = table.cellWidget(row, index)
        if not isinstance(cell, _RowActions):
            cell = _RowActions(self, table)
            table.setCellWidget(row, index, cell)
        cell.set_record(record)

    def _type_color(self, change_type: str) -> QColor | None:
        """类型徽标用主题语义色，深色主题下不会变成刺眼的纯色。"""
        tokens = self._theme_tokens()
        if tokens is None:
            return None
        mapping = {
            "set": tokens.info,
            "delta": tokens.success,
            "reset": tokens.warning,
            "tick": tokens.text_muted,
        }
        value = mapping.get(change_type)
        return QColor(value) if value else None

    def _delta_color(self, delta: object) -> QColor | None:
        tokens = self._theme_tokens()
        if tokens is None or delta is None:
            return None
        try:
            numeric = float(delta)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return None
        if numeric > 0:
            return QColor(tokens.success)
        if numeric < 0:
            return QColor(tokens.danger)
        return None

    @staticmethod
    def _theme_tokens():
        try:
            from lvjiang.ui.theme import get_theme_manager

            return get_theme_manager().tokens
        except Exception:  # noqa: BLE001 - 主题不可用时退化为默认前景色
            return None

    # ─── 列显隐、空状态与分页 ────────────────────────────────

    def _update_empty_state(self) -> None:
        has_rows = self._shown > 0
        if has_rows:
            if self._empty_panel is not None:
                self._empty_panel.hide()
            return
        if self._empty_panel is None:
            self._empty_panel = self._build_empty_panel()
        self._position_empty_panel()
        self._empty_panel.show()

    def _build_empty_panel(self) -> QWidget:
        panel = QWidget(self._table.viewport())
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        label = QLabel(tr("没有符合筛选条件的记录"), panel)
        label.setEnabled(False)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(label)
        button = QPushButton(tr("清除全部筛选"), panel)
        apply_compact_button_style(button, variant="neutral")
        button.clicked.connect(self._clear_all_filters)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(button)
        row.addStretch(1)
        layout.addLayout(row)
        return panel

    def _position_empty_panel(self) -> None:
        panel = self._empty_panel
        viewport = self._table.viewport() if self._table is not None else None
        if panel is None or viewport is None:
            return
        panel.adjustSize()
        panel.move(
            max(0, (viewport.width() - panel.width()) // 2),
            max(0, (viewport.height() - panel.height()) // 2 + self._table.rowHeight(0)),
        )

    def eventFilter(  # noqa: N802 - Qt API
        self, source: QObject | None, event: QEvent | None,
    ) -> bool:
        table = getattr(self, "_table", None)
        if (
            table is not None and source is table.viewport()
            and event is not None and event.type() == QEvent.Type.Resize
        ):
            self._position_empty_panel()
        return super().eventFilter(source, event)

    def _update_pager(self) -> None:
        start = (self._page - 1) * self._page_size + 1 if self._total else 0
        end = start + self._shown - 1 if self._shown else 0
        self._page_label.set_full_text(
            tr("第 {page} / {pages} 页 · {start}–{end} 条").format(
                page=self._page, pages=self._page_count,
                start=start, end=end))
        self._page_spin.setRange(1, self._page_count)
        self._page_spin.setValue(self._page)
        digits = max(2, len(str(self._page_count)))
        self._page_spin.setFixedWidth(
            self.fontMetrics().horizontalAdvance("0" * digits) + 28)
        self._first_button.setEnabled(self._page > 1)
        self._previous_button.setEnabled(self._page > 1)
        self._next_button.setEnabled(self._page < self._page_count)
        self._last_button.setEnabled(self._page < self._page_count)
        self._page_spin.setEnabled(self._page_count > 1)
        self._jump_button.setEnabled(self._page_count > 1)

    def _jump_to_page(self) -> None:
        self._go_to_page(self._page_spin.value())

    def _go_to_page(self, page: int) -> None:
        target = min(max(1, page), self._page_count)
        if target == self._page:
            return
        self._page = target
        self._reload(reset_page=False)

    def _on_page_size_changed(self) -> None:
        self._page_size = int(self._page_size_combo.currentData())
        self._page = 1
        self._reload(reset_page=True)

    # ─── 条件标签 ─────────────────────────────────────────────

    def _condition_chips(self) -> list[tuple[str, str, str]]:
        """把当前条件摊成可取消的标签；同一维度多个取值折叠成一个标签。"""
        query = self._query
        chips: list[tuple[str, str, str]] = []

        def summary(
            raw_values: tuple[_T, ...], display: Callable[[_T], str], noun: str,
        ) -> str:
            if len(raw_values) == 1:
                return display(raw_values[0])
            return tr("{noun} · {count} 项").format(
                noun=noun, count=len(raw_values))

        if query.usernames:
            chips.append((
                "username",
                tr("用户：{value}").format(
                    value=summary(query.usernames, str, tr("用户"))),
                "、".join(query.usernames)))
        if query.keys:
            chips.append((
                "key",
                tr("键：{value}").format(
                    value=summary(
                        query.keys,
                        lambda pair: format_sync_label(f"{pair[0]}:{pair[1]}"),
                        tr("键"))),
                "、".join(f"{pair[0]}:{pair[1]}" for pair in query.keys)))
        if query.change_types:
            chips.append(("change_type", tr("类型：{value}").format(
                value=summary(
                    query.change_types,
                    lambda raw: self._TYPE_LABEL.get(raw, raw), tr("类型"))), ""))
        if query.sources:
            chips.append(("source", tr("来源：{value}").format(
                value=summary(
                    query.sources,
                    lambda raw: raw or tr("未记录"), tr("来源"))), ""))
        if query.devices:
            chips.append(("device", tr("设备：{value}").format(
                value=summary(query.devices, _device_label, tr("设备"))), ""))
        if query.sync_froms:
            chips.append(("sync_from", tr("同步来源：{value}").format(
                value=summary(query.sync_froms, format_sync_label, tr("同步来源"))), ""))
        if query.keyword:
            chips.append(("keyword", tr("搜索：{value}").format(value=query.keyword), ""))
        if query.old_text:
            chips.append(("old", tr("旧值包含：{value}").format(value=query.old_text), ""))
        if query.new_text:
            chips.append(("new", tr("新值包含：{value}").format(value=query.new_text), ""))
        if query.ts_from or query.ts_until:
            chips.append(("time", tr("时间：{value}").format(
                value=self._filter_bar.time_label()), ""))
        if query.delta_scope != "all":
            scope_label = {
                "pos": tr("增加"), "neg": tr("减少"), "zero": tr("为零"),
            }.get(query.delta_scope, query.delta_scope)
            chips.append(("delta", tr("变动量：{value}").format(value=scope_label), ""))
        return chips

    def _drop_condition(self, dimension: str) -> None:
        """取消一个维度：清掉它的控件状态，其它条件保持不动。"""
        if dimension in self._facet_buttons:
            self._facet_buttons[dimension].set_values(())
        elif dimension in self._text_filters:
            edit = self._text_filters[dimension]
            edit.blockSignals(True)
            edit.clear()
            edit.blockSignals(False)
        elif dimension == "delta" and self._delta_combo is not None:
            self._delta_combo.blockSignals(True)
            self._delta_combo.setCurrentIndex(0)
            self._delta_combo.blockSignals(False)
        elif dimension == "keyword":
            self._filter_bar.set_keyword("")
        elif dimension == "time":
            self._filter_bar.reset_time()
        self._apply_filters()

    def _clear_all_filters(self) -> None:
        self._reset_widgets(HistoryQuery())

    def _restore_scope(self) -> None:
        """回到入口带来的默认范围（预设不锁定，这里只是复位，不是权限）。"""
        self._reset_widgets(self._scope.initial_query())

    def _reset_widgets(self, query: HistoryQuery, *, apply: bool = True) -> None:
        """把控件状态整体回填成某个条件对象，默认立刻只查询一次。"""
        for dimension, button in self._facet_buttons.items():
            button.set_values(_values_for_dimension(query, dimension))
        for key, edit in self._text_filters.items():
            edit.blockSignals(True)
            edit.setText(getattr(query, f"{key}_text"))
            edit.blockSignals(False)
        if self._delta_combo is not None:
            self._delta_combo.blockSignals(True)
            index = self._delta_combo.findData(query.delta_scope)
            self._delta_combo.setCurrentIndex(max(0, index))
            self._delta_combo.blockSignals(False)
        self._filter_bar.set_keyword(query.keyword)
        self._filter_bar.reset_time()
        if apply:
            self._apply_filters()

    # ─── 聚合候选回调 ─────────────────────────────────────────

    def _on_facet_values(self, _dimension: str, _values: tuple) -> None:
        self._apply_filters()

    def _on_facet_search(self, dimension: str, text: str) -> None:
        """弹层输入：只在候选列表里模糊找，不改变查询条件。"""
        button = self._facet_buttons.get(dimension)
        if button is None:
            return
        try:
            rows = db_history_facets(
                self._query, dimension, limit=FACET_VISIBLE_LIMIT, search=text)
            total = db_history_facet_total(self._query, dimension, search=text)
        except Exception as error:  # noqa: BLE001
            log_failure(f"变更记录：搜索 {dimension} 候选失败", error)
            return
        button.apply_candidates(rows, total)

    def _facet_display(self, dimension: str) -> Callable[[str], str]:
        if dimension in ("key", "sync_from"):
            return lambda raw: format_sync_label(raw) if raw else tr("未记录")
        if dimension == "change_type":
            return lambda raw: self._TYPE_LABEL.get(raw, raw) if raw else tr("未记录")
        if dimension == "device":
            return _device_label
        return lambda raw: raw or tr("未记录")

    # ─── 来源列内联编辑与撤销 / 删除 ──────────────────────────

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        if self._loading:
            return
        index = self._column_index("source")
        if index is None or item.column() != index:
            return
        history_id = item.data(_HISTORY_ID_ROLE)
        old_source = item.data(_ORIGINAL_SOURCE_ROLE) or ""
        new_source = item.text().strip()
        if new_source == old_source:
            if item.text() != old_source:
                self._loading = True
                item.setText(old_source)
                self._loading = False
            return
        if not db_update_history_source(
            int(history_id), expected_source=old_source, new_source=new_source,
        ):
            QMessageBox.warning(
                self, tr("修改失败"), tr("该条历史记录已发生变化，请刷新后重试"))
            self._reload(reset_page=False)
            return
        self._loading = True
        item.setText(new_source)
        item.setData(_ORIGINAL_SOURCE_ROLE, new_source)
        self._loading = False
        self._refresh_facets(self._query)

    def _column_index(self, key: str) -> int | None:
        for index, column in enumerate(self._columns):
            if column.key == key:
                return index
        return None

    def _record_label(self, record: dict) -> str:
        raw = f"{record.get('type', '')}:{record.get('key', '')}"
        label = format_sync_label(raw)
        return raw if label == raw else f"{label}（{raw}）"

    def _confirm_undo(self, record: dict) -> None:
        message = tr(
            '确定撤销这条记录吗？\n\n用户：{username}\n数据项：{label}\n'
            '时间：{time}\n来源：{source}\n\n撤销会按反向写入一条新记录。'
        ).format(
            username=record.get("username", ""),
            label=self._record_label(record),
            time=_format_timestamp(str(record.get("ts") or "")),
            source=record.get("source", "") or tr("无"),
        )
        answer = QMessageBox.question(
            self, tr("确认撤销"), message,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            undo_profile_history(int(record["id"]))
        except (ProfileWriteConflict, ValueError) as exc:
            log_failure(
                f"{record.get('username', '')} {record.get('type', '')}."
                f"{record.get('key', '')} 历史撤销失败", exc)
            QMessageBox.warning(self, tr("撤销失败"), str(exc))
            self._reload(reset_page=False)
            return
        self.values_changed = True
        self._reload(reset_page=False, refresh_facets=True)

    def _confirm_delete(self, record: dict) -> None:
        message = tr(
            '确定永久删除这条历史记录吗？\n\n用户：{username}\n数据项：{label}\n'
            '时间：{time}\n来源：{source}\n变动量：{delta}\n\n'
            '仅删除历史，不修改当前值，不新增删除记录。此操作不可撤销；'
            '删除获取记录可能释放按历史累计的来源额度。'
        ).format(
            username=record.get("username", ""),
            label=self._record_label(record),
            time=_format_timestamp(str(record.get("ts") or "")),
            source=record.get("source", "") or tr("无"),
            delta=record.get("delta_value"),
        )
        answer = QMessageBox.question(
            self, tr("确认删除"), message,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            delete_profile_history(
                int(record["id"]), str(record["username"]),
                str(record["type"]), str(record["key"]))
        except Exception as error:  # noqa: BLE001 - 失败保留界面并给出可行动提示
            log_failure(
                f"{record.get('username', '')} {record.get('type', '')}."
                f"{record.get('key', '')} 历史删除失败", error)
            QMessageBox.warning(self, tr("删除失败"), str(error))
        self._reload(reset_page=False, refresh_facets=True)

    def _show_read_error(self, error: Exception) -> None:
        log_failure("变更记录：读取历史失败", error)
        QMessageBox.warning(self, tr("读取失败"), str(error))

    # ─── 窗口状态 ─────────────────────────────────────────────

    def _restore_state(self) -> None:
        state = load_ui_page_state(_UI_STATE_PAGE)
        size = state.get("window_size")
        if (
            isinstance(size, list) and len(size) == 2
            and all(type(value) is int and value > 0 for value in size)
        ):
            self.resize(*size)
        page_size = state.get("page_size")
        if page_size in self._PAGE_SIZES:
            self._page_size = int(page_size)
            self._page_size_combo.setCurrentIndex(
                self._page_size_combo.findData(self._page_size))

    def done(self, result: int) -> None:
        # QDialog 的关闭、Esc 与 accept/reject 都经由 done 收尾。
        try:
            update_ui_page_state(_UI_STATE_PAGE, {
                "window_size": [self.width(), self.height()],
                "page_size": self._page_size,
            })
        except Exception as error:  # noqa: BLE001 - 状态存不下不影响关闭
            log_failure("变更记录：保存窗口状态失败", error)
        super().done(result)

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt API
        return QSize(1240, 700)


def _split_key_value(raw: str) -> tuple[str, str]:
    """把聚合候选的「模型:键」还原成筛选条件用的二元组。"""
    model_type, _, key = raw.partition(":")
    return model_type, key


def _values_for_dimension(query: HistoryQuery, dimension: str) -> tuple[str, ...]:
    if dimension == "username":
        return query.usernames
    if dimension == "key":
        return tuple(f"{pair[0]}:{pair[1]}" for pair in query.keys)
    if dimension == "change_type":
        return query.change_types
    if dimension == "source":
        return query.sources
    if dimension == "device":
        return query.devices
    return query.sync_froms


def _format_timestamp(raw: str) -> str:
    """历史时间是 ISO 文本；解析失败就原样显示，不吞掉信息。"""
    try:
        return datetime.fromisoformat(raw).strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        return raw


def _delta_amount(record: dict) -> float | None:
    """与仓储层的方向筛选同一口径：没有 delta_value 时按新旧值补算。

    两者都缺（旧值未知，例如某键的第一条记录）时返回 ``None``，界面显示「—」，
    不把"不清楚起点"说成"从 0 增加了这么多"。
    """
    delta = record.get("delta_value")
    if delta is not None:
        try:
            return float(delta)
        except (TypeError, ValueError):
            return None
    old_value = record.get("old_value")
    new_value = record.get("new_value")
    if old_value is None or new_value is None:
        return None
    try:
        return float(new_value) - float(old_value)
    except (TypeError, ValueError):
        return None


def _format_amount(value: float) -> str:
    """变动量按定点 + 千分位显示。

    账本级数值常常上万，``+g`` 会写成 ``+1.667e+07``，既难读又丢精度。
    """
    if value == 0:
        return "0"
    if value == int(value):
        return f"{int(value):+,}"
    return f"{value:+,.2f}".rstrip("0").rstrip(".")


def _format_value(record: dict, side: str, model_type: str) -> str:
    """note 模型展示文本值，其它模型展示整数化后的数值。"""
    if model_type == "note":
        text = record.get(f"{side}_value_text") or ""
        return text or "—"
    value = record.get(f"{side}_value")
    if value is None:
        return "—"
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return str(value)
    return str(int(numeric)) if numeric == int(numeric) else str(numeric)
