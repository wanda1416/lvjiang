"""脚本配置对话框 - 管理日常页暴露哪些脚本、顺序、显示名、脚本性质

从「工具 → 脚本配置」打开。

脚本本体（.wf 文件 + 内置类实现）由发现层 ``discover_scripts()`` 自动扫描，
本对话框只负责「暴露」：勾选是否在日常下拉展示、调整顺序、覆盖显示名、
设置脚本性质（日常 / 专用）。
保存写入 session 的 ``daily.scripts`` 节点——顺序、勾选、显示名、性质都是
**用户偏好**，不写回系统配置：写回去会把系统后续新增的脚本冻住。

脚本性质：
- 日常（daily）：日常 Tab 负责绘制参数面板 + 读写参数
- 专用（dedicated）：日常 Tab 不碰其配置，不画参数面板；
  由专属配置页面自行管理，执行引擎从 wf_configs 自行加载

参数本身不在此编辑（来自 .wf front-matter 或内置类属性，由源头维护）。
"""
from __future__ import annotations

from loguru import logger
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFontMetrics
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from ...i18n import tr
from ...workflows.discovery import discover_scripts
from ...workflows.policy import WorkflowDiscoveryPolicy as Policy
from ...workflows.preferences import load_preferences, save_preferences
from ..button_styles import apply_button_style


