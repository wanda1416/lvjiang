"""工作流 DSL 解析对外接口与 Lark 实例管理

对外接口：
    parse_file(path) -> Program
    parse_text(text) -> Program
"""

from copy import deepcopy
from dataclasses import replace
from functools import lru_cache
from pathlib import Path
from threading import RLock

from lark import Lark

from ..ast_nodes import Program
from .transformer import _DSLTransformer

# ─── Lark 实例（延迟初始化） ──────────────────────────────

_parser: Lark | None = None
_file_parse_lock = RLock()


def _get_parser() -> Lark:
    global _parser
    if _parser is None:
        grammar_path = Path(__file__).parent.parent / "grammar.lark"
        _parser = Lark(
            grammar_path.read_text(encoding="utf-8"),
            parser="lalr",
            lexer="contextual",
            propagate_positions=True,
        )
    return _parser


# ─── 预处理：换行续行 ────────────────────────────────────

def _preprocess_line_continuation(text: str) -> tuple[str, list[int]]:
    """处理两种换行续行，并记下每个输出字符来自原文的哪个下标。

    1. 显式续行：行尾反斜杠 \\ → 与下一行拼接
    2. 隐式续行：{} [] () 内部的换行视为空格（不终结语句）

    被吐掉的换行会在逻辑行结束后补回（以空行形式），保证后续语句
    的行号与源文件一致 —— 静态校验的报错要报真实行号。
    补出来的换行没有原文下标，记为 -1。调用方用这张表把解析器看到的
    token 位置还原到续行之前的源文本。
    """
    result: list[str] = []
    origin: list[int] = []
    depth = 0
    in_string = False
    pending_nl = 0  # 当前逻辑行内吐掉的换行数
    i = 0

    def emit(ch: str, src: int) -> None:
        result.append(ch)
        origin.append(src)

    while i < len(text):
        ch = text[i]
        if ch == '"' and (i == 0 or text[i - 1] != '\\'):
            in_string = not in_string
            emit(ch, i)
        elif not in_string and ch == '\\' and text[i + 1:i + 2] in ('\n', '\r'):
            # 显式续行：\\\n 或 \\\r\n。空格占住反斜杠的位置，换行稍后补回。
            emit(' ', i)
            pending_nl += 1
            i += 2
            if text[i - 1] == '\r' and text[i:i + 1] == '\n':
                i += 1  # 跳过 \r\n 的 \n
            continue
        elif not in_string:
            if ch in ('(', '[', '{'):
                depth += 1
                emit(ch, i)
            elif ch in (')', ']', '}'):
                depth = max(0, depth - 1)
                emit(ch, i)
            elif ch == '\n' and depth > 0:
                # 括号内换行 → 空格
                emit(' ', i)
                pending_nl += 1
            elif ch == '\n':
                # 逻辑行结束：补回吐掉的换行，保住总行数
                emit('\n', i)
                for _ in range(pending_nl):
                    emit('\n', -1)
                pending_nl = 0
            else:
                emit(ch, i)
        else:
            emit(ch, i)
        i += 1
    for _ in range(pending_nl):
        emit('\n', -1)
    return ''.join(result), origin


# ─── 公共接口 ─────────────────────────────────────────────

def parse_file(path: Path | str) -> Program:
    """按实际内容复用文件解析结果，每次返回独立 AST。

    不按 mtime 判断，保留同大小/同时间戳修改、删除和 import 热加载语义。
    缓存仅保存语法树，不能共享其中的可变列表或字典给引擎。
    """
    path = Path(path)
    text = path.read_text(encoding="utf-8-sig")
    # lru_cache 自身允许并发 miss 重复计算；显式锁避免多个任务同时解析
    # 同一文件。读取和副本构造不持锁，缓存内容不会交给调用方修改。
    with _file_parse_lock:
        program = _parse_cached_file(str(path.resolve()), text)
    return replace(deepcopy(program), source=str(path))


@lru_cache(maxsize=128)
def _parse_cached_file(source: str, text: str) -> Program:
    return parse_text(text, source=source)


def parse_text(text: str, source: str = "<text>") -> Program:
    """从字符串解析 DSL 文本，返回 Program AST 节点（主要用于测试）"""
    parser = _get_parser()
    # 预处理：换行续行。origin[i] 是预处理文本第 i 个字符在原文中的下标。
    original = text
    text, origin = _preprocess_line_continuation(original)
    # 确保文本以换行结尾（grammar 要求 _NL 终止）
    if not text.endswith("\n"):
        text += "\n"
        origin.append(-1)
    tree = parser.parse(text)
    transformer = _DSLTransformer()
    transformer.set_source_map(original, text, origin)
    program = transformer.transform(tree)
    return Program(body=program.body, imports=program.imports, procs=program.procs, source=source)
