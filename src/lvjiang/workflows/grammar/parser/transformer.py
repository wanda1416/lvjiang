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

    # ─── 工具方法 ─────────────────────────────────────────

    @staticmethod
    def _line(items) -> int:
        """从子节点中提取行号"""
        for item in items:
            if isinstance(item, Token) and hasattr(item, 'line'):
                return item.line or 0
            if hasattr(item, 'line_no') and item.line_no:
                return item.line_no
            if isinstance(item, Tree) and hasattr(item, 'meta') and item.meta:
                return getattr(item.meta, 'line', 0)
        return 0

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
