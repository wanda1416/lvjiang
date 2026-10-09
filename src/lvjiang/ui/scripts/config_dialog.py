"""脚本配置对话框 - 按元数据性质分组管理展示、顺序和显示名

作为「工具 → 脚本编辑」工作台中的配置页使用；独立对话框外壳仅供兼容。

脚本本体（.wf 文件 + 内置类实现）由发现层 ``discover_scripts()`` 自动扫描，
本对话框按元数据声明分为日常、专用两页，只负责「暴露」：
勾选是否在日常下拉展示、调整同类脚本顺序、覆盖显示名。
保存写入 session 的 ``daily.scripts`` 节点——顺序、勾选、显示名都是
**用户偏好**，不写回系统配置：写回去会把系统后续新增的脚本冻住。

脚本性质：
- 日常（daily）：日常 Tab 负责绘制参数面板 + 读写参数
- 专用（dedicated）：日常 Tab 不碰其配置，不画参数面板；
  由专属配置页面自行管理，执行引擎从 wf_configs 自行加载

参数本身不在此编辑（来自 .wf front-matter 或内置类属性，由源头维护）。
"""
from __future__ import annotations

from loguru import logger
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFontMetrics
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
)

from ...i18n import tr
from ...workflows.discovery import discover_scripts, script_display_name
from ...workflows.policy import WorkflowDiscoveryPolicy as Policy
from ...workflows.preferences import load_preferences, save_preferences
from ..button_styles import apply_button_style


