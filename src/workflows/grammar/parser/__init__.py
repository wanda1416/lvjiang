"""工作流 DSL v2 解析器

基于 Lark 的解析器，将 .wf 文件解析为 AST 节点树。

对外接口：
    parse_file(path) -> Program
    parse_text(text) -> Program
"""

from .api import parse_file, parse_text

__all__ = ["parse_file", "parse_text"]