class ScriptConfigDialog(QDialog):
    """脚本配置：勾选暴露 + 调整顺序 + 覆盖显示名"""

    # 列索引
    COL_EXPOSE = 0
    COL_NAME = 1
    COL_SCOPE = 2
    COL_SOURCE = 3
    COL_ID = 4
    COL_PARAMS = 5

    # 脚本性质选项
    SCOPE_LABELS = {"daily": tr("日常"), "dedicated": tr("专用")}

    def __init__(self, main_window):
        super().__init__(main_window)
        self._main = main_window
        self.setWindowTitle(tr("脚本配置"))
        self.setMinimumSize(640, 480)
        # id -> 发现层原始显示名（用于判断是否需要写 overrides）
        self._base_names: dict[str, str] = {}
        self._scripts: dict[str, dict] = {}
        self._setup_ui()
        self._load()

    # ─── UI ─────────────────────────────────────────────
    def _setup_ui(self):
        layout = QVBoxLayout(self)

        hint = QLabel(
            tr("勾选「暴露」决定日常页下拉是否展示；「脚本性质」决定日常 Tab 是否管理参数："
               "日常 = 日常页绘制参数面板并读写配置；专用 = 日常页不碰，由专属页面管理。"
               "专用脚本默认不暴露，需要时可显式勾选。"
               "用上移/下移调整暴露顺序；显示名可双击编辑（留空恢复默认）。")
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: palette(mid);")
        layout.addWidget(hint)

        self._table = QTableWidget(0, 6)
        self._table.setHorizontalHeaderLabels(
            [tr("暴露"), tr("显示名"), tr("脚本性质"), tr("来源"), "id", tr("参数数")])
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        header = self._table.horizontalHeader()
        fm = QFontMetrics(header.font())
        header.setMinimumSectionSize(fm.horizontalAdvance("测") * 3)
        for col in range(self._table.columnCount()):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self._table)

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
        self._btn_cancel = QPushButton(tr("取消"))
        self._btn_cancel.clicked.connect(self.reject)
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

    # ─── 数据加载 ────────────────────────────────────────
    def _load(self):
        scripts = {s["id"]: s for s in discover_scripts()}
        self._scripts = scripts
        self._base_names = {sid: s["name"] for sid, s in scripts.items()}
        prefs = load_preferences()

        # 排序：用户调过的顺序在前，其余按 id 追加
        ordered_ids = [i for i in prefs.order if i in scripts]
        ordered_ids += sorted(i for i in scripts if i not in ordered_ids)

        self._table.setRowCount(len(ordered_ids))
        for row, sid in enumerate(ordered_ids):
            cfg = scripts[sid]
            scope = prefs.scopes.get(sid) or cfg.get("scope") or "daily"
            # 勾选状态：用户明确改过则以用户为准，否则用作者声明的默认可见性
            checked = prefs.visible.get(
                sid,
                Policy.visible_by_default(
                    hidden=bool(cfg.get("hidden", False)), scope=scope),
            )
            self._fill_row(
                row, cfg, checked=checked,
                display=prefs.names.get(sid) or cfg["name"],
                scope=scope)

    def _fill_row(self, row: int, script: dict, checked: bool, display: str,
                  scope: str = "daily"):
        sid = script["id"]

        expose_item = QTableWidgetItem()
        expose_item.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled |
                             Qt.ItemFlag.ItemIsSelectable)
        expose_item.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
        self._table.setItem(row, self.COL_EXPOSE, expose_item)

        name_item = QTableWidgetItem(display)
        name_item.setData(Qt.ItemDataRole.UserRole, sid)  # 行标识：脚本 id
        self._table.setItem(row, self.COL_NAME, name_item)

        # 脚本性质：下拉框（日常 / 专用）
        scope_combo = QComboBox()
        for key, label in self.SCOPE_LABELS.items():
            scope_combo.addItem(tr(label), key)
        scope_combo.setCurrentIndex(max(scope_combo.findData(scope), 0))
        scope_combo.currentIndexChanged.connect(
            lambda _index, combo=scope_combo, item=expose_item, cfg=script:
            item.setCheckState(
                Qt.CheckState.Checked
                if Policy.visible_by_default(
                    hidden=bool(cfg.get("hidden", False)),
                    scope=combo.currentData() or "daily",
                )
                else Qt.CheckState.Unchecked
            )
        )
        self._table.setCellWidget(row, self.COL_SCOPE, scope_combo)

        source = f".wf: {script['wf_file']}" if script.get("wf_file") else f"内置类: {script['class']}"
        source_item = QTableWidgetItem(source)
        source_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
        self._table.setItem(row, self.COL_SOURCE, source_item)

        id_item = QTableWidgetItem(sid)
        id_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
        self._table.setItem(row, self.COL_ID, id_item)

        count_item = QTableWidgetItem(str(len(script.get("parameters") or [])))
        count_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
        count_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        self._table.setItem(row, self.COL_PARAMS, count_item)

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

    def _swap_rows(self, a: int, b: int):
        # QTableWidget 拥有 setCellWidget() 放入的控件。不能把现有 QComboBox
        # remove 后再塞回另一格：这会跨越 Qt 的所有权/延迟销毁边界，Windows
        # 上曾在随后的 setCurrentCell() 触发 native access violation。
        # 先按值快照，再用新 item/widget 原地重建两行，不复用任何旧包装器。
        state_a = self._row_state(a)
        state_b = self._row_state(b)
        self._restore_row(a, state_b)
        self._restore_row(b, state_a)

    def _row_state(self, row: int) -> tuple[str, bool, str, str]:
        name_item = self._table.item(row, self.COL_NAME)
        expose_item = self._table.item(row, self.COL_EXPOSE)
        scope_combo: QComboBox = self._table.cellWidget(row, self.COL_SCOPE)
        return (
            str(name_item.data(Qt.ItemDataRole.UserRole)),
            expose_item.checkState() == Qt.CheckState.Checked,
            name_item.text(),
            str(scope_combo.currentData() or "daily"),
        )

    def _restore_row(
        self, row: int, state: tuple[str, bool, str, str],
    ) -> None:
        sid, checked, display, scope = state
        self._fill_row(
            row, self._scripts[sid], checked=checked,
            display=display, scope=scope,
        )

    # ─── 读写用户偏好（session.daily.scripts）──────────────

    def _on_save(self):
        """把顺序/勾选/显示名/性质写进 session 的用户偏好

        这些都是**用户偏好**，不写回系统配置：写回去会把系统后续新增的脚本
        冻住，用户除非删掉本地配置否则再也看不到新脚本。
        可见性只记「与作者声明不同」的项，系统新增脚本因此自动出现。
        """
        scripts = getattr(self, "_scripts", {})
        order: list[str] = []
        visible: dict[str, bool] = {}
        names: dict[str, str] = {}
        scopes: dict[str, str] = {}
        for row in range(self._table.rowCount()):
            name_item = self._table.item(row, self.COL_NAME)
            sid = name_item.data(Qt.ItemDataRole.UserRole)
            order.append(sid)
            cfg = scripts.get(sid) or {}

            checked = (self._table.item(row, self.COL_EXPOSE).checkState()
                       == Qt.CheckState.Checked)

            scope_combo: QComboBox = self._table.cellWidget(row, self.COL_SCOPE)
            scope = scope_combo.currentData() if scope_combo else "daily"
            default_visible = Policy.visible_by_default(
                hidden=bool(cfg.get("hidden", False)), scope=scope)
            if checked != default_visible:
                visible[sid] = checked      # 与作者声明的默认值相反才记

            display = (name_item.text() or "").strip()
            if display and display != self._base_names.get(sid, ""):
                names[sid] = display

            if scope and scope != (cfg.get("scope") or "daily"):
                scopes[sid] = scope

        save_preferences(order, visible, names, scopes)
        logger.info(
            f"日常脚本偏好已保存：{len(order)} 项，"
            f"可见性覆盖 {len(visible)}，改名 {len(names)}，性质覆盖 {len(scopes)}")
        self.accept()
