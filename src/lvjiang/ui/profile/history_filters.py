"""变更记录对话框的筛选控件。

三个部件构成一套筛选界面：

- :class:`ConditionBar`：顶部"当前生效条件"的标签条，每个标签可单独取消，
  适合展示被表格列挡住的维度（例如单用户视图里被隐藏的「用户」条件）。
- :class:`FacetFilterButton`：表头筛选行里的聚合按钮。候选按出现次数倒序，
  默认只给前 20 项；要在更多取值里找，就在弹层搜索框里输入，由调用方去数据库
  做候选内的模糊查找（不是新的查询条件）。
- :class:`KeywordFilterBar`：关键词 + 时间范围，这两个维度放不进某一列。

控件本身不读数据库、也不拼条件：候选和当前取值都由对话框注入，控件只在
用户操作时发信号回去。
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable

from PyQt6.QtCore import QDate, QEvent, QPoint, QRect, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QKeySequence, QResizeEvent, QShortcut
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QDateEdit,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from lvjiang.i18n import tr
from lvjiang.ui.widgets.button_styles import apply_compact_button_style
from lvjiang.ui.widgets.combo_box import AutoWidthComboBox, ComboWidthMode

#: 聚合候选默认展示条数；超过这个数只能靠搜索框输入来找。
FACET_VISIBLE_LIMIT = 20

#: 弹层刚因失活被收起后，这么短时间内的再次点击算「收起」而不是「重新打开」。
_TOGGLE_GRACE_SECONDS = 0.25

#: 时间快捷档位：label 与 days（``None`` 表示全部，0 表示今天）。
_TIME_PRESETS: tuple[tuple[str, int | None], ...] = (
    (tr("全部"), None),
    (tr("今天"), 0),
    (tr("近 7 天"), 7),
    (tr("近 30 天"), 30),
    (tr("近 90 天"), 90),
    (tr("自定义"), -1),
)

#: 条件标签最多显示多宽；超出省略，全文放 tooltip。
#: 不定上限时，多几个长条件就能把对话框顶宽，正是要避免的"窗口跳动"。
_CHIP_TEXT_WIDTH = 132

_CHIP_STYLE = (
    "QFrame#historyConditionChip { border: 1px solid palette(mid); "
    "border-radius: 9px; background: palette(alternate-base); }"
    "QFrame#historyConditionChip QLabel { border: none; background: transparent; }"
    "QFrame#historyConditionChip QPushButton { border: none; background: transparent; "
    "padding: 0 2px; color: palette(mid); }"
    "QFrame#historyConditionChip QPushButton:hover { color: palette(text); }"
)


class ElidedLabel(QLabel):
    """会按当前宽度省略文字的标签，且**不参与**对话框的最小宽度。

    ``QLabel`` 的最小宽度就是整段文字的宽度，页码、合计这类随数据变长的文字
    会拖着对话框一起变宽。这里把最小宽度让到 0：空间够就整段显示，不够就省略
    成「…」，全文仍在 tooltip 里。窗口尺寸因此不再随筛选结果跳动。
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._full_text = ""

    def set_full_text(self, text: str) -> None:
        self._full_text = text
        self.setToolTip(text)
        self._apply_elide()

    def full_text(self) -> str:
        return self._full_text

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt API
        metrics = self.fontMetrics()
        # 多留几个像素：按文字宽度精确取整会让末尾一个字被省略掉。
        return QSize(metrics.horizontalAdvance(self._full_text) + 4, metrics.height())

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt API
        return QSize(0, self.fontMetrics().height())

    def resizeEvent(self, event: QResizeEvent | None) -> None:  # noqa: N802 - Qt API
        super().resizeEvent(event)
        self._apply_elide()

    def _apply_elide(self) -> None:
        super().setText(self.fontMetrics().elidedText(
            self._full_text, Qt.TextElideMode.ElideRight, max(0, self.width())))


