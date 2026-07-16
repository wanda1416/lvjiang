"""工作流 DSL v2 AST 节点定义

所有节点均为不可变 dataclass，便于 Transformer 构造与引擎模式匹配。

节点分三类：
- 程序：Program
- 语句：Click / Drag / Wait / Scan / ClickMatch / Collect / Log
         If / For / Loop / Break / Return / Label / Goto / Eval
- 表达式：VarRef / Literal / FieldAccess / Contains / Equals / InList / IsEmpty
          Not / And / Or
"""

from dataclasses import dataclass, field
from typing import Any


# ─── 程序 ─────────────────────────────────────────────────

@dataclass(frozen=True)
class Program:
    body: list  # list[语句节点]
    source: str = "<text>"


# ─── 语句 ─────────────────────────────────────────────────

@dataclass(frozen=True)
class Click:
    scene: Any      # Literal | VarRef
    field: Any      # Literal | VarRef
    line_no: int = 0


@dataclass(frozen=True)
class Drag:
    scene: Any      # Literal | VarRef
    arrow: Any      # Literal | VarRef
    duration: Any = None  # Literal(秒数) | list[Literal](二元组范围) | None(默认)
    hold: float | None = None  # 到达目标后按住不放的时长（秒）
    line_no: int = 0


@dataclass(frozen=True)
class Wait:
    delay: Any      # Literal（命名延迟或秒数）
    line_no: int = 0


@dataclass(frozen=True)
class Scan:
    scene: Any      # Literal | VarRef
    target: Any     # VarRef（必须为变量，as 子句）
    fields: list | None = None  # list[Literal] | None
    line_no: int = 0


@dataclass(frozen=True)
class ClickMatch:
    scene: Any      # VarRef（场景名）
    var: Any        # VarRef（变量名，读取 OCR 结果）
    text: Any       # Literal
    error_msg: Any | None = None  # Literal | None
    line_no: int = 0


@dataclass(frozen=True)
class Collect:
    source: Any     # VarRef（要收集的变量）
    alias: str | None = None  # 可选别名
    line_no: int = 0


@dataclass(frozen=True)
class Log:
    message: Any    # Literal
    line_no: int = 0


@dataclass(frozen=True)
class If:
    condition: Any  # 表达式节点
    then_body: list = field(default_factory=list)
    else_body: list = field(default_factory=list)
    line_no: int = 0


@dataclass(frozen=True)
class For:
    var: str                    # 循环变量名（裸字符串）
    iterable: list              # list[Literal]（字面量字符串列表）
    body: list = field(default_factory=list)
    line_no: int = 0


@dataclass(frozen=True)
class Loop:
    count: int
    body: list = field(default_factory=list)
    line_no: int = 0


@dataclass(frozen=True)
class Break:
    line_no: int = 0


@dataclass(frozen=True)
class Return:
    line_no: int = 0


@dataclass(frozen=True)
class Label:
    name: str
    line_no: int = 0


@dataclass(frozen=True)
class Goto:
    target: str
    line_no: int = 0


@dataclass(frozen=True)
class Eval:
    """eval [var =] func(args...)"""
    func_name: str
    func_args: list             # list[Literal | VarRef]
    target: str | None = None   # 赋值目标变量名，None 表示丢弃返回值
    line_no: int = 0


# ─── 表达式 ───────────────────────────────────────────────

@dataclass(frozen=True)
class VarRef:
    """变量引用：[name]"""
    name: str


@dataclass(frozen=True)
class Literal:
    """字面量字符串"""
    value: str


@dataclass(frozen=True)
class FieldAccess:
    """[var].field"""
    var: VarRef
    field_name: str


@dataclass(frozen=True)
class Contains:
    left: FieldAccess
    right: Any      # Literal
    line_no: int = 0


@dataclass(frozen=True)
class Equals:
    left: FieldAccess
    right: Any
    line_no: int = 0


@dataclass(frozen=True)
class InList:
    left: FieldAccess
    right: list     # list[Literal]
    line_no: int = 0


@dataclass(frozen=True)
class IsEmpty:
    expr: FieldAccess
    line_no: int = 0


@dataclass(frozen=True)
class Not:
    operand: Any
    line_no: int = 0


@dataclass(frozen=True)
class And:
    left: Any
    right: Any
    line_no: int = 0


@dataclass(frozen=True)
class Or:
    left: Any
    right: Any
    line_no: int = 0
