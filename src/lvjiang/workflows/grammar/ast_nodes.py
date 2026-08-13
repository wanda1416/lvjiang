"""工作流 DSL v2 AST 节点定义

所有节点均为不可变 dataclass，便于 Transformer 构造与引擎模式匹配。

节点分三类：
- 程序：Program
- 语句：Click / Drag / Wait / Scan / Recognize / Collect / Log / Align
         If / For / Loop / Break / Return / Label / Goto / Eval
- 表达式：SceneRef / PanelRef / VarRef / Literal / FieldAccess / Contains / Equals / InList / IsEmpty
          Not / And / Or

引用语义：
- SceneRef → 静态配置引用（场景名/区域名），来自 yaml，语法 [scene].[region]
- PanelRef → panel 三级索引（场景名/panel名/行/列），语法 [scene].[panel][row][col]
- VarRef   → 运行时变量引用，来自 variables dict，语法 $var
"""

from dataclasses import dataclass, field
from typing import Any

# ─── 程序 ─────────────────────────────────────────────────

@dataclass(frozen=True)
class Program:
    body: list  # list[语句节点]（不含 import/def）
    imports: list = field(default_factory=list)  # list[Import]
    procs: dict = field(default_factory=dict)    # dict[str, ProcDef]
    source: str = "<text>"


# ─── 语句 ─────────────────────────────────────────────────

@dataclass(frozen=True)
class Click:
    target: Any     # SceneRef（静态 [scene].[region]）| PanelRef（[scene].[panel][row][col]）| VarRef（动态 $var，find 产出坐标）| CoordPoint（画布归一化坐标）
    line_no: int = 0


@dataclass(frozen=True)
class Drag:
    scene: Any      # SceneRef（静态 [scene].[region]）| PanelRef（panel 三级索引）| None（坐标/点对模式）
    arrow: Any      # SceneRef | None（坐标/点对模式）
    duration: Any = None  # Literal(秒数) | list[Literal](二元组范围) | None(默认)
    hold: float | None = None  # 到达目标后按住不放的时长（秒）
    from_point: Any = None  # CoordPoint | None（坐标模式起点）
    to_point: Any = None    # CoordPoint | None（坐标模式终点）
    from_scene_ref: Any = None  # SceneRef | None（点对模式起点）
    to_scene_ref: Any = None    # SceneRef | None（点对模式终点）
    direction: str | None = None   # "up" | "down" | "left" | "right" | None（panel 拖拽方向）
    distance: Any = 1.0            # 拖拽距离：float | VarRef（支持整数、浮点数如 0.5、变量引用）
    line_no: int = 0


@dataclass(frozen=True)
class Align:
    """align [scene].[panel] — 触发 panel 区域截图 + 图像自对齐

    引擎在 panel 区域内运行方差分析/黑边检测，缓存每个 slot 的精确坐标，
    后续 click [scene].[panel][row][col] 从缓存读取坐标。
    """
    scene: str      # 场景名（静态）
    panel: str      # panel key（静态）
    line_no: int = 0


@dataclass(frozen=True)
class Wait:
    delay: Any      # Literal（命名延迟或秒数）
    line_no: int = 0


@dataclass(frozen=True)
class Scan:
    scene: Any      # SceneRef（静态场景引用）| PanelRef（panel cell 级）
    target: Any     # VarRef（$var，as 子句）
    fields: list | None = None  # list[Literal] | None
    region_var: Any = None  # VarRef | None（动态 region，如 [scene].$var）
    by: Any = None  # ByClause | None（by 子句：有则返回字段名 str，无则返回 dict）
    line_no: int = 0


@dataclass(frozen=True)
class Recognize:
    scene: Any      # SceneRef（静态场景引用）| PanelRef（panel cell 级）
    target: Any     # VarRef（$var，as 子句）
    fields: list | None = None  # list[Literal] | None
    region_var: Any = None  # VarRef | None（动态 region，如 [scene].$var）
    by: Any = None  # ByClause | None（by 子句：有则返回字段名 str，无则返回 dict）
    group: Any = None  # Literal | VarRef | None（group 子句：限定材料分组）
    line_no: int = 0


@dataclass(frozen=True)
class Find:
    """find 指令：在指定区域或全画布 OCR 搜索目标文字，产出可点击的 FoundRegion

    与 scan/recognize 共享 scene_target + by_clause 语法。
    search_scene / search_region: 搜索区域（均为 None 时搜索全画布）
    var_name: 结果变量名（as $var）
    by: ByClause（必填，指定匹配模式和搜索目标）
    """
    var_name: str       # 结果变量名（不含 $ 前缀）
    by: Any             # ByClause（必填）
    search_scene: Any = None    # str | VarRef | None（搜索场景名）
    search_region: Any = None   # str | VarRef | None（搜索区域名）
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
    source: Any         # VarRef | FieldAccess | Literal（要收集的值）
    alias: str | None = None      # 静态别名（字面量字符串）
    alias_var: Any | None = None  # 动态别名（VarRef）
    line_no: int = 0


@dataclass(frozen=True)
class Log:
    message: Any    # Literal
    line_no: int = 0


@dataclass(frozen=True)
class Screenshot:
    """截取当前画面并保存到 logs/image/"""
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
class ForRange:
    """for i in [start...end] — 闭区间范围迭代"""
    var: str                    # 循环变量名
    start: Any                  # 起始值（Literal | VarRef）
    end: Any                    # 结束值（Literal | VarRef），闭区间
    body: list = field(default_factory=list)
    line_no: int = 0