class ConditionBar(QWidget):
    """一行可取消的条件标签。

    ``conditions`` 里每项是 ``(维度键, 展示文本, 提示)``；点 × 只发出维度键，
    由对话框决定怎么把这个维度恢复成"不限"。
    """

    removed = pyqtSignal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setStyleSheet(_CHIP_STYLE)
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(6)
        self._hint = QLabel(tr("当前显示全部记录"))
        self._hint.setEnabled(False)
        self._layout.addWidget(self._hint)
        self._layout.addStretch(1)

    def set_conditions(self, conditions: Iterable[tuple[str, str, str]]) -> None:
        """重建标签；没有条件时显示"全部记录"提示。"""
        while self._layout.count():
            item = self._layout.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                # 先隐藏再排队销毁：旧标签在下一轮事件循环前仍会画在原来的位置。
                widget.hide()
                widget.deleteLater()

        chips = list(conditions)
        self._hint = QLabel(tr("当前显示全部记录") if not chips else "")
        self._hint.setEnabled(False)
        if not chips:
            self._layout.addWidget(self._hint)
        for dimension, text, tip in chips:
            self._layout.addWidget(self._build_chip(dimension, text, tip))
        self._layout.addStretch(1)

    def _build_chip(self, dimension: str, text: str, tip: str) -> QFrame:
        chip = QFrame(self)
        chip.setObjectName("historyConditionChip")
        chip.setToolTip(tip or text)
        layout = QHBoxLayout(chip)
        layout.setContentsMargins(8, 1, 4, 1)
        layout.setSpacing(2)
        # 单条标签限宽省略：长关键词不能把整行顶宽，进而顶宽对话框。
        label = QLabel(
            self.fontMetrics().elidedText(
                text, Qt.TextElideMode.ElideRight, _CHIP_TEXT_WIDTH),
            chip,
        )
        label.setToolTip(tip or text)
        layout.addWidget(label)
        close = QPushButton("×", chip)
        close.setCursor(Qt.CursorShape.PointingHandCursor)
        close.setToolTip(tr("取消这个条件"))
        close.clicked.connect(lambda: self.removed.emit(dimension))
        layout.addWidget(close)
        return chip


