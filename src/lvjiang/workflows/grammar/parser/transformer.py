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


class _DSLTransformer(_StmtMixin, _ExprMixin, _ModuleControlMixin, Transformer):
    """将 Lark 解析树转换为 DSL AST 节点"""

    def __init__(self):
        super().__init__()
        self._meta_line = 0

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