class ScriptConfigDialog(QDialog):
    """脚本配置：勾选暴露 + 调整顺序 + 覆盖显示名"""

    # 列索引
    COL_EXPOSE = 0
    COL_NAME = 1
    COL_SOURCE = 2
    COL_ID = 3
    COL_PARAMS = 4

    TAB_SCOPES = ("daily", "dedicated")
    REMOTE_PREFIX = "[远程] "

    preferences_saved = pyqtSignal()

    def __init__(self, main_window, *, embedded: bool = False):
        super().__init__(main_window)
        self._main = main_window
        self._embedded = embedded
        self._loading = False
        self._dirty = False
        if embedded:
            self.setWindowFlags(Qt.WindowType.Widget)
        self.setWindowTitle(tr("脚本配置"))
        self.setMinimumSize(640, 480)
        # id -> 发现层原始显示名（用于判断是否需要写 overrides）
        self._base_names: dict[str, str] = {}
        self._scripts: dict[str, dict] = {}
        self._ordered_ids: list[str] = []
        self._setup_ui()
        self._load()

    # ─── UI ─────────────────────────────────────────────
    def _setup_ui(self):
        layout = QVBoxLayout(self)

        self._tabs = QTabWidget()
        self._tables: dict[str, QTableWidget] = {}
        for scope, label in zip(self.TAB_SCOPES, (tr("日常"), tr("专用")), strict=True):
            table = QTableWidget(0, 5)
            table.setHorizontalHeaderLabels(
                [tr("暴露"), tr("显示名"), tr("来源"), "id", tr("参数数")])
            table.verticalHeader().setVisible(False)
            table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
            table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
            header = table.horizontalHeader()
            fm = QFontMetrics(header.font())
            header.setMinimumSectionSize(fm.horizontalAdvance("测") * 3)
            for col in range(table.columnCount()):
                header.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
            table.itemChanged.connect(self._mark_dirty)
            table.itemSelectionChanged.connect(self._refresh_buttons)
            self._tables[scope] = table
            self._tabs.addTab(table, label)
        self._tabs.currentChanged.connect(self._refresh_buttons)
        layout.addWidget(self._tabs)

        # 底部单行操作栏：顺序调整居左，保存/取消居右
        btn_bar = QHBoxLayout()
        self._btn_up = QPushButton(tr("上移"))
        self._btn_up.clicked.connect(lambda: self._move_row(-1))
        self._btn_down = QPushButton(tr("下移"))
        self._btn_down.clicked.connect(lambda: self._move_row(1))
        btn_bar.addWidget(self._btn_up)
        btn_bar.addWidget(self._btn_down)
        btn_bar.addStretch()
        self._btn_save = QPushButton(tr("保存"))
        self._btn_save.clicked.connect(self._on_save)
        self._btn_cancel = QPushButton(tr("撤销") if self._embedded else tr("取消"))
        self._btn_cancel.clicked.connect(self._on_undo if self._embedded else self.reject)
        apply_button_style(self._btn_save)
        apply_button_style(
            self._btn_up,
            self._btn_down,
            self._btn_cancel,
            variant="neutral",
        )
        btn_bar.addWidget(self._btn_save)
        btn_bar.addWidget(self._btn_cancel)
        layout.addLayout(btn_bar)
        self._refresh_buttons()

    @property
    def _table(self) -> QTableWidget:
        return self._tables[self.TAB_SCOPES[self._tabs.currentIndex()]]

    @staticmethod
    def _script_scope(script: dict) -> str:
        return "dedicated" if script.get("scope") == "dedicated" else "daily"

    # ─── 数据加载 ────────────────────────────────────────
    def _load(self):
        self._loading = True
        scripts = {s["id"]: s for s in discover_scripts()}
        self._scripts = scripts
        self._base_names = {sid: s["name"] for sid, s in scripts.items()}
        prefs = load_preferences()

        # 排序：用户调过的顺序在前，其余按 id 追加
        ordered_ids = [i for i in prefs.order if i in scripts]
        ordered_ids += sorted(i for i in scripts if i not in ordered_ids)

        self._ordered_ids = ordered_ids
        for scope, table in self._tables.items():
            ids = [sid for sid in ordered_ids if self._script_scope(scripts[sid]) == scope]
            table.setRowCount(len(ids))
            for row, sid in enumerate(ids):
                cfg = scripts[sid]
                checked = prefs.visible.get(
                    sid,
                    Policy.visible_by_default(
                        hidden=bool(cfg.get("hidden", False)), scope=scope),
                )
                self._fill_row(
                    table, row, cfg, checked=checked,
                    display=prefs.names.get(sid) or cfg["name"])
        self._loading = False
        self._dirty = False
        self._refresh_buttons()

    def _fill_row(self, table: QTableWidget, row: int, script: dict,
                  checked: bool, display: str):
        sid = script["id"]

        expose_item = QTableWidgetItem()
        expose_item.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled |
                             Qt.ItemFlag.ItemIsSelectable)
        expose_item.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
        table.setItem(row, self.COL_EXPOSE, expose_item)

        display_cfg = {**script, "name": display}
        name_item = QTableWidgetItem(script_display_name(display_cfg))
        name_item.setData(Qt.ItemDataRole.UserRole, sid)  # 行标识：脚本 id
        table.setItem(row, self.COL_NAME, name_item)

        if script.get("wf_file"):
            prefix = "[远程] " if script.get("is_remote") else ""
            source = f"{prefix}.wf: {script['wf_file']}"
        else:
            source = f"内置类: {script['class']}"
        source_item = QTableWidgetItem(source)
        source_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
        table.setItem(row, self.COL_SOURCE, source_item)

        id_item = QTableWidgetItem(sid)
        id_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
        table.setItem(row, self.COL_ID, id_item)

        count_item = QTableWidgetItem(str(len(script.get("parameters") or [])))
        count_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
        count_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        table.setItem(row, self.COL_PARAMS, count_item)

    # ─── 顺序调整 ────────────────────────────────────────
    def _move_row(self, delta: int):
        row = self._table.currentRow()
        if row < 0:
            return
        target = row + delta
        if target < 0 or target >= self._table.rowCount():
            return
        # 逐行取出各列内容重建，交换 row 与 target
        self._swap_rows(row, target)
        self._table.setCurrentCell(target, self.COL_NAME)
        self._mark_dirty()

    def _mark_dirty(self, *_args) -> None:
        if self._loading:
            return
        self._dirty = True
        self._refresh_buttons()

    def _refresh_buttons(self, *_args) -> None:
        if not hasattr(self, "_btn_save"):
            return
        if self._embedded:
            self._btn_save.setEnabled(self._dirty)
            self._btn_cancel.setEnabled(self._dirty)
        row = self._table.currentRow()
        self._btn_up.setEnabled(row > 0)
        self._btn_down.setEnabled(0 <= row < self._table.rowCount() - 1)

    def _on_undo(self) -> None:
        if self._dirty:
            self._load()

    def _swap_rows(self, a: int, b: int):
        state_a = self._row_state(a)
        state_b = self._row_state(b)
        self._restore_row(a, state_b)
        self._restore_row(b, state_a)

    def _row_state(self, row: int) -> tuple[str, bool, str]:
        name_item = self._table.item(row, self.COL_NAME)
        expose_item = self._table.item(row, self.COL_EXPOSE)
        assert name_item is not None and expose_item is not None
        sid = str(name_item.data(Qt.ItemDataRole.UserRole))
        return (
            sid,
            expose_item.checkState() == Qt.CheckState.Checked,
            self._raw_display(name_item.text(), sid),
        )

    def _raw_display(self, text: str, sid: str) -> str:
        """展示前缀不是用户改名的一部分，保存/换行时必须剥离。"""
        if (self._scripts.get(sid) or {}).get("is_remote") \
                and text.startswith(self.REMOTE_PREFIX):
            return text[len(self.REMOTE_PREFIX):]
        return text

    def _restore_row(
        self, row: int, state: tuple[str, bool, str],
    ) -> None:
        sid, checked, display = state
        self._fill_row(
            self._table, row, self._scripts[sid], checked=checked,
            display=display,
        )

    # ─── 读写用户偏好（session.daily.scripts）──────────────

    def _on_save(self):
        """把两页的顺序/勾选/显示名写进 session 的用户偏好

        这些都是**用户偏好**，不写回系统配置：写回去会把系统后续新增的脚本
        冻住，用户除非删掉本地配置否则再也看不到新脚本。
        可见性只记「与作者声明不同」的项，系统新增脚本因此自动出现。
        """
        scripts = getattr(self, "_scripts", {})
        visible: dict[str, bool] = {}
        names: dict[str, str] = {}
        grouped_order: dict[str, list[str]] = {}
        for scope, table in self._tables.items():
            grouped_order[scope] = []
            for row in range(table.rowCount()):
                name_item = table.item(row, self.COL_NAME)
                sid = str(name_item.data(Qt.ItemDataRole.UserRole))
                grouped_order[scope].append(sid)
                cfg = scripts[sid]
                checked = (table.item(row, self.COL_EXPOSE).checkState()
                           == Qt.CheckState.Checked)
                default_visible = Policy.visible_by_default(
                    hidden=bool(cfg.get("hidden", False)), scope=scope)
                if checked != default_visible:
                    visible[sid] = checked
                display = self._raw_display(name_item.text() or "", sid).strip()
                if display and display != self._base_names.get(sid, ""):
                    names[sid] = display

        # 只替换同类脚本的顺序，保留专用/日常在原入口列表中的交错位置。
        iterators = {scope: iter(ids) for scope, ids in grouped_order.items()}
        order = [next(iterators[self._script_scope(scripts[sid])])
                 for sid in self._ordered_ids]
        save_preferences(order, visible, names)
        self._ordered_ids = order
        logger.info(
            f"日常脚本偏好已保存：{len(order)} 项，"
            f"可见性覆盖 {len(visible)}，改名 {len(names)}")
        self._dirty = False
        self._refresh_buttons()
        self.preferences_saved.emit()
        if not self._embedded:
            self.accept()

    @property
    def dirty(self) -> bool:
        return self._dirty