class FacetFilterButton(QPushButton):
    """表头筛选行的聚合候选按钮。

    弹层里是「搜索框 + 复选列表 + 提示」。列表默认只有前 ``FACET_VISIBLE_LIMIT``
    项，提示里写明还有多少项没列出来；输入即请求调用方拉取新的候选。

    弹层是 ``Qt.Tool`` 无边框窗口，不是 ``Qt.Popup``：Windows 的输入法只把候选
    窗口挂到被激活的顶层窗口上，而 Popup 窗口从不激活，弹层里的搜索框因此打不进
    中文（2026-11-15）。代价是 Popup 原本自带的「点外面自动关」要自己补——失活
    （``WindowDeactivate``）即收起，同时保证同一次只开一个。
    """

    values_changed = pyqtSignal(tuple)
    search_requested = pyqtSignal(str)

    def __init__(
        self,
        dimension: str,
        title: str,
        parent=None,
        *,
        display: Callable[[str], str] | None = None,
    ) -> None:
        super().__init__(parent)
        self._dimension = dimension
        self._title = title
        self._display: Callable[[str], str] = display or (lambda raw: raw or tr("未记录"))
        self._values: tuple[str, ...] = ()
        self._rows: list[tuple[str, int]] = []
        self._total = 0
        self._search = ""
        self._full_text = tr("全部")
        self._suppress = False
        self._panel: QFrame | None = None
        self._search_edit: QLineEdit | None = None
        self._list: QListWidget | None = None
        self._hint: QLabel | None = None
        self._closed_at = 0.0
        apply_compact_button_style(self, variant="neutral")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.clicked.connect(self.open_panel)
        self._refresh_label()

    # ─── 调用方注入状态 ───

    @property
    def dimension(self) -> str:
        return self._dimension

    def values(self) -> tuple[str, ...]:
        return self._values

    @property
    def candidates(self) -> list[tuple[str, int]]:
        """当前展示的候选（``(取值, 条数)``，按条数倒序）。"""
        return list(self._rows)

    @property
    def candidate_total(self) -> int:
        """该维度在相同条件下的取值总数，用于"另有 N 项"提示。"""
        return self._total

    def search_text(self) -> str:
        """弹层里的候选查找词；候选刷新要沿用同一份，不能悄悄回到全量。"""
        return self._search

    def set_values(self, values: Iterable[str]) -> None:
        """程序化回填选中值；不触发 ``values_changed``。"""
        self._values = tuple(values)
        self._suppress = True
        try:
            self._rebuild_list()
        finally:
            self._suppress = False
        self._refresh_label()

    def apply_candidates(
        self, rows: Iterable[tuple[str, int]], total: int | None = None,
    ) -> None:
        """更新候选（含计数）与"另有 N 项"提示。"""
        self._rows = list(rows)
        if total is not None:
            self._total = int(total)
        self._rebuild_list()

    # ─── 弹层 ───

    def open_panel(self) -> None:
        panel = self._ensure_panel()
        if panel.isVisible():
            self.close_panel()
            return
        if time.monotonic() - self._closed_at < _TOGGLE_GRACE_SECONDS:
            # 这次点击其实是「收起」：它刚刚因为失活被收起（点按钮本身也算失活），
            # 不能再立刻弹回来，否则按钮永远关不掉面板。
            self._closed_at = 0.0
            return
        self._close_sibling_panels()
        if self._search_edit is not None:
            # 每次打开从"全部候选"起步，避免上次的查找词在关掉后仍然生效。
            self._search_edit.blockSignals(True)
            self._search_edit.clear()
            self._search_edit.blockSignals(False)
        self._search = ""
        self._rebuild_list()
        self.search_requested.emit("")
        self._resize_panel()
        panel.adjustSize()
        panel.move(self._panel_position(panel))
        panel.show()
        # 输入法只认被激活的窗口：显式激活，别指望父窗口的激活状态能传下来。
        panel.activateWindow()
        panel.raise_()
        if self._search_edit is not None:
            self._search_edit.setFocus()

    def close_panel(self) -> None:
        """收起弹层：失活、Esc、「完成」、换维度、对话框关闭都走这里。"""
        if self._panel is not None and self._panel.isVisible():
            self._panel.hide()
        self._closed_at = time.monotonic()

    def _close_sibling_panels(self) -> None:
        """同一个窗口里只留一个弹层；``Qt.Tool`` 不像 Popup 会互相顶掉。"""
        window = self.window()
        if window is None:
            return
        for other in window.findChildren(FacetFilterButton):
            if other is not self:
                other.close_panel()

    def _available_rect(self) -> QRect:
        screen = self.screen()
        return screen.availableGeometry() if screen is not None else QRect()

    def _panel_position(self, panel: QWidget) -> QPoint:
        """贴着按钮下沿展开；下面放不下就翻到上方，再不行就夹进可用屏幕。

        候选从 10 项扩到 20 项后弹层明显变高，靠窗口底部的表头必须能往上翻。
        """
        size = panel.size()
        avail = self._available_rect()
        below = self.mapToGlobal(QPoint(0, self.height()))
        y = below.y()
        if not avail.isNull() and y + size.height() > avail.bottom():
            above = self.mapToGlobal(QPoint(0, -size.height()))
            if above.y() >= avail.top():
                y = above.y()
            else:
                y = max(avail.top(), avail.bottom() - size.height())
        x = below.x()
        if not avail.isNull():
            x = min(x, avail.right() - size.width())
            x = max(avail.left(), x)
        return QPoint(x, y)

    def _resize_panel(self) -> None:
        """弹层宽度按最长候选项算，不跟着窄列宽被截断。"""
        listing = self._list
        if listing is None:
            return
        metrics = listing.fontMetrics()
        widest = max(
            [metrics.horizontalAdvance(self._display(raw)) for raw, _ in self._rows]
            + [metrics.horizontalAdvance(self._display(raw)) for raw in self._values]
            + [0],
        )
        # 「值  (1234)」还带计数与内边距，单独留出一段固定余量；
        # 搜索框的提示语也要能整句读到，否则用户不知道可以输入。
        hint_width = 0
        if self._search_edit is not None:
            hint_width = metrics.horizontalAdvance(
                self._search_edit.placeholderText()) + 48
        listing.setMinimumWidth(min(560, max(widest + 96, hint_width)))
        # 20 项在高 DPI 小屏上可能比屏幕还高：压一个不超过可用高度的上限，
        # 多出来的交给滚动条，不让弹层顶到屏幕外。
        avail = self._available_rect()
        if not avail.isNull():
            listing.setMaximumHeight(max(140, int(avail.height() * 0.6)))

    def eventFilter(self, source, event) -> bool:  # noqa: N802 - Qt API
        # Tool 窗口没有 Popup 的自动关闭：一失活（点对话框、切到别的窗口）就收起。
        if (source is self._panel and event is not None
                and event.type() == QEvent.Type.WindowDeactivate):
            self.close_panel()
        return super().eventFilter(source, event)

    def _ensure_panel(self) -> QFrame:
        if self._panel is not None:
            return self._panel
        # Tool + 无边框 = 能激活、能被输入法挂上的轻量弹层（见类说明）。
        panel = QFrame(
            self, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint)
        panel.setObjectName("historyFacetPanel")
        panel.setFrameShape(QFrame.Shape.StyledPanel)
        panel.installEventFilter(self)
        # Popup 原本按 Esc 就收起；换成 Tool 后 Esc 会落到对话框上，这里自己补。
        escape = QShortcut(QKeySequence("Esc"), panel)
        escape.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        escape.activated.connect(self.close_panel)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        search = QLineEdit(panel)
        search.setPlaceholderText(tr("搜索{title}（可输入未列出的值）").format(title=self._title))
        search.setClearButtonEnabled(True)
        search.textChanged.connect(self._on_search_changed)
        layout.addWidget(search)
        self._search_edit = search

        listing = QListWidget(panel)
        listing.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        listing.setTextElideMode(Qt.TextElideMode.ElideNone)
        # 默认列 FACET_VISIBLE_LIMIT 项，列表高度就按这么多行给，不靠滚动条硬凑；
        # 小屏上限由 _resize_panel 按可用高度压。
        listing.setMinimumHeight(
            listing.fontMetrics().height() * FACET_VISIBLE_LIMIT + 8)
        listing.itemChanged.connect(self._on_item_changed)
        layout.addWidget(listing)
        self._list = listing

        hint = QLabel(panel)
        hint.setEnabled(False)
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self._hint = hint

        buttons = QHBoxLayout()
        clear = QPushButton(tr("清除"), panel)
        clear.clicked.connect(self._clear_values)
        done = QPushButton(tr("完成"), panel)
        done.clicked.connect(self.close_panel)
        apply_compact_button_style(clear, done, variant="neutral")
        buttons.addWidget(clear)
        buttons.addStretch(1)
        buttons.addWidget(done)
        layout.addLayout(buttons)
        self._panel = panel
        return panel

    def _on_search_changed(self, text: str) -> None:
        self._search = text.strip()
        self.search_requested.emit(self._search)

    def _clear_values(self) -> None:
        if not self._values:
            return
        self._values = ()
        self._suppress = True
        try:
            self._rebuild_list()
        finally:
            self._suppress = False
        self._refresh_label()
        self.values_changed.emit(self._values)

    def _on_item_changed(self, _item: QListWidgetItem) -> None:
        if self._suppress or self._list is None:
            return
        checked = [
            str(entry.data(Qt.ItemDataRole.UserRole))
            for index in range(self._list.count())
            if (entry := self._list.item(index)) is not None
            and entry.checkState() == Qt.CheckState.Checked
        ]
        self._values = tuple(checked)
        self._refresh_label()
        self.values_changed.emit(self._values)

    def _rebuild_list(self) -> None:
        """用当前候选重建列表；已选中但不在候选里的值仍然列出，否则取消不掉。"""
        listing = self._list
        if listing is None:
            return
        self._suppress = True
        try:
            listing.clear()
            shown = set()
            for raw, count in self._rows:
                shown.add(raw)
                listing.addItem(self._make_item(raw, count))
            for raw in self._values:
                if raw not in shown:
                    listing.addItem(self._make_item(raw, None))
            if self._hint is not None:
                extra = max(0, self._total - len(self._rows))
                self._hint.setText(
                    tr("另有 {count} 项未列出，输入可搜索").format(count=extra)
                    if extra else ""
                )
        finally:
            self._suppress = False

    def _make_item(self, raw: str, count: int | None) -> QListWidgetItem:
        text = self._display(raw)
        if count is not None:
            text = f"{text}  ({count})"
        item = QListWidgetItem(text)
        item.setData(Qt.ItemDataRole.UserRole, raw)
        item.setToolTip(raw or tr("未记录"))
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        item.setCheckState(
            Qt.CheckState.Checked if raw in self._values else Qt.CheckState.Unchecked)
        return item

    # ─── 闭合状态文案 ───

    def _refresh_label(self) -> None:
        if not self._values:
            text = tr("全部")
            self.setToolTip(tr("按{title}筛选").format(title=self._title))
        elif len(self._values) == 1:
            text = self._display(self._values[0])
            self.setToolTip(f"{self._title}：{text}")
        else:
            first = self._display(self._values[0])
            text = tr("{first} +{rest}").format(
                first=first, rest=len(self._values) - 1)
            self.setToolTip(
                self._title + "：" + "、".join(self._display(v) for v in self._values))
        self._full_text = text + " ▾"
        self._apply_label()

    def _apply_label(self) -> None:
        """按实际宽度省略文字：窄列里长来源不能画成半截还看不出被截断。"""
        available = max(20, self.width() - 22)
        self.setText(self.fontMetrics().elidedText(
            self._full_text, Qt.TextElideMode.ElideRight, available))

    def resizeEvent(self, event: QResizeEvent | None) -> None:  # noqa: N802 - Qt API
        super().resizeEvent(event)
        self._apply_label()


