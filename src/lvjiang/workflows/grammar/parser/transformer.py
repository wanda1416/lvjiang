"""_DSLTransformer：Parse Tree → AST（Mixin 组合）

Lark Transformer 按规则名 getattr(self, rule) 派发回调，各规则回调按
职责分拆到三个 Mixin，经 MRO 组合到本类；Transformer 置于 MRO 末端。
跨组共用的静态工具（_line / _unquote / _ensure_literal）挂在本类上，
供各 Mixin 经 self. 调用。
"""

from lark import Token, Transformer, Tree

from ..ast_nodes import Literal
from .expressions import _ExprMixin
from .modules_control import _ModuleControlMixin
from .statements import _StmtMixin


def _line_col_to_offset(text: str, line: int, col: int) -> int | None:
    """1-based 行列转下标。``col`` 可以落在行尾的换行上，但不能超出该行。"""
    if line <= 0 or col <= 0:
        return None
    offset = 0
    current = 1
    while current < line:
        newline = text.find("\n", offset)
        if newline < 0:
            return None
        offset = newline + 1
        current += 1
    line_end = text.find("\n", offset)
    if line_end < 0:
        line_end = len(text)
    target = offset + col - 1
    if target > line_end:
        return None
    return target


def _offset_to_line_col(text: str, offset: int) -> tuple[int, int]:
    """下标转 1-based 行列。"""
    line = text.count("\n", 0, offset) + 1
    previous = text.rfind("\n", 0, offset)
    return line, offset - previous


class _DSLTransformer(_StmtMixin, _ExprMixin, _ModuleControlMixin, Transformer):
    """将 Lark 解析树转换为 DSL AST 节点"""

    def __init__(self):
        super().__init__()
        self._meta_line = 0
        self._original = ""
        self._preprocessed = ""
        self._origin: list[int] = []

    def set_source_map(self, original: str, preprocessed: str, origin: list[int]) -> None:
        """记录续行预处理的下标映射，供过程名位置还原到源文本。"""
        self._original = original
        self._preprocessed = preprocessed
        self._origin = origin

    def _call_userfunc(self, tree, new_children=None):
        """规则回调前记下本子树的起始行号，供 _line 兜底

        Lark 自底向上转换，规则回调拿到的子节点多已是 AST 对象，Token 已
        消失，_line 从中取不到行号（恒 0）。转换器本身持有 parse tree，
        propagate_positions=True 又保证 meta 带位置，故在派发前留一份。
        """
        meta = getattr(tree, "meta", None)
        if meta is not None and not getattr(meta, "empty", True):
            self._meta_line = getattr(meta, "line", 0) or 0
        return super()._call_userfunc(tree, new_children)

    # ─── 工具方法 ─────────────────────────────────────────

    def _line(self, items) -> int:
        """从子节点中提取行号，取不到则用当前规则子树的起始行"""
        for item in items:
            if isinstance(item, Token) and hasattr(item, 'line'):
                return item.line or 0
            if hasattr(item, 'line_no') and item.line_no:
                return item.line_no
            if isinstance(item, Tree) and hasattr(item, 'meta') and item.meta:
                return getattr(item.meta, 'line', 0)
        return self._meta_line

    def _token_span(self, token) -> tuple[int, int, int]:
        """终端在源文本中的 1-based 行号，以及 1-based 半开列区间。

        Lark 的行列是续行预处理之后的。过程名若写在反斜杠换行之后，
        预处理会把它并进上一行；这里按原文下标还原，取不到位置时返回 0。
        """
        if not isinstance(token, Token):
            return 0, 0, 0
        line = int(token.line or 0)
        start = int(token.column or 0)
        end = int(getattr(token, "end_column", 0) or 0)
        if start <= 0:
            return 0, 0, 0
        if end <= start:
            end = start + len(str(token))
        return self._remap_span(line, start, end)

    def _remap_span(self, line: int, start: int, end: int) -> tuple[int, int, int]:
        if not self._origin or len(self._origin) != len(self._preprocessed):
            return line, start, end
        start_off = _line_col_to_offset(self._preprocessed, line, start)
        last_off = _line_col_to_offset(self._preprocessed, line, end - 1)
        if start_off is None or last_off is None:
            return 0, 0, 0
        if not (0 <= start_off < len(self._origin) and 0 <= last_off < len(self._origin)):
            return 0, 0, 0
        src_start = self._origin[start_off]
        src_last = self._origin[last_off]
        if src_start < 0 or src_last < src_start:
            return 0, 0, 0
        start_line, start_col = _offset_to_line_col(self._original, src_start)
        last_line, last_col = _offset_to_line_col(self._original, src_last)
        if start_line != last_line:
            return 0, 0, 0
        return start_line, start_col, last_col + 1

    @staticmethod
    def _unquote(s: str) -> str:
        """去除字符串两端的双引号"""
        if s.startswith('"') and s.endswith('"'):
            return s[1:-1]
        return s

    @staticmethod
    def _ensure_literal(node) -> Literal:
        """确保返回 Literal（处理 STRING Token 未被子规则转换的情况）"""
        if isinstance(node, Literal):
            return node
        if isinstance(node, Token):
            s = str(node)
            if s.startswith('"') and s.endswith('"'):
                s = s[1:-1]
            return Literal(value=s)
        return node

    @classmethod
    def _normalize_expr(cls, node):
        """把 grammar 直出的标量统一包装为 Literal，AST 节点原样透传。"""
        if isinstance(node, Literal):
            return node
        if isinstance(node, Token):
            return Literal(value=cls._unquote(str(node)))
        if node is None or isinstance(node, (bool, int, float, str)):
            return Literal(value=node)
        return node
