"""脚本编辑 → 内置函数：浏览全部可用函数、定位本地脚本、试算返回值。

数据有两段来源，合并在同一棵树里：

- 源码与已加载插件：读注册表元数据（打包成 exe 后依然有效，因为元数据随
  ``@builtin_func`` 进了函数对象）；
- ``config/local/workflows`` 下的 .py：AST 静态扫描，**不 import**——管理界面
  只是"看一眼"，没有理由为此执行用户代码。试算是显式动作，才真正加载。
"""

from __future__ import annotations

import json
import types
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QBrush, QColor
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ...core.config.resolver import get_resolver
from ...i18n import tr
from ...workflows.builtins import (
    capture_registrations,
    get_function,
    iter_function_metas,
    list_functions,
    signature_params,
    validate_function_meta,
)
from ...workflows.builtins.script_meta import scan_directory, script_problems
from ..button_styles import apply_compact_button_style

#: 元数据问题与重名冲突的提示色
_WARN_COLOR = "#c62828"


@dataclass
class FunctionEntry:
    """面板里的一行：内置函数或本地脚本函数。"""

    name: str
    group: str
    summary: str
    #: (参数名, 默认值, 类型, 含义)，顺序与签名一致
    params: list[tuple[str, str, str, str]] = field(default_factory=list)
    returns: str = ""
    source: str = ""
    injected: str | None = None
    script_path: Path | None = None
    rel_path: str = ""
    line: int = 0
    fn: Any = None
    issues: list[str] = field(default_factory=list)

    @property
    def is_script(self) -> bool:
        return self.script_path is not None


