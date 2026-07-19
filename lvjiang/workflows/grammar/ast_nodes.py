"""工作流 DSL v2 AST 节点定义

所有节点均为不可变 dataclass，便于 Transformer 构造与引擎模式匹配。

节点分三类：
- 程序：Program
- 语句：Click / Drag / Wait / Scan / Recognize / Collect / Log
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
    target: Any     # SceneRef（静态 [scene].[region]）| VarRef（动态 $var，find 产出坐标）| CoordPoint（画布归一化坐标）
    line_no: int = 0


@dataclass(frozen=True)
class Drag:
    scene: Any      # SceneRef（静态 [scene].[region]）| None（坐标模式）
    arrow: Any      # SceneRef | None（坐标模式）
    duration: Any = None  # Literal(秒数) | list[Literal](二元组范围) | None(默认)
    hold: float | None = None  # 到达目标后按住不放的时长（秒）
    from_point: Any = None  # CoordPoint | None（坐标模式起点）
    to_point: Any = None    # CoordPoint | None（坐标模式终点）
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
    region_var: Any = None  # VarRef | None（动态 region，如 [scene].$var）
    by: Any = None  # ByClause | None（by 子句：有则返回字段名 str，无则返回 dict）
    line_no: int = 0


@dataclass(frozen=True)
class Recognize:
    scene: Any      # SceneRef（静态场景引用）
    target: Any     # VarRef（$var，as 子句）
    fields: list | None = None  # list[Literal] | None
    region_var: Any = None  # VarRef | None（动态 region，如 [scene].$var）
    by: Any = None  # ByClause | None（by 子句：有则返回字段名 str，无则返回 dict）
    group: Any = None  # Literal | VarRef | None（group 子句：限定材料分组）
    line_no: int = 0


@dataclass(frozen=True)
class ByClause:
    """scan/recognize 的 by 子句 —— 短路识别策略

    match_mode:
        - "equals"        精确匹配单值（target 为 str）
        - "contains"      子串匹配单值（target 为 str）
        - "equals_any"    精确匹配列表任一元素（target 必须为 list）
        - "contains_any"  子串匹配列表任一元素（target 必须为 list）
    target:
        Literal（字符串常量）或 VarRef（运行时变量，求值后须匹配 match_mode 要求类型）

    语义：一次截图后逐字段识别，首个命中即返回该字段名（str）；全部未命中返回 ""。
    """
    match_mode: str
    target: Any     # Literal | VarRef


@dataclass(frozen=True)
class Collect:
    source: Any         # VarRef | FieldAccess（要收集的值）
    alias: str | None = None      # 静态别名（字面量字符串）
    alias_var: Any | None = None  # 动态别名（VarRef）
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
    iterable: Any               # list[Literal]（静态列表）| VarRef（动态列表变量）
    body: list = field(default_factory=list)
    line_no: int = 0


@dataclass(frozen=True)
class Loop:
    count: Any      # int | str(NAME) | VarRef
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
class EvalFieldChainAssign:
    """eval $dict.key = value 或 eval $dict.key1.key2 = value — 字段赋值"""
    target: Any         # FieldAccess — 字段访问链
    value: Any          # Literal | FuncCall | VarRef
    line_no: int = 0


@dataclass(frozen=True)
class FuncCall:
    """函数调用：func_name($arg1, "arg2", ...)"""
    func_name: str
    func_args: list     # list[Literal | VarRef]
    line_no: int = 0


@dataclass(frozen=True)
class Call:
    """call "sub.wf" with $x as "arg1" read "key" as $var"""
    workflow: Any               # Literal（wf 文件路径）
    args: list = field(default_factory=list)       # [(as_side, as_side), ...] with 传入参数
    reads: list = field(default_factory=list)      # [(as_side, as_side), ...] read 读取返回值
    line_no: int = 0


# ─── 表达式 ───────────────────────────────────────────────

@dataclass(frozen=True)
class SceneRef:
    """静态配置引用：[scene] 或 [scene].[region]（region 支持 $var 动态引用）"""
    scene: str
    region: str | None = None  # str | VarRef | None


@dataclass(frozen=True)
class CoordPoint:
    """画布归一化坐标点 (rx, ry ∈ [0,1])

    录制产生的坐标字面量，不依赖 scene/region 定义。
    回放时经画布配置 + 当前窗口位置反算为屏幕绝对坐标。
    """
    rx: float
    ry: float


@dataclass(frozen=True)
class VarRef:
    """运行时变量引用：$name"""
    name: str


@dataclass(frozen=True)
class KeywordRef:
    """DSL 关键字引用：session / context

    与 VarRef 不同，KeywordRef 不查 variables 字典，
    而是由引擎直接返回对应的持久/临时状态对象。
    """
    name: str          # "session" | "context"


@dataclass(frozen=True)
class Literal:
    """字面量字符串"""
    value: str


@dataclass(frozen=True)
class FieldAccess:
    """$var.field 或 session.field（链式访问）

    root 为 VarRef / KeywordRef（最外层变量/关键字）或另一个 FieldAccess（链式嵌套）。
    例如 $ring.affix_1.value 表示为：
        FieldAccess(root=FieldAccess(root=VarRef('ring'), field_name='affix_1'), field_name='value')
    例如 session.current_user 表示为：
        FieldAccess(root=KeywordRef('session'), field_name='current_user')
    """
    root: Any          # VarRef | KeywordRef | FieldAccess
    field_name: Any    # str | VarRef | Literal


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
    """$var > number 或 $var.field > number"""
    left: Any  # VarRef | FieldAccess
    right: float
    line_no: int = 0


@dataclass(frozen=True)
class LessThan:
    """$var < number 或 $var.field < number"""
    left: Any  # VarRef | FieldAccess
    right: float
    line_no: int = 0


@dataclass(frozen=True)
class GreaterEqual:
    """$var >= number 或 $var.field >= number"""
    left: Any  # VarRef | FieldAccess
    right: float
    line_no: int = 0


@dataclass(frozen=True)
class LessEqual:
    """$var <= number 或 $var.field <= number"""
    left: Any  # VarRef | FieldAccess
    right: float
    line_no: int = 0


@dataclass(frozen=True)
class NotEqual:
    """$var != number 或 $var.field != number"""
    left: Any  # VarRef | FieldAccess
    right: float
    line_no: int = 0


@dataclass(frozen=True)
class NumericEqual:
    """$var == number 或 $var.field == number"""
    left: Any  # VarRef | FieldAccess
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