@dataclass(frozen=True)
class Loop:
    count: Any      # int | str(NAME) | VarRef
    body: list = field(default_factory=list)
    line_no: int = 0


@dataclass(frozen=True)
class WhileLoop:
    """loop while <condition> ... end — 条件循环（条件为真继续）"""
    condition: Any  # 条件表达式节点
    body: list = field(default_factory=list)
    line_no: int = 0


@dataclass(frozen=True)
class UntilLoop:
    """loop until <condition> ... end — 条件循环（条件为假继续，即条件为真退出）"""
    condition: Any  # 条件表达式节点
    body: list = field(default_factory=list)
    line_no: int = 0


@dataclass(frozen=True)
class Break:
    line_no: int = 0


@dataclass(frozen=True)
class Continue:
    """continue — 跳过当前循环迭代，进入下一轮"""
    line_no: int = 0


@dataclass(frozen=True)
class Try:
    """try ... catch $err ... end — 异常处理

    body: try 块内的语句列表
    catch_body: catch 块内的语句列表（可选，无 catch 子句时为空）
    err_var: catch 绑定的错误消息变量名（可选，无绑定则为 None）
    """
    body: list = field(default_factory=list)
    catch_body: list = field(default_factory=list)
    err_var: str | None = None
    line_no: int = 0


@dataclass(frozen=True)
class Return:
    """return [value] — 返回值可选"""
    value: Any = None  # 返回值表达式（arith_expr 节点），None 表示无返回值
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
class Import:
    """import "path.wf" — 引入外部文件的 def 定义"""
    path: str
    line_no: int = 0


@dataclass(frozen=True)
class ProcDef:
    """def proc_name($p1, $p2) ... end"""
    name: str
    params: list          # list[str] — 参数名列表
    body: list            # list[statement]


@dataclass(frozen=True)
class CallProc:
    """call proc_name($arg1, "arg2", ...) 或 call $result = proc_name(...) [as $output]"""
    name: str
    args: list            # list[Literal | VarRef | number]
    result_var: str | None = None  # 返回值绑定变量名，None 表示不绑定
    output_var: str | None = None  # output dict 绑定变量名，None 表示丢弃
    line_no: int = 0


# ─── 表达式 ───────────────────────────────────────────────

@dataclass(frozen=True)
class SceneRef:
    """静态配置引用：[scene] 或 [scene].[region]（region 支持 $var 动态引用）"""
    scene: str
    region: str | None = None  # str | VarRef | None


@dataclass(frozen=True)
class PanelRef:
    """panel 三级索引：[scene].[panel][row][col]

    scene/panel 为静态名称（str）；row/col 可为 int（字面量）或 VarRef（运行时变量）。
    引擎执行时查 panel 校准缓存获取格子中心坐标。
    """
    scene: str
    panel: str
    row: Any      # int | VarRef
    col: Any      # int | VarRef


@dataclass(frozen=True)
class PanelGridDrag:
    """panel grid 级拖拽：drag [scene].[panel] up|down|left|right [n]

    起点为 panel 中心，拖拽距离按 slot+span/2 计算（支持浮点数，如 0.5 表示半行）。
    """
    scene: str
    panel: str
    direction: str        # "up" | "down" | "left" | "right"
    distance: Any = 1.0   # float | VarRef（支持整数、浮点数、变量引用）
    line_no: int = 0


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
    """字面量（字符串、数字、null、bool）"""
    value: Any


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
    """$var > expr 或 $var.field > expr"""
    left: Any  # VarRef | FieldAccess
    right: Any  # float | ArithOp | VarRef | FieldAccess
    line_no: int = 0


@dataclass(frozen=True)
class LessThan:
    """$var < expr 或 $var.field < expr"""
    left: Any  # VarRef | FieldAccess
    right: Any  # float | ArithOp | VarRef | FieldAccess
    line_no: int = 0


@dataclass(frozen=True)
class GreaterEqual:
    """$var >= expr 或 $var.field >= expr"""
    left: Any  # VarRef | FieldAccess
    right: Any  # float | ArithOp | VarRef | FieldAccess
    line_no: int = 0


@dataclass(frozen=True)
class LessEqual:
    """$var <= expr 或 $var.field <= expr"""
    left: Any  # VarRef | FieldAccess
    right: Any  # float | ArithOp | VarRef | FieldAccess
    line_no: int = 0


@dataclass(frozen=True)
class NotEqual:
    """$var != expr 或 $var.field != expr"""
    left: Any  # VarRef | FieldAccess
    right: Any  # float | ArithOp | VarRef | FieldAccess
    line_no: int = 0


@dataclass(frozen=True)
class NumericEqual:
    """$var == expr 或 $var.field == expr"""
    left: Any  # VarRef | FieldAccess
    right: Any  # float | ArithOp | VarRef | FieldAccess
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


@dataclass(frozen=True)
class ArithOp:
    """算术二元运算：+ - * /

    left/right 为任意可求值节点（Literal / VarRef / FieldAccess / ArithOp / FuncCall）。
    引擎求值时统一走 _resolve → _to_number。
    """
    op: str           # "+" | "-" | "*" | "/"
    left: Any
    right: Any
    line_no: int = 0