class BuiltinFunctionPanel(QWidget):
    """内置函数浏览面板。"""

    #: 请求编辑器跳到本地脚本的某一行
    locate_requested = pyqtSignal(str, int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._entries: list[FunctionEntry] = []
        self._current: FunctionEntry | None = None
        self._param_edits: list[tuple[str, QLineEdit]] = []
        self._build_ui()
        self.reload()

    # ─── 数据装载 ─────────────────────────────────────────

    def reload(self) -> None:
        """重建列表；脚本改动后调用即可刷新。"""
        self._entries = self._collect_builtins() + self._collect_scripts()
        self._rebuild_tree()
        self._update_stats()

    def _collect_builtins(self) -> list[FunctionEntry]:
        entries: list[FunctionEntry] = []
        for meta in iter_function_metas():
            fn = get_function(meta.name)
            if fn is None:
                continue
            params = [
                (spec.name, spec.default, spec.annotation, spec.meaning)
                for spec in signature_params(fn)
            ]
            entries.append(FunctionEntry(
                name=meta.name,
                group=meta.group,
                summary=meta.summary,
                params=params,
                returns=meta.returns,
                source=self._source_label(meta.module),
                injected=getattr(fn, "_inject", None),
                fn=fn,
                issues=validate_function_meta(meta.name),
            ))
        return entries

    def _collect_scripts(self) -> list[FunctionEntry]:
        root = self._script_root()
        builtin_names = set(list_functions())
        entries: list[FunctionEntry] = []
        for scanned in scan_directory(root):
            issues: list[str] = []
            if scanned.name in builtin_names:
                issues.append(tr("与内置函数重名，脚本运行时会被拒绝"))
            params = [
                (label, default, annotation, scanned.params.get(label, ""))
                for label, default, annotation in scanned.param_specs
            ]
            entries.append(FunctionEntry(
                name=scanned.name,
                group=scanned.group,
                summary=scanned.summary,
                params=params,
                returns=scanned.returns,
                source=f"{tr('本地脚本')} · {scanned.rel_path}:{scanned.line}",
                injected=scanned.injected,
                script_path=scanned.path,
                rel_path=scanned.rel_path,
                line=scanned.line,
                issues=issues,
            ))
        return entries

    @staticmethod
    def _script_root() -> Path:
        return get_resolver().local_dir / "workflows"

    @staticmethod
    def _source_label(module: str) -> str:
        if not module:
            return tr("内置")
        parts = module.split(".")
        if "apps" in parts:
            index = parts.index("apps")
            tail = [p for p in parts[index + 1:] if p not in {"workflows", "builtins"}]
            return f"{tr('插件')} · {'/'.join(tail)}"
        return f"{tr('内置')} · {parts[-1]}"

    # ─── 左侧树 ───────────────────────────────────────────

    def _rebuild_tree(self) -> None:
        self.tree.clear()
        for is_script, title in ((False, tr("内置函数")), (True, tr("本地脚本"))):
            bucket = [entry for entry in self._entries if entry.is_script is is_script]
            if not bucket:
                continue
            root_item = QTreeWidgetItem([f"{title}（{len(bucket)}）"])
            self.tree.addTopLevelItem(root_item)
            groups: dict[str, list[FunctionEntry]] = {}
            for entry in bucket:
                groups.setdefault(entry.group, []).append(entry)
            for group_name in sorted(groups):
                group_item = QTreeWidgetItem([f"{group_name}（{len(groups[group_name])}）"])
                root_item.addChild(group_item)
                for entry in sorted(groups[group_name], key=lambda item: item.name):
                    item = QTreeWidgetItem([entry.name])
                    item.setData(0, Qt.ItemDataRole.UserRole, entry)
                    if entry.issues:
                        item.setForeground(0, QBrush(QColor(_WARN_COLOR)))
                        item.setToolTip(0, "\n".join(entry.issues))
                    group_item.addChild(item)
            root_item.setExpanded(True)

    def _apply_filter(self, text: str) -> None:
        keyword = text.strip().lower()
        for index in range(self.tree.topLevelItemCount()):
            root_item = self.tree.topLevelItem(index)
            if root_item is None:
                continue
            root_visible = False
            for group_index in range(root_item.childCount()):
                group_item = root_item.child(group_index)
                if group_item is None:
                    continue
                group_visible = False
                for item_index in range(group_item.childCount()):
                    item = group_item.child(item_index)
                    if item is None:
                        continue
                    entry = item.data(0, Qt.ItemDataRole.UserRole)
                    matched = (
                        not keyword
                        or keyword in entry.name.lower()
                        or keyword in entry.summary.lower()
                    )
                    item.setHidden(not matched)
                    group_visible = group_visible or matched
                group_item.setHidden(not group_visible)
                root_visible = root_visible or group_visible
            root_item.setHidden(not root_visible)

    def _on_current_changed(self, current: QTreeWidgetItem | None, _previous) -> None:
        entry = current.data(0, Qt.ItemDataRole.UserRole) if current is not None else None
        if not isinstance(entry, FunctionEntry):
            self._current = None
            return
        self._current = entry
        self._show_entry(entry)

    # ─── 右侧详情 ─────────────────────────────────────────

    def _show_entry(self, entry: FunctionEntry) -> None:
        self.title.setText(entry.name)
        self.signature_label.setText(self._signature_text(entry))
        self.summary_label.setText(entry.summary or tr("（无说明）"))
        self.group_label.setText(f"{tr('分组')}：{entry.group}")
        self.returns_label.setText(f"{tr('返回')}：{entry.returns or tr('（未声明）')}")
        self.source_label.setText(f"{tr('来源')}：{entry.source}")
        self.issues_label.setText("\n".join(entry.issues))
        self.issues_label.setVisible(bool(entry.issues))
        self.locate_button.setEnabled(entry.is_script)

        self.params_table.setRowCount(len(entry.params))
        for row, (name, default, annotation, meaning) in enumerate(entry.params):
            for column, text in enumerate((name, annotation, default, meaning)):
                self.params_table.setItem(row, column, QTableWidgetItem(text))
        self._rebuild_param_inputs(entry)

    @staticmethod
    def _signature_text(entry: FunctionEntry) -> str:
        labels = []
        for name, default, _annotation, _meaning in entry.params:
            labels.append(name if not default else f"{name} = {default}")
        suffix = f" → {entry.returns}" if entry.returns else ""
        return f"{entry.name}({', '.join(labels)}){suffix}"

    def _rebuild_param_inputs(self, entry: FunctionEntry) -> None:
        while self.params_form.rowCount():
            self.params_form.removeRow(0)
        self._param_edits = []
        for name, default, _annotation, _meaning in entry.params:
            if name.startswith("*"):
                continue  # 变参无法逐个填，试算时自行展开
            edit = QLineEdit()
            edit.setPlaceholderText(default or tr("留空使用默认值"))
            self.params_form.addRow(name, edit)
            self._param_edits.append((name, edit))
        can_preview = entry.injected is None
        self.preview_button.setEnabled(can_preview)
        self.preview_button.setToolTip(
            "" if can_preview
            else tr("该函数需要引擎注入（_engine/_wf），只能在脚本执行时调用")
        )
        self.preview_output.clear()

    def _run_preview(self) -> None:
        entry = self._current
        if entry is None:
            return
        try:
            fn = entry.fn if entry.fn is not None else self._load_script_function(entry)
        except Exception as exc:  # noqa: BLE001 - 脚本错误要原样显示给用户
            self.preview_output.setPlainText(f"{type(exc).__name__}: {exc}")
            return
        if fn is None:
            self.preview_output.setPlainText(tr("找不到该函数"))
            return
        args: list[Any] = []
        try:
            for _name, edit in self._param_edits:
                text = edit.text().strip()
                if not text:
                    break  # 之后的位置参数走默认值
                args.append(_parse_literal(text))
        except ValueError as exc:
            self.preview_output.setPlainText(f"{tr('参数解析失败')}：{exc}")
            return
        try:
            result = fn(*args)
            self.preview_output.setPlainText(f"{type(result).__name__}: {result!r}")
        except Exception as exc:  # noqa: BLE001 - 试算失败本身就是给用户看的信息
            self.preview_output.setPlainText(f"{type(exc).__name__}: {exc}")

    @staticmethod
    def _load_script_function(entry: FunctionEntry):
        """试算用：编译执行一次脚本并取回目标函数。

        与引擎加载同源（compile + exec + 捕获注册），区别是不经过引擎实例，
        所以需要 ``_engine`` / ``_wf`` 的函数在界面上已被禁用。
        """
        if entry.script_path is None:
            return None
        source = entry.script_path.read_text(encoding="utf-8")
        module_name = f"lvjiang_preview_{entry.rel_path.replace('/', '_').removesuffix('.py')}"
        module = types.ModuleType(module_name)
        module.__file__ = str(entry.script_path)
        with capture_registrations() as registered:
            exec(compile(source, str(entry.script_path), "exec"), module.__dict__)
        return registered.get(entry.name)

    def _on_locate(self) -> None:
        entry = self._current
        if entry is not None and entry.is_script:
            self.locate_requested.emit(entry.rel_path, entry.line)

    # ─── 统计与提示 ───────────────────────────────────────

    def _update_stats(self) -> None:
        builtin_count = sum(1 for entry in self._entries if not entry.is_script)
        script_count = sum(1 for entry in self._entries if entry.is_script)
        problems = script_problems(self._script_root())
        text = f"{tr('内置')} {builtin_count} · {tr('本地脚本')} {script_count}"
        if problems:
            text += f" · {tr('无法解析')} {len(problems)}"
        self.stats.setText(text)
        self.problems_label.setText("\n".join(problems))
        self.problems_label.setVisible(bool(problems))

    # ─── 界面搭建 ─────────────────────────────────────────

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)

        top = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText(tr("搜索函数名或说明"))
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._apply_filter)
        self.stats = QLabel()
        top.addWidget(self.search, 1)
        top.addWidget(self.stats)
        layout.addLayout(top)

        self.problems_label = QLabel()
        self.problems_label.setWordWrap(True)
        self.problems_label.setStyleSheet(f"color: {_WARN_COLOR};")
        self.problems_label.setVisible(False)
        layout.addWidget(self.problems_label)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.currentItemChanged.connect(self._on_current_changed)
        splitter.addWidget(self.tree)
        splitter.addWidget(self._build_detail())
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([240, 640])
        layout.addWidget(splitter, 1)

    def _build_detail(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(6, 0, 0, 0)

        self.title = QLabel()
        font = self.title.font()
        font.setBold(True)
        font.setPointSize(font.pointSize() + 2)
        self.title.setFont(font)

        self.signature_label = QLabel()
        self.signature_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        self.signature_label.setWordWrap(True)

        self.summary_label = QLabel()
        self.summary_label.setWordWrap(True)
        self.group_label = QLabel()
        self.returns_label = QLabel()
        self.returns_label.setWordWrap(True)
        self.source_label = QLabel()
        self.source_label.setWordWrap(True)
        self.source_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        self.issues_label = QLabel()
        self.issues_label.setWordWrap(True)
        self.issues_label.setStyleSheet(f"color: {_WARN_COLOR};")

        layout.addWidget(self.title)
        layout.addWidget(self.signature_label)
        layout.addWidget(self.summary_label)
        layout.addWidget(self.group_label)
        layout.addWidget(self.returns_label)
        layout.addWidget(self.source_label)
        layout.addWidget(self.issues_label)

        params_caption = QLabel(tr("参数"))
        font = params_caption.font()
        font.setBold(True)
        params_caption.setFont(font)
        layout.addWidget(params_caption)

        self.params_table = QTableWidget(0, 4)
        self.params_table.setHorizontalHeaderLabels(
            [tr("参数"), tr("类型"), tr("默认值"), tr("含义")])
        header = self.params_table.horizontalHeader()
        if header is not None:
            header.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.params_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers)
        self.params_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        vertical_header = self.params_table.verticalHeader()
        if vertical_header is not None:
            vertical_header.setVisible(False)
        self.params_table.setMinimumHeight(90)
        layout.addWidget(self.params_table, 1)

        self.locate_button = QPushButton(tr("定位到脚本"))
        apply_compact_button_style(self.locate_button)
        self.locate_button.setEnabled(False)
        self.locate_button.clicked.connect(self._on_locate)
        locate_row = QHBoxLayout()
        locate_row.addWidget(self.locate_button)
        locate_row.addStretch(1)
        layout.addLayout(locate_row)

        preview_caption = QLabel(tr("试算"))
        font = preview_caption.font()
        font.setBold(True)
        preview_caption.setFont(font)
        layout.addWidget(preview_caption)

        self.params_form = QFormLayout()
        self.params_form.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(self.params_form)

        self.preview_button = QPushButton(tr("执行"))
        apply_compact_button_style(self.preview_button)
        self.preview_button.clicked.connect(self._run_preview)
        preview_row = QHBoxLayout()
        preview_row.addWidget(self.preview_button)
        preview_row.addStretch(1)
        layout.addLayout(preview_row)

        self.preview_output = QPlainTextEdit()
        self.preview_output.setReadOnly(True)
        self.preview_output.setMaximumHeight(90)
        self.preview_output.setPlaceholderText(tr("执行结果显示在这里"))
        layout.addWidget(self.preview_output)
        return panel


def _parse_literal(text: str) -> Any:
    """按 JSON 解析字面量，失败则当普通字符串。

    DSL 里写 ``"abc"`` 与 ``abc`` 都是字符串，界面输入沿用同一宽容度：
    但 ``1`` / ``true`` / ``[1, 2]`` 这类要能变成真正的类型。
    """
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text