class KeywordFilterBar(QWidget):
    """关键词输入 + 时间快捷档位与自定义区间。"""

    changed = pyqtSignal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self._keyword = QLineEdit(self)
        self._keyword.setPlaceholderText(
            tr("搜索 值 / 来源 / 用户 / 键"))
        self._keyword.setClearButtonEnabled(True)
        self._keyword.textEdited.connect(lambda _text: self.changed.emit())
        layout.addWidget(self._keyword, 1)

        layout.addWidget(QLabel(tr("时间"), self))
        self._time_combo = AutoWidthComboBox(self, width_mode=ComboWidthMode.FULL)
        for label, days in _TIME_PRESETS:
            self._time_combo.addItem(label, days)
        self._time_combo.currentIndexChanged.connect(self._on_preset_changed)
        layout.addWidget(self._time_combo)

        self._start_date = self._build_date_edit()
        self._end_date = self._build_date_edit()
        layout.addWidget(self._start_date)
        layout.addWidget(QLabel("~", self))
        layout.addWidget(self._end_date)
        self._sync_date_enabled()
        self._start_date.dateChanged.connect(lambda _date: self.changed.emit())
        self._end_date.dateChanged.connect(lambda _date: self.changed.emit())

    def _build_date_edit(self) -> QDateEdit:
        edit = QDateEdit(self)
        edit.setCalendarPopup(True)
        edit.setDisplayFormat("yyyy-MM-dd")
        edit.setDate(QDate.currentDate())
        return edit

    def keyword(self) -> str:
        return self._keyword.text().strip()

    def set_keyword(self, text: str) -> None:
        self._keyword.setText(text)

    def focus_keyword(self) -> None:
        self._keyword.setFocus()
        self._keyword.selectAll()

    def date_range(self) -> tuple[str | None, str | None]:
        """返回 ``(起, 止)``；``止`` 是"当日之后"的开区间上界。"""
        days = self._time_combo.currentData()
        if days is None:
            return None, None
        if days == -1:
            start = self._start_date.date()
            end = self._end_date.date()
        else:
            start = QDate.currentDate().addDays(-int(days))
            end = QDate.currentDate()
        return (
            start.toString("yyyy-MM-dd"),
            end.addDays(1).toString("yyyy-MM-dd"),
        )

    def time_label(self) -> str:
        label = self._time_combo.currentText()
        if self._time_combo.currentData() != -1:
            return label
        return f"{self._start_date.date().toString('yyyy-MM-dd')} ~ " \
               f"{self._end_date.date().toString('yyyy-MM-dd')}"

    def reset_time(self) -> None:
        self._time_combo.blockSignals(True)
        self._time_combo.setCurrentIndex(0)
        self._time_combo.blockSignals(False)
        self._sync_date_enabled()

    def _on_preset_changed(self) -> None:
        self._sync_date_enabled()
        self.changed.emit()

    def _sync_date_enabled(self) -> None:
        custom = self._time_combo.currentData() == -1
        self._start_date.setEnabled(custom)
        self._end_date.setEnabled(custom)


def build_scope_combo(parent=None) -> AutoWidthComboBox:
    """「全部 / 增加 / 减少 / 为零」的变动量方向下拉。"""
    combo = AutoWidthComboBox(parent, width_mode=ComboWidthMode.FULL)
    combo.addItem(tr("全部"), "all")
    combo.addItem(tr("增加"), "pos")
    combo.addItem(tr("减少"), "neg")
    combo.addItem(tr("为零"), "zero")
    return combo


def build_text_filter(parent=None, placeholder: str = "") -> QLineEdit:
    """表头筛选行里的文本包含输入框。"""
    edit = QLineEdit(parent)
    edit.setPlaceholderText(placeholder or tr("包含"))
    edit.setClearButtonEnabled(True)
    return edit
