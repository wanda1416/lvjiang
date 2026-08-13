"""DSL 语法模块 - 解析器与 AST 定义

提供工作流 DSL 的语法解析能力，将 .wf 文本转换为 AST。
引擎通过此模块感知语法，无需关注解析细节。
"""

from .ast_nodes import (
    And,
    ArithOp,
    Break,
    ByClause,
    CallProc,
    # 语句节点
    Click,
    Collect,
    Contains,
    Continue,
    CoordPoint,
    Drag,
    Equals,
    Eval,
    EvalFieldChainAssign,
    FieldAccess,
    Find,
    For,
    ForRange,
    FuncCall,
    Goto,
    GreaterEqual,
    GreaterThan,
    If,
    Import,
    InList,
    IsEmpty,
    KeywordRef,
    Label,
    LessEqual,
    LessThan,
    Literal,
    Log,
    Loop,
    Not,
    NotEqual,
    NumericEqual,
    Or,
    PanelGridDrag,
    PanelRef,
    ProcDef,
    # 程序
    Program,
    Recognize,
    Return,
    Scan,
    # 表达式节点
    SceneRef,
    Try,
    UntilLoop,
    VarRef,
    Wait,
    WhileLoop,
)
from .parser import parse_file, parse_text

__all__ = [
    "parse_file", "parse_text",
    "Program",
    "Click", "Drag", "Wait", "Scan", "Recognize", "Collect", "Log", "Find",
    "Import", "ProcDef", "CallProc",
    "If", "For", "ForRange", "Loop", "WhileLoop", "UntilLoop", "Break", "Continue", "Return", "Label", "Goto", "Try",
    "Eval", "EvalFieldChainAssign", "FuncCall",
    "SceneRef", "PanelRef", "PanelGridDrag", "VarRef", "KeywordRef", "Literal", "FieldAccess", "CoordPoint", "ByClause",
    "Contains", "Equals", "InList", "IsEmpty",
    "GreaterThan", "LessThan", "GreaterEqual", "LessEqual", "NotEqual", "NumericEqual",
    "Not", "And", "Or", "ArithOp",
]
