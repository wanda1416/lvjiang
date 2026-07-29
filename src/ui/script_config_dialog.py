"""脚本配置对话框 - 管理日常页暴露哪些脚本、顺序、显示名

从「工具 → 脚本配置」打开，作为手写 workflows.yaml 的可视化替代方案。

脚本本体（.wf 文件 + 内置类实现）由发现层 ``discover_scripts()`` 自动扫描，
本对话框只负责「暴露」：勾选是否在日常下拉展示、调整顺序、覆盖显示名。
保存后写回 ``config/system/workflows.yaml`` 的 ``exposed`` + ``overrides``。

参数本身不在此编辑（来自 .wf front-matter 或内置类属性，由源头维护）。
"""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QAbstractItemView, QHeaderView,
    QMessageBox,
)
from loguru import logger

import yaml

from src.constants import SYSTEM_CONFIG_DIR
from ..workflows.discovery import discover_scripts

_YAML_HEADER = """\
# 脚本暴露配置
#
# 脚本本体（.wf 文件 + 内置类实现）由发现层自动扫描（见 src/workflows/discovery.py）：
#   - config/system/workflows/ 顶层 *.wf（跳过 _ 前缀与子目录），name/参数取自 #% front-matter
#   - 已注册内置类实现，name/参数取自类属性 DISPLAY_NAME / PARAMETERS
# 本文件只负责「暴露」：决定日常页下拉展示哪些脚本、顺序、以及可选的显示名覆盖。
# 可在「工具 → 脚本配置」中可视化编辑本文件。
#
# 语义：exposed 缺失/为空 → 展示全部已发现脚本；否则仅展示且按其顺序。

"""


class ScriptConfigDialog(QDialog):
    """脚本配置：勾选暴露 + 调整顺序 + 覆盖显示名"""

    # 列索引
    COL_EXPOSE = 0
    COL_NAME = 1
    COL_SOURCE = 2
    COL_ID = 3
    COL_PARAMS = 4

    def __init__(self, main_window):
        super().__init__(main_window)
        self._main = main_window
        self.setWindowTitle("脚本配置")
        self.setMinimumSize(640, 480)
        # id -> 发现层原始显示名（用于判断是否需要写 overrides）
        self._base_names: dict[str, str] = {}
        self._setup_ui()
        self._load()

    # ─── UI ─────────────────────────────────────────────
    def _setup_ui(self):
        layout = QVBoxLayout(self)

        hint = QLabel(
            "勾选「暴露」决定日常页下拉是否展示；用上移/下移调整暴露顺序；"
            "显示名可双击编辑（留空恢复默认）。参数来自脚本源头，此处不编辑。"
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #666;")
        layout.addWidget(hint)

        self._table = QTableWidget(0, 5)
        self._table.setHorizontalHeaderLabels(
            ["暴露", "显示名", "来源", "id", "参数数"])
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(self.COL_NAME, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(self.COL_SOURCE, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self._table)

        # 顺序调整按钮
        order_bar = QHBoxLayout()
        self._btn_up = QPushButton("上移")
        self._btn_up.clicked.connect(lambda: self._move_row(-1))
        self._btn_down = QPushButton("下移")
        self._btn_down.clicked.connect(lambda: self._move_row(1))
        order_bar.addWidget(self._btn_up)
        order_bar.addWidget(self._btn_down)
        order_bar.addStretch()
        layout.addLayout(order_bar)

        # 底部保存/取消
        btn_bar = QHBoxLayout()
        btn_bar.addStretch()
        btn_save = QPushButton("保存")
        btn_save.setStyleSheet(
            "background-color: #4CAF50; color: white; font-weight: bold; padding: 6px 16px;")
        btn_save.clicked.connect(self._on_save)
        btn_cancel = QPushButton("取消")
        btn_cancel.clicked.connect(self.reject)
        btn_bar.addWidget(btn_save)
        btn_bar.addWidget(btn_cancel)
        layout.addLayout(btn_bar)

    # ─── 数据加载 ────────────────────────────────────────
    def _load(self):
        scripts = {s["id"]: s for s in discover_scripts()}
        self._base_names = {sid: s["name"] for sid, s in scripts.items()}
        exposed, overrides = self._read_yaml()

        # 排序：先按 exposed 顺序（勾选），再追加未暴露项（不勾选）
        exposed_valid = [i for i in exposed if i in scripts]
        rest = sorted(i for i in scripts if i not in exposed_valid)
        ordered_ids = exposed_valid + rest
        exposed_set = set(exposed_valid)

        self._table.setRowCount(len(ordered_ids))
        for row, sid in enumerate(ordered_ids):
            s = scripts[sid]
            self._fill_row(row, s, checked=sid in exposed_set,
                           display=(overrides.get(sid) or {}).get("name") or s["name"])

    def _fill_row(self, row: int, script: dict, checked: bool, display: str):
        sid = script["id"]

        expose_item = QTableWidgetItem()
        expose_item.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled |
                             Qt.ItemFlag.ItemIsSelectable)
        expose_item.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
        self._table.setItem(row, self.COL_EXPOSE, expose_item)

        name_item = QTableWidgetItem(display)
        name_item.setData(Qt.ItemDataRole.UserRole, sid)  # 行标识：脚本 id
        self._table.setItem(row, self.COL_NAME, name_item)

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
        for col in range(self._table.columnCount()):
            item_a = self._table.takeItem(a, col)
            item_b = self._table.takeItem(b, col)
            self._table.setItem(a, col, item_b)
            self._table.setItem(b, col, item_a)

    # ─── 读写 workflows.yaml ─────────────────────────────
    def _yaml_path(self):
        return SYSTEM_CONFIG_DIR / "workflows.yaml"

    def _read_yaml(self) -> tuple[list, dict]:
        path = self._yaml_path()
        if not path.exists():
            return [], {}
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception as e:
            logger.error(f"读取脚本暴露配置失败: {e}")
            return [], {}
        return (data.get("exposed") or []), (data.get("overrides") or {})

    def _on_save(self):
        exposed: list[str] = []
        overrides: dict[str, dict] = {}
        for row in range(self._table.rowCount()):
            name_item = self._table.item(row, self.COL_NAME)
            sid = name_item.data(Qt.ItemDataRole.UserRole)
            if self._table.item(row, self.COL_EXPOSE).checkState() == Qt.CheckState.Checked:
                exposed.append(sid)
            display = (name_item.text() or "").strip()
            base = self._base_names.get(sid, "")
            if display and display != base:
                overrides[sid] = {"name": display}

        text = _YAML_HEADER
        text += yaml.safe_dump({"exposed": exposed}, allow_unicode=True,
                               default_flow_style=False, sort_keys=False)
        text += "\n"
        if overrides:
            text += yaml.safe_dump({"overrides": overrides}, allow_unicode=True,
                                   default_flow_style=False, sort_keys=False)
        else:
            text += "overrides: {}\n"

        try:
            self._yaml_path().write_text(text, encoding="utf-8")
        except OSError as e:
            QMessageBox.warning(self, "保存失败", f"写入 workflows.yaml 失败：{e}")
            return
        logger.info(f"脚本暴露配置已保存：exposed={exposed}")
        self.accept()
