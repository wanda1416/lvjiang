"""脚本编辑对话框 - 新建 / 编辑 / 删除 .wf 工作流脚本

从「工具 → 脚本编辑」打开（F6）。左侧是 workflows 整棵目录树的合并视图
（见 ``workflows.file_tree``：system ∪ local，local 影子优先，不做任何过滤），
中间是带语法高亮的编辑区，右侧是调试面板
（「指令」Tab：快捷指令式选操作填槽位插入，见 ``action_palette``；「调试」Tab：截图画布
取点/取色/取区域 → 插入脚本；运行/单步/继续/暂停/停止 + 当前行高亮 + 变量表 + 日志，
见 ``workbench.DebugPanel``）。

写入走 ConfigResolver：开发模式写 system，用户模式写 local 影子
（与脚本配置 / 场景管理同一套模式判定），所以用户改系统脚本不会污染
system 目录。

用户模式下**系统脚本只读**：编辑区置灰，要改必须先右键「复制到本地」。
这一步不是多余的仪式——复制之后该文件就成了 local 影子，从此收不到任何
系统更新（实体文件是整文件影子，不做内容合并），代价得让用户明确知道。
开发模式直接写 system，不受此限。

校验分两档：
- 「检查」只做语法解析，不需要保存；
- 「保存」落盘后再跑一遍引擎的 validate_only（语法 + import 链 + 命名等待 +
  布局引用），判据与真正执行共用，预检放过的上机不会炸。

复制之后想反悔，右键「还原为系统」丢掉本地那份——没有这条回头路，
「复制到本地」就是单行道：改坏了既删不掉（系统内容受保护）也回不去。

新建脚本会被发现层自动扫到并默认展示在日常页，无需额外登记。
**新建**的脚本 id 不允许 ``_`` 前缀（发现层把 ``_*.wf`` 当临时文件跳过，
建出来也跑不了）；但这只约束新建，树上照旧展示磁盘上的每一个 ``.wf``，
包括录制产物与 ``_editor_run.wf`` 这类临时文件——它们同样是用户要打开的东西。
新建 / 另存为落在**当前选中文件所在目录**，不会莫名跑回顶层。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from loguru import logger
from PyQt6.QtCore import QRegularExpression, QSize, Qt
from PyQt6.QtGui import (
    QColor,
    QFont,
    QIcon,
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
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QStyle,
    QTabWidget,
    QTextEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ...core.config.resolver import get_resolver
from ...i18n import tr
from ...workflows.file_tree import (
    WORKFLOWS_DIR,
    WorkflowFile,
    list_directories,
    list_workflow_files,
)
from ..button_styles import apply_button_style
from ..theme import get_theme_manager

#: 脚本 id = 文件名 stem；``_`` 前缀被发现层视为临时文件，不允许
_SCRIPT_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")


# ─── 纯逻辑（可离线测试）────────────────────────────────

@dataclass(frozen=True)
class ScriptEntry:
    """树上一个节点：合并视图里的元信息 + 实际解析到的磁盘路径"""

    file: WorkflowFile
    path: Path

    @property
    def rel_path(self) -> str:
        """相对 workflows 根的 posix 路径，如 ``subcall/nav.wf``——节点唯一键"""
        return self.file.rel_path

    @property
    def id(self) -> str:
        """文件名去掉 .wf。**同名不同目录会重复**，别拿它当键"""
        return self.file.name[:-3] if self.file.name.endswith(".wf") else self.file.name

    @property
    def name(self) -> str:
        return self.file.name

    @property
    def parent(self) -> str:
        return self.file.parent

    @property
    def layer(self) -> str:
        return self.file.layer


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
    """顶层脚本 id → resolver 相对路径（新建时用）"""
    return f"{WORKFLOWS_DIR}/{sid}.wf"


def wf_rel_path(rel_path: str) -> str:
    """workflows 内相对路径（含 .wf）→ resolver 相对路径"""
    return f"{WORKFLOWS_DIR}/{rel_path}"


def join_rel(parent: str, sid: str) -> str:
    """目录 + id → workflows 内相对路径；顶层 parent 传空串"""
    return f"{parent}/{sid}.wf" if parent else f"{sid}.wf"


def list_script_files() -> list[ScriptEntry]:
    """workflows 整棵树的合并视图（local 影子优先），按路径排序

    过滤规则在 ``file_tree`` 里统一定义（结论是：不过滤）。这里只补上
    resolver 解析出的真实磁盘路径，供编辑区读写。
    """
    resolver = get_resolver()
    out: list[ScriptEntry] = []
    for f in list_workflow_files():
        p = resolver.resolve_read(wf_rel_path(f.rel_path))
        if p is None:
            continue
        out.append(ScriptEntry(file=f, path=Path(p)))
    return out


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

    from ...workflows.engine.signals import WorkflowUserError
    from ...workflows.grammar import parse_text

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
    from ...workflows.engine import WorkflowEngine
    from ...workflows.engine.signals import WorkflowUserError

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
        self._file_items: dict[str, QTreeWidgetItem] = {}
        self._dir_items: dict[str, QTreeWidgetItem] = {}
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
        apply_button_style(
            self.btn_new,
            self.btn_save,
            self.btn_save_as,
            self.btn_check,
        )
        apply_button_style(self.btn_delete, variant="danger")
        for b in (self.btn_new, self.btn_save, self.btn_save_as, self.btn_delete, self.btn_check):
            btn_row.addWidget(b)
        btn_row.addStretch()
        self.lbl_layer = QLabel("")
        self.lbl_layer.setStyleSheet("color: palette(mid);")
        btn_row.addWidget(self.lbl_layer)
        root.addLayout(btn_row)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setMinimumWidth(220)
        # 节点只写文件名。「来自哪一层」「能否独立启动」是另一层面的事，
        # 走选中态的 lbl_layer 与 tooltip，不往树上堆标记。
        self.tree.setUniformRowHeights(True)
        self.tree.setIndentation(16)
        self.tree.setIconSize(QSize(16, 16))
        self.tree.setAnimated(True)
        # 只调间距，不写颜色：颜色归全局主题样式表管（见 ui.theme），
        # 在这里硬编码 token 会在切换深浅色时留一块不跟着变的死角。
        self.tree.setStyleSheet(
            "QTreeWidget::item { padding: 5px 4px; }")
        self._icon_dir, self._icon_file = self._tree_icons()
        self.tree.currentItemChanged.connect(self._on_select)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._on_tree_menu)
        splitter.addWidget(self.tree)

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
        self.editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.editor.textChanged.connect(self._on_text_changed)
        self._highlighter = WfHighlighter(self.editor.document())
        rl.addWidget(self.editor)
        self.lbl_status = QLabel("")
        self.lbl_status.setWordWrap(True)
        self.lbl_status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.lbl_status.setStyleSheet("color: palette(mid); padding: 4px;")
        rl.addWidget(self.lbl_status)
        splitter.addWidget(right)

        from .action_palette import ActionPalette, default_providers
        from .workbench import DebugPanel
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

    def _tree_icons(self) -> tuple[QIcon, QIcon]:
        """目录 / 文件图标取系统标准图标，跟着平台与主题走"""
        style = self.style()
        if style is None:                      # 无 QStyle 的极端环境（离屏测试）
            return QIcon(), QIcon()
        return (style.standardIcon(QStyle.StandardPixmap.SP_DirIcon),
                style.standardIcon(QStyle.StandardPixmap.SP_FileIcon))

    @staticmethod
    def _is_mac() -> bool:
        import sys
        return sys.platform == "darwin"

    # ─── 列表 ──────────────────────────────────────────

    def _reload_list(self, select_id: str | None = None):
        """重建目录树。``select_id`` 是 workflows 内相对路径（含 .wf）"""
        self._entries = list_script_files()
        expanded = self._expanded_dirs()        # 重建会丢展开状态，先记下来
        was_blocked = self.tree.blockSignals(True)
        self.tree.clear()
        self._file_items = {}
        self._dir_items = {}
        dir_items = self._dir_items
        for d in list_directories([e.file for e in self._entries]):
            parent_dir, _, leaf = d.rpartition("/")
            node = QTreeWidgetItem([leaf])
            node.setIcon(0, self._icon_dir)
            node.setData(0, Qt.ItemDataRole.UserRole, None)   # 目录不可选中打开
            node.setFlags(node.flags() & ~Qt.ItemFlag.ItemIsSelectable)
            dir_items[d] = node
            (dir_items[parent_dir].addChild(node) if parent_dir
             else self.tree.addTopLevelItem(node))
        for e in self._entries:
            item = QTreeWidgetItem([e.name])
            item.setIcon(0, self._icon_file)
            item.setData(0, Qt.ItemDataRole.UserRole, e.rel_path)
            item.setToolTip(0, f"{e.rel_path}\n{e.layer}: {e.path}")
            (dir_items[e.parent].addChild(item) if e.parent
             else self.tree.addTopLevelItem(item))
            self._file_items[e.rel_path] = item
        for d, node in dir_items.items():
            # 必须等节点进了树才生效：脱离 model 的 QTreeWidgetItem 上
            # setExpanded 是空操作
            node.setExpanded(d in expanded)
        self.tree.blockSignals(was_blocked)
        target = select_id or (
            self._current.rel_path if self._current is not None else None)
        sel = self._file_items.get(target) if target else None
        if sel is None and self._current is None:
            sel = self._first_file_item()
        if sel is not None:
            self._reveal(sel)
            self.tree.setCurrentItem(sel)

    def _expanded_dirs(self) -> set[str]:
        """当前展开着的目录（相对 workflows 根）"""
        return {d for d, node in getattr(self, "_dir_items", {}).items()
                if node.isExpanded()}

    @staticmethod
    def _reveal(item: QTreeWidgetItem) -> None:
        """展开到某个节点可见——选中子目录里的文件时必须做，否则光标落在
        收起的目录里，用户看不到自己在编辑哪一个"""
        node = item.parent()
        while node is not None:
            node.setExpanded(True)
            node = node.parent()

    def _first_file_item(self) -> QTreeWidgetItem | None:
        """根目录下的第一个文件。

        不往子目录里钻：子目录默认是收起的，钻进去等于开局就替用户展开一层
        目录，而且 subcall/ archived/ 这些本来就不是他要跑的东西。根目录一个
        文件都没有时才退而求其次，取树序第一个（照样得展开才看得见）。
        """
        roots = [self.tree.topLevelItem(i) for i in range(self.tree.topLevelItemCount())]
        for node in roots:
            if node.data(0, Qt.ItemDataRole.UserRole):
                return node
        stack = list(reversed(roots))
        while stack:
            node = stack.pop()
            if node.data(0, Qt.ItemDataRole.UserRole):
                return node
            stack.extend(node.child(i) for i in reversed(range(node.childCount())))
        return None

    def _entry(self, rel_path: str) -> ScriptEntry | None:
        return next((e for e in self._entries if e.rel_path == rel_path), None)

    def _on_select(self, item: QTreeWidgetItem | None, _prev=None):
        if item is None:
            return
        rel = item.data(0, Qt.ItemDataRole.UserRole)
        if rel is None:                                     # 目录节点
            return
        if self._current is not None and rel == self._current.rel_path:
            return
        if not self._confirm_discard():
            # 回退选中
            self.tree.blockSignals(True)
            self._reload_list(select_id=self._current.rel_path if self._current else None)
            self.tree.blockSignals(False)
            return
        self._load_entry(self._entry(rel))

    # ─── 右键菜单 ──────────────────────────────────────

    def _on_tree_menu(self, pos):
        item = self.tree.itemAt(pos)
        rel = item.data(0, Qt.ItemDataRole.UserRole) if item is not None else None
        entry = self._entry(rel) if rel else None
        if entry is None:
            return
        menu = QMenu(self)
        if not self._is_editable(entry):
            menu.addAction(tr("复制到本地以修改")).triggered.connect(
                lambda: self._copy_to_local(entry))
        elif entry.file.overrides_system and not get_resolver().is_dev_mode():
            # 覆盖了系统的影子：能还原，但不能删（删了这个实体就没了，
            # 而系统内容不允许用户删除）
            menu.addAction(tr("还原为系统")).triggered.connect(
                lambda: self._revert_to_system(entry))
        else:
            menu.addAction(tr("删除")).triggered.connect(self._on_delete)
        menu.exec(self.tree.viewport().mapToGlobal(pos))

    @staticmethod
    def _is_editable(entry: ScriptEntry) -> bool:
        """开发模式直接写 system，用户模式只有 local 那份能改"""
        return entry.file.editable or get_resolver().is_dev_mode()

    def _copy_to_local(self, entry: ScriptEntry):
        """把系统脚本原样复制成 local 影子，之后才允许编辑

        复制后该文件脱离系统更新（整文件影子），所以要用户明确确认一次。
        """
        ret = QMessageBox.question(
            self, tr("复制到本地"),
            tr("把系统脚本 {name} 复制到本地后才能修改。\n"
               "复制之后这个文件不再跟随系统更新，确定？").format(name=entry.rel_path),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if ret != QMessageBox.StandardButton.Yes:
            return
        try:
            text = entry.path.read_text(encoding="utf-8")
        except OSError as e:
            QMessageBox.warning(self, tr("复制失败"), str(e))
            return
        # force：内容与系统逐字相同，不强制的话 write_entity 会判成空操作
        if self._write(entry.rel_path, text, force=True) is None:
            return
        self._changed_any = True
        self._current = None          # 强制按新的 layer 重新加载
        self._reload_list(select_id=entry.rel_path)
        self._load_entry(self._entry(entry.rel_path))
        self._set_status(tr("已复制到本地，现在可以编辑"))

    def _revert_to_system(self, entry: ScriptEntry):
        """丢掉 local 影子，回到系统那一份——「复制到本地」的反向操作"""
        ret = QMessageBox.question(
            self, tr("还原为系统"),
            tr("丢弃 {name} 的本地修改，恢复系统版本？此操作不可恢复。")
            .format(name=entry.rel_path),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if ret != QMessageBox.StandardButton.Yes:
            return
        rel = entry.rel_path
        try:
            get_resolver().revert_entity_to_system(wf_rel_path(rel))
        except OSError as e:
            QMessageBox.warning(self, tr("还原失败"), str(e))
            return
        logger.info(f"脚本已还原为系统: {rel}")
        self._changed_any = True
        self._dirty = False
        self._current = None
        self._reload_list(select_id=rel)
        self._load_entry(self._entry(rel))
        self._set_status(tr("已还原为系统版本"))

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
        self._apply_read_only(entry)
        self._set_status(
            "" if entry is None or self._is_editable(entry)
            else tr("系统脚本只读——右键「复制到本地以修改」后才能编辑"))
        self._refresh_buttons()

    def _apply_read_only(self, entry: ScriptEntry | None):
        """系统脚本置灰编辑区，并把来源写进状态标签"""
        editable = entry is not None and self._is_editable(entry)
        self.editor.setReadOnly(entry is not None and not editable)
        if entry is None:
            self.lbl_layer.setText("")
            return
        if entry.file.is_system:
            origin = tr("系统") if editable else tr("系统（只读）")
        else:
            origin = tr("本地覆盖系统") if entry.file.overrides_system else tr("本地")
        self.lbl_layer.setText(f"{origin} · {entry.path}")

    # ─── 状态 ──────────────────────────────────────────

    def _on_text_changed(self):
        self._dirty = True
        self._refresh_buttons()

    def _refresh_buttons(self):
        cur = self._current
        has_text = bool(self.editor.toPlainText().strip())
        editable = cur is not None and self._is_editable(cur)
        # 系统脚本只读：保存按钮直接关掉，别让用户敲完一屏才发现存不下去
        self.btn_save.setEnabled(self._dirty and (editable or (cur is None and has_text)))
        self.btn_save_as.setEnabled(has_text)
        # 系统脚本属于 system 内容，用户模式下不可删除——不想在日常页看到
        # 请在「工具 → 脚本配置」取消勾选。
        shadow = (cur is not None and editable and cur.file.overrides_system
                  and not get_resolver().is_dev_mode())
        can_delete = editable and not shadow
        if cur is None or can_delete:
            hint = ""
        elif shadow:
            hint = tr("这是系统脚本的本地副本，删不掉；右键「还原为系统」可丢弃本地修改")
        else:
            hint = tr("系统脚本不可删除；不想展示请在「脚本配置」取消勾选")
        self.btn_delete.setEnabled(can_delete)
        self.btn_delete.setToolTip(hint)
        self.btn_check.setEnabled(has_text)
        title = tr("脚本编辑")
        if cur is not None:
            title += f" - {cur.rel_path}" + (" *" if self._dirty else "")
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

    def _target_dir(self) -> str:
        """新建 / 另存为落在当前选中文件所在目录，顶层为空串"""
        return self._current.parent if self._current is not None else ""

    def _ask_script_id(self, title: str, default: str = "") -> str | None:
        """问一个 id，返回 workflows 内相对路径（含 .wf）；取消返回 None"""
        parent = self._target_dir()
        prompt = tr("脚本 id（文件名，字母/数字/下划线）:")
        if parent:
            prompt += f"\n{parent}/"
        while True:
            sid, ok = QInputDialog.getText(self, title, prompt, text=default)
            if not ok:
                return None
            sid = sid.strip()
            err = validate_script_id(sid)
            if err:
                QMessageBox.warning(self, tr("id 不合法"), err)
                default = sid
                continue
            rel = join_rel(parent, sid)
            existing = self._entry(rel)
            if existing is not None:
                if not self._is_editable(existing):
                    QMessageBox.warning(
                        self, tr("不可覆盖"),
                        tr("{rel} 是系统脚本。要改它请在树里右键「复制到本地以修改」。")
                        .format(rel=rel))
                    default = sid
                    continue
                ret = QMessageBox.question(
                    self, tr("已存在"),
                    tr("脚本 {rel} 已存在，覆盖？").format(rel=rel),
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if ret != QMessageBox.StandardButton.Yes:
                    default = sid
                    continue
            return rel

    def _on_new(self):
        if not self._confirm_discard():
            return
        rel = self._ask_script_id(tr("新建脚本"))
        if rel is None:
            return
        sid = rel.rsplit("/", 1)[-1][:-3]
        name, ok = QInputDialog.getText(self, tr("新建脚本"), tr("显示名:"), text=sid)
        if not ok:
            return
        text = new_script_text(name.strip() or sid)
        path = self._write(rel, text)
        if path is None:
            return
        self._changed_any = True
        self._current = None
        self._reload_list(select_id=rel)
        self._load_entry(self._entry(rel))
        self._set_status(
            tr("已创建 {path}").format(path=path))

    def _write(self, rel: str, text: str, *, force: bool = False) -> Path | None:
        """rel 是 workflows 内相对路径（含 .wf）"""
        try:
            path = get_resolver().write_entity(wf_rel_path(rel), text, force=force)
        except OSError as e:
            QMessageBox.warning(self, tr("保存失败"), str(e))
            return None
        logger.info(f"脚本已写入: {path}")
        return path

    def _on_save(self):
        if self._current is None:
            self._on_save_as()
            return
        if not self._is_editable(self._current):
            self._set_status(
                tr("系统脚本只读——右键「复制到本地以修改」后才能保存"), error=True)
            return
        self._save_to(self._current.rel_path)

    def _on_save_as(self):
        rel = self._ask_script_id(
            tr("另存为"), default=self._current.id if self._current else "")
        if rel is None:
            return
        self._save_to(rel)

    def _save_to(self, rel: str):
        text = self.editor.toPlainText()
        if not text.endswith("\n"):
            text += "\n"
        path = self._write(rel, text)
        if path is None:
            return
        self._changed_any = True
        self._dirty = False
        self._current = None
        self._reload_list(select_id=rel)
        entry = self._entry(rel)
        self._current = entry
        self._apply_read_only(entry)
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
        rel = self._current.rel_path
        ret = QMessageBox.question(
            self, tr("删除脚本"),
            tr("删除脚本 {rel}？此操作不可恢复。").format(rel=rel),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if ret != QMessageBox.StandardButton.Yes:
            return
        try:
            get_resolver().delete_entity(wf_rel_path(rel))
        except OSError as e:
            QMessageBox.warning(self, tr("删除失败"), str(e))
            return
        logger.info(f"脚本已删除: {rel}")
        self._changed_any = True
        self._current = None
        self._dirty = False
        self._reload_list()
        if self._current is None:
            self._load_entry(None)
        self._set_status(tr("已删除 {rel}").format(rel=rel))

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
        sel.format.setBackground(
            QColor(get_theme_manager().tokens.warning_surface)
        )
        sel.format.setProperty(QTextFormat.Property.FullWidthSelection, True)
        cur = QTextCursor(block)
        sel.cursor = cur
        self.editor.setExtraSelections([sel])
        self.editor.setTextCursor(cur)
        self.editor.ensureCursorVisible()

    def set_locked(self, locked: bool) -> None:
        """运行期间编辑器只读——行号变了高亮就对不上"""
        self.tree.setEnabled(not locked)
        if locked:
            self.editor.setReadOnly(True)
            for b in (self.btn_new, self.btn_save, self.btn_save_as, self.btn_delete):
                b.setEnabled(False)
        else:
            # 别无脑放开只读——系统脚本解锁后仍应是只读的
            self._apply_read_only(self._current)
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
