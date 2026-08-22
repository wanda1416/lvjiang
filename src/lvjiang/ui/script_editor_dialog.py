"""脚本编辑对话框 - 新建 / 编辑 / 删除 .wf 工作流脚本

从「工具 → 脚本编辑」打开（F6）。左侧是 workflows 顶层 .wf 的合并视图
（system ∪ local，标注来源层），中间是带语法高亮的编辑区，右侧是调试面板
（「指令」Tab：快捷指令式选操作填槽位插入，见 ``action_palette``；「调试」Tab：截图画布
取点/取色/取区域 → 插入脚本；运行/单步/继续/暂停/停止 + 当前行高亮 + 变量表 + 日志，
见 ``script_workbench.DebugPanel``）。

写入走 ConfigResolver：开发模式写 system，用户模式写 local 影子
（与脚本配置 / 场景管理同一套模式判定），所以用户改出厂脚本不会污染
system 目录，删除出厂脚本落墓碑而不是真删。

校验分两档：
- 「检查」只做语法解析，不需要保存；
- 「保存」落盘后再跑一遍引擎的 validate_only（语法 + import 链 + 命名等待 +
  布局引用），判据与真正执行共用，预检放过的上机不会炸。

新建脚本自动加进 workflows.yaml 的 exposed（否则日常页下拉看不到它）。
脚本 id 即文件名，不允许 ``_`` 前缀（发现层把 ``_*.wf`` 当临时文件跳过）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from loguru import logger
from PyQt6.QtCore import QRegularExpression, Qt
from PyQt6.QtGui import (
    QColor,
    QFont,
    QSyntaxHighlighter,
    QTextCharFormat,
    QTextCursor,
    QTextDocument,
    QTextFormat,
)
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..core.config.resolver import get_resolver
from ..i18n import tr

#: 脚本 id = 文件名 stem；``_`` 前缀被发现层视为临时文件，不允许
_SCRIPT_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")


# ─── 纯逻辑（可离线测试）────────────────────────────────

@dataclass(frozen=True)
class ScriptEntry:
    id: str
    path: Path
    layer: str  # "system" | "local"


def validate_script_id(sid: str) -> str | None:
    """合法返回 None，否则返回错误说明"""
    sid = (sid or "").strip()
    if not sid:
        return tr("脚本 id 不能为空")
    if sid.startswith("_"):
        return tr("脚本 id 不能以 _ 开头（发现层会把它当临时文件跳过）")
    if not _SCRIPT_ID_RE.match(sid):
        return tr("脚本 id 只能用字母、数字、下划线，且以字母开头")
    return None


def script_rel_path(sid: str) -> str:
    return f"workflows/{sid}.wf"


def list_script_files() -> list[ScriptEntry]:
    """workflows 顶层 .wf 的合并视图（local 影子优先），按 id 排序"""
    resolver = get_resolver()
    local_root = Path(resolver.local_dir)
    out: list[ScriptEntry] = []
    for name in resolver.enumerate_entities("workflows", "*.wf"):
        p = resolver.resolve_read(f"workflows/{name}")
        if p is None:
            continue
        try:
            is_local = Path(p).is_relative_to(local_root)
        except ValueError:
            is_local = False
        out.append(ScriptEntry(id=Path(p).stem, path=Path(p), layer="local" if is_local else "system"))
    return sorted(out, key=lambda e: e.id)


def new_script_text(name: str, env: tuple[str, ...] = ("android", "desktop")) -> str:
    """新建脚本模板：front-matter + 带注释的骨架"""
    env_list = ", ".join(env)
    return (
        f"#% name: {name}\n"
        f"#% env: [{env_list}]\n"
        "\n"
        "# 在这里写工作流。常用指令：\n"
        "#   click [scene].[region] after wait @page_refresh\n"
        "#   scan [scene].[region] as $text by contains \"关键字\"\n"
        "#   find as $hit by image \"模板名\"\n"
        "#   if $hit\n"
        "#       click $hit\n"
        "#   end\n"
        "# 语法文档：docs/30-architecture/32-grammar/README.md\n"
        "\n"
        "log \"开始\"\n"
    )


def check_syntax(text: str) -> list[str]:
    """只做语法解析；返回问题清单（空 = 通过）

    lark 的词法/语法错误带行列；transformer 里抛的 WorkflowUserError 会被包成
    VisitError，解出原始异常的消息。
    """
    from lark.exceptions import UnexpectedInput, VisitError

    from ..workflows.engine.signals import WorkflowUserError
    from ..workflows.grammar import parse_text

    try:
        parse_text(text)
    except UnexpectedInput as e:
        line = getattr(e, "line", "?")
        col = getattr(e, "column", "?")
        snippet = ""
        try:
            snippet = e.get_context(text, span=40).strip()
        except Exception:  # noqa: BLE001 — get_context 对某些错误类型不可用
            pass
        msg = tr("第 {line} 行第 {col} 列：语法错误").format(line=line, col=col)
        return [f"{msg}\n{snippet}" if snippet else msg]
    except VisitError as e:
        orig = getattr(e, "orig_exc", None)
        return [str(orig) if isinstance(orig, WorkflowUserError) else str(e)]
    except WorkflowUserError as e:
        return [str(e)]
    except Exception as e:  # noqa: BLE001 — 把解析器内部错误也当问题展示，不让对话框崩
        return [f"{type(e).__name__}: {e}"]
    return []


def validate_with_layout(path: Path, layout, delay_params: dict | None) -> list[str]:
    """落盘后的完整静态校验（语法 + import 链 + 命名等待 + 布局引用）

    与真执行共用 WorkflowEngine._load_and_validate；硬件后端传 None，
    validate_only 不碰它们。
    """
    from ..workflows.engine import WorkflowEngine
    from ..workflows.engine.signals import WorkflowUserError

    engine = WorkflowEngine(
        capture=None, ocr=None, input_ctrl=None,  # type: ignore[arg-type]
        layout=layout, delay_params=delay_params or {},
    )
    try:
        engine.validate_only(path)
    except WorkflowUserError as e:
        return [str(e)]
    except Exception as e:  # noqa: BLE001
        return [f"{type(e).__name__}: {e}"]
    return []


def expose_script(sid: str) -> bool:
    """把脚本加进 workflows.yaml 的 exposed；已在或 exposed 为空（=全部展示）时不写，返回是否写了"""
    resolver = get_resolver()
    try:
        data = resolver.load_merged("workflows.yaml") or {}
    except Exception as e:  # noqa: BLE001
        logger.warning(f"读取 workflows.yaml 失败，跳过自动暴露: {e}")
        return False
    exposed = list(data.get("exposed") or [])
    if not exposed or sid in exposed:
        return False
    exposed.append(sid)
    resolver.save_merged("workflows.yaml", {"exposed": exposed, "overrides": data.get("overrides") or {}})
    return True


# ─── 语法高亮 ───────────────────────────────────────────

def _fmt(color: str, bold: bool = False, italic: bool = False) -> QTextCharFormat:
    f = QTextCharFormat()
    f.setForeground(QColor(color))
    if bold:
        f.setFontWeight(QFont.Weight.Bold)
    if italic:
        f.setFontItalic(True)
    return f


class WfHighlighter(QSyntaxHighlighter):
    """.wf 语法高亮；关键字集合与 VS Code 扩展的 tmLanguage 同源，另补 press/move/scroll/image"""

    _CONTROL = r"\b(if|else|end|for|in|loop|while|until|break|continue|return|goto|try|catch)\b"
    _COMMANDS = (
        r"\b(click|move|scroll|drag|press|wait|scan|recognize|collect|log|screenshot|"
        r"eval|default|align|find|import|def|call)\b"
    )
    _MODIFIERS = r"\b(as|by|where|on|group|hold|rich|full|with|stable|threshold|interval|duration|least)\b"
    _TIMING = r"\b(before|after|around)\b"
    _MATCH = r"\b(equals_any|contains_any|equals|contains|image)\b"
    _CONST = r"\b(true|false|null|not|and|or|is_empty|up|down|left|right|session|context)\b"

    def __init__(self, doc: QTextDocument):
        super().__init__(doc)
        rules = [
            (self._CONTROL, _fmt("#af00db", bold=True)),
            (self._COMMANDS, _fmt("#0000ff", bold=True)),
            (self._MODIFIERS, _fmt("#0070c1")),
            (self._TIMING, _fmt("#0070c1")),
            (self._MATCH, _fmt("#267f99")),
            (self._CONST, _fmt("#0000ff")),
            (r"\b[0-9]+(\.[0-9]+)?\b", _fmt("#098658")),
            (r"\$[A-Za-z_][A-Za-z0-9_]*", _fmt("#001080")),
            (r"@[A-Za-z_][A-Za-z0-9_]*", _fmt("#795e26")),
            (r"\[[^\]\n]+\]", _fmt("#267f99")),
            (r"\"[^\"\n]*\"", _fmt("#a31515")),
            (r"#%.*$", _fmt("#008000", italic=True)),
            (r"#(?!%).*$", _fmt("#6a9955", italic=True)),
        ]
        self._rules = [(QRegularExpression(p, QRegularExpression.PatternOption.CaseInsensitiveOption), f)
                       for p, f in rules]

    def highlightBlock(self, text: str | None) -> None:  # noqa: N802 — Qt 虚函数
        if not text:
            return
        for regex, fmt in self._rules:
            it = regex.globalMatch(text)
            while it.hasNext():
                m = it.next()
                self.setFormat(m.capturedStart(), m.capturedLength(), fmt)


# ─── 对话框 ─────────────────────────────────────────────

class ScriptEditorDialog(QDialog):
    """脚本列表 + 编辑区 + 新建/保存/另存为/删除/检查"""

    def __init__(self, main_window=None):
        super().__init__(main_window)
        self._main = main_window
        self._entries: list[ScriptEntry] = []
        self._current: ScriptEntry | None = None
        self._dirty = False
        self._changed_any = False   # 本次会话有无落盘变更（关闭后主窗口据此刷新）
        self.setWindowTitle(tr("脚本编辑"))
        self.setMinimumSize(960, 640)
        self._setup_ui()
        self._reload_list()

    # ─── UI ────────────────────────────────────────────

    def _setup_ui(self):
        root = QVBoxLayout(self)

        btn_row = QHBoxLayout()
        self.btn_new = QPushButton(tr("新建"))
        self.btn_new.clicked.connect(self._on_new)
        self.btn_save = QPushButton(tr("保存"))
        self.btn_save.clicked.connect(self._on_save)
        self.btn_save_as = QPushButton(tr("另存为"))
        self.btn_save_as.clicked.connect(self._on_save_as)
        self.btn_delete = QPushButton(tr("删除"))
        self.btn_delete.clicked.connect(self._on_delete)
        self.btn_check = QPushButton(tr("检查"))
        self.btn_check.clicked.connect(self._on_check)
        for b in (self.btn_new, self.btn_save, self.btn_save_as, self.btn_delete, self.btn_check):
            btn_row.addWidget(b)
        btn_row.addStretch()
        self.lbl_layer = QLabel("")
        self.lbl_layer.setStyleSheet("color: #888;")
        btn_row.addWidget(self.lbl_layer)
        root.addLayout(btn_row)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.list = QListWidget()
        self.list.setMinimumWidth(200)
        self.list.currentItemChanged.connect(self._on_select)
        splitter.addWidget(self.list)

        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        self.editor = QPlainTextEdit()
        font = QFont("Menlo" if self._is_mac() else "Consolas")
        font.setStyleHint(QFont.StyleHint.Monospace)
        font.setPointSize(12)
        self.editor.setFont(font)
        self.editor.setTabStopDistance(4 * self.editor.fontMetrics().horizontalAdvance(" "))
        self.editor.setPlaceholderText(tr("左侧选择脚本，或点「新建」"))
        self.editor.textChanged.connect(self._on_text_changed)
        self._highlighter = WfHighlighter(self.editor.document())
        rl.addWidget(self.editor)
        self.lbl_status = QLabel("")
        self.lbl_status.setWordWrap(True)
        self.lbl_status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.lbl_status.setStyleSheet("color: #555; padding: 4px;")
        rl.addWidget(self.lbl_status)
        splitter.addWidget(right)

        from .action_palette import ActionPalette, default_providers
        from .script_workbench import DebugPanel
        self.debug = DebugPanel(self._main, self)
        self.palette = ActionPalette(default_providers(self._main, self.debug))
        self.palette.insert_requested.connect(self.insert_statement)
        tabs = QTabWidget()
        tabs.addTab(self.palette, tr("指令"))
        tabs.addTab(self.debug, tr("调试"))
        self.side_tabs = tabs
        splitter.addWidget(tabs)
        splitter.setStretchFactor(1, 3)
        splitter.setStretchFactor(2, 2)
        root.addWidget(splitter, 1)   # 纵向多余空间全给编辑区，别平分给工具条

        self._refresh_buttons()

    @staticmethod
    def _is_mac() -> bool:
        import sys
        return sys.platform == "darwin"

    # ─── 列表 ──────────────────────────────────────────

    def _reload_list(self, select_id: str | None = None):
        self._entries = list_script_files()
        self.list.blockSignals(True)
        self.list.clear()
        for e in self._entries:
            item = QListWidgetItem(f"{e.id}  [{e.layer}]")
            item.setData(Qt.ItemDataRole.UserRole, e.id)
            self.list.addItem(item)
        self.list.blockSignals(False)
        if select_id:
            for i in range(self.list.count()):
                if self.list.item(i).data(Qt.ItemDataRole.UserRole) == select_id:
                    self.list.setCurrentRow(i)
                    return
        if self._current is None and self.list.count():
            self.list.setCurrentRow(0)

    def _entry(self, sid: str) -> ScriptEntry | None:
        return next((e for e in self._entries if e.id == sid), None)

    def _on_select(self, item: QListWidgetItem | None, _prev=None):
        if item is None:
            return
        sid = item.data(Qt.ItemDataRole.UserRole)
        if self._current is not None and sid == self._current.id:
            return
        if not self._confirm_discard():
            # 回退选中
            self.list.blockSignals(True)
            self._reload_list(select_id=self._current.id if self._current else None)
            self.list.blockSignals(False)
            return
        self._load_entry(self._entry(sid))

    def _load_entry(self, entry: ScriptEntry | None):
        self._current = entry
        self.editor.blockSignals(True)
        if entry is None:
            self.editor.setPlainText("")
        else:
            try:
                self.editor.setPlainText(entry.path.read_text(encoding="utf-8"))
            except OSError as e:
                self.editor.setPlainText("")
                self._set_status(tr("读取失败: {e}").format(e=e), error=True)
        self.editor.blockSignals(False)
        self._dirty = False
        self.lbl_layer.setText("" if entry is None else f"{entry.layer}: {entry.path}")
        self._set_status("")
        self._refresh_buttons()

    # ─── 状态 ──────────────────────────────────────────

    def _on_text_changed(self):
        self._dirty = True
        self._refresh_buttons()

    def _refresh_buttons(self):
        has_current = self._current is not None
        has_text = bool(self.editor.toPlainText().strip())
        self.btn_save.setEnabled(self._dirty and (has_current or has_text))
        self.btn_save_as.setEnabled(has_text)
        self.btn_delete.setEnabled(has_current)
        self.btn_check.setEnabled(has_text)
        title = tr("脚本编辑")
        if self._current is not None:
            title += f" - {self._current.id}" + (" *" if self._dirty else "")
        self.setWindowTitle(title)

    def _set_status(self, text: str, error: bool = False):
        self.lbl_status.setText(text)
        self.lbl_status.setStyleSheet(
            "color: #c62828; padding: 4px;" if error else "color: #2e7d32; padding: 4px;")

    def _confirm_discard(self) -> bool:
        if not self._dirty:
            return True
        ret = QMessageBox.question(
            self, tr("放弃修改？"),
            tr("当前脚本有未保存的修改，放弃？"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return ret == QMessageBox.StandardButton.Yes

    # ─── 动作 ──────────────────────────────────────────

    def _ask_script_id(self, title: str, default: str = "") -> str | None:
        while True:
            sid, ok = QInputDialog.getText(self, title, tr("脚本 id（文件名，字母/数字/下划线）:"), text=default)
            if not ok:
                return None
            sid = sid.strip()
            err = validate_script_id(sid)
            if err:
                QMessageBox.warning(self, tr("id 不合法"), err)
                default = sid
                continue
            if self._entry(sid) is not None:
                ret = QMessageBox.question(
                    self, tr("已存在"),
                    tr("脚本 {sid} 已存在，覆盖？").format(sid=sid),
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if ret != QMessageBox.StandardButton.Yes:
                    default = sid
                    continue
            return sid

    def _on_new(self):
        if not self._confirm_discard():
            return
        sid = self._ask_script_id(tr("新建脚本"))
        if sid is None:
            return
        name, ok = QInputDialog.getText(self, tr("新建脚本"), tr("显示名:"), text=sid)
        if not ok:
            return
        text = new_script_text(name.strip() or sid)
        path = self._write(sid, text)
        if path is None:
            return
        exposed = expose_script(sid)
        self._changed_any = True
        self._reload_list(select_id=sid)
        self._load_entry(self._entry(sid))
        self._set_status(
            tr("已创建 {path}").format(path=path)
            + (tr("，并已加入日常页展示列表") if exposed else ""))

    def _write(self, sid: str, text: str) -> Path | None:
        try:
            path = get_resolver().write_entity(script_rel_path(sid), text)
        except OSError as e:
            QMessageBox.warning(self, tr("保存失败"), str(e))
            return None
        logger.info(f"脚本已写入: {path}")
        return path

    def _on_save(self):
        if self._current is None:
            self._on_save_as()
            return
        self._save_to(self._current.id)

    def _on_save_as(self):
        sid = self._ask_script_id(tr("另存为"), default=self._current.id if self._current else "")
        if sid is None:
            return
        self._save_to(sid)
        expose_script(sid)

    def _save_to(self, sid: str):
        text = self.editor.toPlainText()
        if not text.endswith("\n"):
            text += "\n"
        path = self._write(sid, text)
        if path is None:
            return
        self._changed_any = True
        self._dirty = False
        self._reload_list(select_id=sid)
        entry = self._entry(sid)
        self._current = entry
        self.lbl_layer.setText("" if entry is None else f"{entry.layer}: {entry.path}")
        self._refresh_buttons()
        problems = check_syntax(text) or self._validate_on_disk(path)
        if problems:
            self._set_status(tr("已保存 {path}，但校验未通过：\n").format(path=path) + "\n".join(problems), error=True)
        else:
            self._set_status(tr("已保存 {path}，语法与布局引用校验通过").format(path=path))

    def _validate_on_disk(self, path: Path) -> list[str]:
        """有主窗口时用当前布局做完整校验；没有（独立打开）就只报语法"""
        main = self._main
        lm = getattr(main, "_layout_manager", None)
        if lm is None:
            return []
        try:
            layout = lm.load_layout(lm.get_active_layout_name())
        except Exception as e:  # noqa: BLE001
            return [tr("无法加载当前布局做引用校验: {e}").format(e=e)]
        if not layout:
            return [tr("无法加载当前布局做引用校验")]
        user_cfg = getattr(main, "_user_config", None)
        delay_params = getattr(user_cfg, "delay_params", None)
        return validate_with_layout(path, layout, delay_params)

    def _on_delete(self):
        if self._current is None:
            return
        sid = self._current.id
        ret = QMessageBox.question(
            self, tr("删除脚本"),
            tr("删除脚本 {sid}？（用户模式下出厂脚本只是被隐藏，可通过删除 local 墓碑恢复）").format(sid=sid),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if ret != QMessageBox.StandardButton.Yes:
            return
        try:
            get_resolver().delete_entity(script_rel_path(sid))
        except OSError as e:
            QMessageBox.warning(self, tr("删除失败"), str(e))
            return
        logger.info(f"脚本已删除: {sid}")
        self._changed_any = True
        self._current = None
        self._dirty = False
        self._reload_list()
        if self._current is None:
            self._load_entry(None)
        self._set_status(tr("已删除 {sid}").format(sid=sid))

    def _on_check(self):
        problems = check_syntax(self.editor.toPlainText())
        if problems:
            self._set_status("\n".join(problems), error=True)
        else:
            self._set_status(tr("语法检查通过（保存后会再做布局引用校验）"))

    # ─── 调试面板用的编辑器接口 ─────────────────────────

    def insert_text(self, text: str) -> None:
        """光标处插入（画布取点/取色/取区域的落点）"""
        cursor = self.editor.textCursor()
        cursor.insertText(text)
        self.editor.setTextCursor(cursor)
        self.editor.setFocus()

    def insert_statement(self, text: str) -> None:
        """按语句插入：光标所在行前面已有内容时先换行，保持缩进与当前行一致"""
        cursor = self.editor.textCursor()
        block = cursor.block()
        block_text = block.text()
        col = cursor.positionInBlock()
        indent = block_text[: len(block_text) - len(block_text.lstrip())]
        body = "\n".join((indent + ln) if i and ln else ln for i, ln in enumerate(text.split("\n")))
        if col > 0 and block_text[:col].strip():
            body = "\n" + indent + body      # 行内已有内容：另起一行
        # 行尾且后面还有行：借用已有的换行；否则自己补一个
        at_line_end = col == len(block_text)
        if not (at_line_end and block.next().isValid() and col > 0):
            body += "\n"
        cursor.insertText(body)
        self.editor.setTextCursor(cursor)
        self.editor.setFocus()

    def script_text(self) -> str:
        return self.editor.toPlainText()

    def highlight_line(self, line_no: int | None) -> None:
        """高亮当前执行行（None 清除）并滚到可见"""
        if line_no is None or line_no <= 0:
            self.editor.setExtraSelections([])
            return
        block = self.editor.document().findBlockByNumber(line_no - 1)
        if not block.isValid():
            self.editor.setExtraSelections([])
            return
        sel = QTextEdit.ExtraSelection()
        sel.format.setBackground(QColor("#fff3b0"))
        sel.format.setProperty(QTextFormat.Property.FullWidthSelection, True)
        cur = QTextCursor(block)
        sel.cursor = cur
        self.editor.setExtraSelections([sel])
        self.editor.setTextCursor(cur)
        self.editor.ensureCursorVisible()

    def set_locked(self, locked: bool) -> None:
        """运行期间编辑器只读——行号变了高亮就对不上"""
        self.editor.setReadOnly(locked)
        self.list.setEnabled(not locked)
        if locked:
            for b in (self.btn_new, self.btn_save, self.btn_save_as, self.btn_delete):
                b.setEnabled(False)
        else:
            self._refresh_buttons()

    # ─── 关闭 ──────────────────────────────────────────

    @property
    def changed(self) -> bool:
        return self._changed_any

    def closeEvent(self, event):  # noqa: N802 — Qt 虚函数
        if not self._confirm_discard():
            event.ignore()
            return
        self.debug.shutdown()
        super().closeEvent(event)

    def reject(self):
        if not self._confirm_discard():
            return
        self.debug.shutdown()
        super().reject()
