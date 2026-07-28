"""工作流 DSL 解析对外接口与 Lark 实例管理

对外接口：
    parse_file(path) -> Program
    parse_text(text) -> Program
"""

from pathlib import Path

from lark import Lark

from ..ast_nodes import Program
from .transformer import _DSLTransformer

# ─── Lark 实例（延迟初始化） ──────────────────────────────

_parser: Lark | None = None


def _get_parser() -> Lark:
    global _parser
    if _parser is None:
        grammar_path = Path(__file__).parent.parent / "grammar.lark"
        _parser = Lark(
            grammar_path.read_text(encoding="utf-8"),
            parser="earley",
            propagate_positions=True,
        )
    return _parser


# ─── 预处理：换行续行 ────────────────────────────────────

def _preprocess_line_continuation(text: str) -> str:
    """处理两种换行续行：

    1. 显式续行：行尾反斜杠 \\ → 与下一行拼接
    2. 隐式续行：{} [] () 内部的换行视为空格（不终结语句）
    """
    # 第一步：显式续行 — 行尾 \\ 后紧跟换行，拼接两行
    text = text.replace("\\\n", " ")

    # 第二步：隐式续行 — 跟踪括号深度，深度 > 0 时换行替换为空格
    result = []
    depth = 0
    in_string = False
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == '"' and (i == 0 or text[i - 1] != '\\'):
            in_string = not in_string
            result.append(ch)
        elif not in_string:
            if ch in ('(', '[', '{'):
                depth += 1
                result.append(ch)
            elif ch in (')', ']', '}'):
                depth = max(0, depth - 1)
                result.append(ch)
            elif ch == '\n' and depth > 0:
                # 括号内换行 → 空格
                result.append(' ')
            else:
                result.append(ch)
        else:
            result.append(ch)
        i += 1
    return ''.join(result)


# ─── 公共接口 ─────────────────────────────────────────────

def parse_file(path: Path | str) -> Program:
    """解析 .wf 文件，返回 Program AST 节点"""
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    return parse_text(text, source=str(path))


def parse_text(text: str, source: str = "<text>") -> Program:
    """从字符串解析 DSL 文本，返回 Program AST 节点（主要用于测试）"""
    parser = _get_parser()
    # 预处理：换行续行
    text = _preprocess_line_continuation(text)
    # 确保文本以换行结尾（grammar 要求 _NL 终止）
    if not text.endswith("\n"):
        text += "\n"
    tree = parser.parse(text)
    program = _DSLTransformer().transform(tree)
    return Program(body=program.body, imports=program.imports, procs=program.procs, source=source)
