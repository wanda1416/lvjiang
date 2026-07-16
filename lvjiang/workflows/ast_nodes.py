"""工作流 DSL v2 AST 节点定义

所有节点均为不可变 dataclass，便于 Transformer 构造与引擎模式匹配。

节点分三类：
- 程序：Program
- 语句：Click / Drag / Wait / Scan / Find / Collect / Log
         If / For / Loop / Break / Return / Label / Goto / Eval
- 表达式：SceneRef / VarRef / Literal / FieldAccess / Contains / Equals / InList / IsEmpty
          Not / And / Or

引用语义：
- SceneRef → 静态配置引用（场景名/区域名），来自 yaml，语法 [scene].[region]
- VarRef   → 运行时变量引用，来自 variables dict，语法 $var
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
    target: Any     # SceneRef（静态 [scene].[region]）| VarRef（动态 $var，find 产出坐标）
    line_no: int = 0


@dataclass(frozen=True)
class Drag:
    scene: Any      # SceneRef（静态 [scene].[region]）
    arrow: Any      # SceneRef
    duration: Any = None  # Literal(秒数) | list[Literal](二元组范围) | None(默认)
    hold: float | None = None  # 到达目标后按住不放的时长（秒）
    line_no: int = 0


@dataclass(frozen=True)
class Wait:
    delay: Any      # Literal（命名延迟或秒数）
    line_no: int = 0


@dataclass(frozen=True)
class Scan:
    scene: Any      # SceneRef（静态场景引用）
    target: Any     # VarRef（$var，as 子句）
    fields: list | None = None  # list[Literal] | None
    line_no: int = 0


@dataclass(frozen=True)
class Recognize:
    scene: Any      # SceneRef（静态场景引用）
    target: Any     # VarRef（$var，as 子句）
    fields: list | None = None  # list[Literal] | None
    line_no: int = 0


@dataclass(frozen=True)
class Find:
    """在 scan 结果中查找文本，将坐标存入变量"""
    source: Any     # VarRef（scan 结果变量）
    text: Any       # Literal（要查找的文本）
    target: Any     # VarRef（坐标输出变量）
    error_msg: Any | None = None  # Literal | None
    line_no: int = 0


@dataclass(frozen=True)
class Collect:
    source: Any     # VarRef（要收集的变量）
    alias: str | None = None  # 可选别名（字面量字符串）
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
    """eval $var = func($arg...)"""
    func_name: str
    func_args: list             # list[Literal | VarRef]
    target: str | None = None   # 赋值目标变量名，None 表示丢弃返回值
    line_no: int = 0


@dataclass(frozen=True)
class EvalFieldAssign:
    """eval $dict.key = value"""
    var_name: str       # 字典变量名
    field_name: str     # 字段名
    value: Any          # Literal | FuncCall
    line_no: int = 0


@dataclass(frozen=True)
class FuncCall:
    """函数调用：func_name($arg1, "arg2", ...)"""
    func_name: str
    func_args: list     # list[Literal | VarRef]
    line_no: int = 0


@dataclass(frozen=True)
class Call:
    """call "sub.wf" with $x as arg1 read "key" as $var"""
    workflow: Any               # Literal（wf 文件路径）
    args: list = field(default_factory=list)       # [(VarRef, str), ...] 传入参数
    reads: list = field(default_factory=list)      # [(str, VarRef), ...] 读取返回值
    line_no: int = 0


# ─── 表达式 ───────────────────────────────────────────────

@dataclass(frozen=True)
class SceneRef:
    """静态配置引用：[scene] 或 [scene].[region]"""
    scene: str
    region: str | None = None


@dataclass(frozen=True)
class VarRef:
    """运行时变量引用：$name"""
    name: str


@dataclass(frozen=True)
class Literal:
    """字面量字符串"""
    value: str


@dataclass(frozen=True)
class FieldAccess:
    """$var.field 或 $var.field1.field2（链式访问）

    root 为 VarRef（最外层变量）或另一个 FieldAccess（链式嵌套）。
    例如 $ring.affix_1.value 表示为：
        FieldAccess(root=FieldAccess(root=VarRef('ring'), field_name='affix_1'), field_name='value')
    """
    root: Any          # VarRef | FieldAccess
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
class GreaterThan:
    """$var.field > number"""
    left: FieldAccess
    right: float
    line_no: int = 0


@dataclass(frozen=True)
class LessThan:
    """$var.field < number"""
    left: FieldAccess
    right: float
    line_no: int = 0


@dataclass(frozen=True)
class GreaterEqual:
    """$var.field >= number"""
    left: FieldAccess
    right: float
    line_no: int = 0


@dataclass(frozen=True)
class LessEqual:
    """$var.field <= number"""
    left: FieldAccess
    right: float
    line_no: int = 0


@dataclass(frozen=True)
class NotEqual:
    """$var.field != number"""
    left: FieldAccess
    right: float
    line_no: int = 0


@dataclass(frozen=True)
class NumericEqual:
    """$var.field == number"""
    left: FieldAccess
    right: float
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
