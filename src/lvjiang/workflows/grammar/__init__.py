"""DSL 语法模块 - 解析器与 AST 定义

提供工作流 DSL 的语法解析能力，将 .wf 文本转换为 AST。
引擎通过此模块感知语法，无需关注解析细节。
"""

from .parser import parse_file, parse_text
from .ast_nodes import (
    # 程序
    Program,
    # 语句节点
    Click, Drag, Wait, Scan, Recognize, Collect, Log,
    Import, ProcDef, CallProc,
    If, For, ForRange, Loop, WhileLoop, UntilLoop, Break, Continue, Return, Label, Goto, Try,
    Eval, EvalFieldChainAssign, FuncCall,
    # 表达式节点
    SceneRef, PanelRef, PanelGridDrag, VarRef, KeywordRef, Literal, FieldAccess, CoordPoint, ByClause,
    Contains, Equals, InList, IsEmpty,
    GreaterThan, LessThan, GreaterEqual, LessEqual, NotEqual, NumericEqual,
    Not, And, Or, ArithOp,
)

__all__ = [
    "parse_file", "parse_text",
    "Program",
    "Click", "Drag", "Wait", "Scan", "Recognize", "Collect", "Log",
    "Import", "ProcDef", "CallProc",
    "If", "For", "ForRange", "Loop", "WhileLoop", "UntilLoop", "Break", "Continue", "Return", "Label", "Goto", "Try",
    "Eval", "EvalFieldChainAssign", "FuncCall",
    "SceneRef", "PanelRef", "PanelGridDrag", "VarRef", "KeywordRef", "Literal", "FieldAccess", "CoordPoint", "ByClause",
    "Contains", "Equals", "InList", "IsEmpty",
    "GreaterThan", "LessThan", "GreaterEqual", "LessEqual", "NotEqual", "NumericEqual",
    "Not", "And", "Or", "ArithOp",
]
