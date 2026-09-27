"""参数依赖表达式 ``require``：借用 DSL 条件语法做静态校验与求值。

工作流参数之间存在真实依赖：``online_role_max_wait`` 只有在
``skip_online_role`` 关掉时才有意义。作者在参数上写一行 ``require``，UI 据此
决定是否展示该参数::

    #%   - name: online_role_max_wait
    #%     label: 最大等待时间（秒）
    #%     require: $skip_online_role == false

语法**就是 DSL 的条件表达式**：表达式经 ``parse_text("if <expr>\\nend\\n")``
走同一套 Lark 语法和 transformer，求值复用引擎的 ``_EvalMixin``。因此
``==`` / ``!=`` / ``in [...]`` / ``and`` / ``or`` / ``not`` / ``contains`` /
``equals`` / ``is_empty`` 与比较语义（数字容差、字符串精确）和 wf 正文完全一致，
不存在“元数据里是另一套规则”的问题；参数名也沿用注入后的 ``$name`` 写法，
与 wf 正文引用同一个参数时的拼写相同。

只允许纯取值节点（变量、字面量、比较、布尔组合）。函数调用、``session`` /
``context``、字段访问和布局实体一律在解析期拒绝——UI 每次改值都要重算可见性，
这里不能触发任何有副作用或依赖运行时环境的求值。

设计边界（与参数级 ``env`` 完全一致，见 ``task_params.parameters_for_env``）：
``require`` **只控制 UI 展示**，不裁剪运行时参数快照、不改写已保存的值。条件
不满足时 wf 依旧拿到该变量（没存过就是声明的 ``default``），避免引用未定义变量
报错；该值此时无意义，由 wf 自己保证不使用它。
"""

from __future__ import annotations

from dataclasses import fields, is_dataclass
from functools import lru_cache
from typing import Any

from ..workflows.engine.evaluation import _EvalMixin
from ..workflows.errors import WorkflowUserError
from ..workflows.grammar.ast_nodes import (
    And,
    Contains,
    Equals,
    GreaterEqual,
    GreaterThan,
    InList,
    IsEmpty,
    LessEqual,
    LessThan,
    Literal,
    Not,
    NotEqual,
    NumericEqual,
    Or,
    VarRef,
)

#: 允许出现在 ``require`` 里的 AST 节点。只有纯取值语义的节点在列——新增条件
#: 类节点时要显式加进来，默认拒绝而不是默认放行。
_ALLOWED_NODES = (
    VarRef, Literal, And, Or, Not, Contains, Equals, InList, IsEmpty,
    GreaterThan, LessThan, GreaterEqual, LessEqual, NotEqual, NumericEqual,
)

#: 允许被 ``require`` 引用的参数类型。``checkgroup`` 的值是 ``{选项: 布尔}``
#: 映射，整体比较没有清晰语义；需要按子项依赖时再单独设计，不在此处放行。
REFERENCEABLE_TYPES = {"bool", "select", "number", "text"}

#: 直接比较形态的节点（左变量、右字面量），用于把比较值和 select 的 options 对账。
_DIRECT_COMPARISONS = (Equals, NumericEqual, NotEqual, InList)


class RequireError(ValueError):
    """``require`` 表达式无法解析、引用不成立或用了不允许的语法。"""


class _RequireEvaluator(_EvalMixin):
    """只带变量表的最小求值宿主，复用引擎的条件求值实现。

    这样 ``require: $mode == "1"`` 与 wf 正文里 ``if $mode == "1"`` 的判定结果
    必然一致——包括 ``==`` 的数字容差与字符串精确比较规则。白名单已排除函数
    调用、session / context 与字段访问，本子集下 ``_EvalMixin`` 只会用到
    ``self.variables``，因此不需要引擎的其余状态。
    """

    def __init__(self, variables: dict[str, Any]) -> None:
        self.variables = variables

    def holds(self, node: Any) -> bool:
        try:
            return bool(self._eval_condition(node))
        except WorkflowUserError as exc:
            raise RequireError(str(exc)) from exc


# ─── 解析 ─────────────────────────────────────────────────


@lru_cache(maxsize=512)
def parse_condition(expression: str) -> Any:
    """把单条条件文本解析成 DSL 条件 AST 节点。

    走 ``if <expr>`` 的合成程序，复用生产解析路径；不为元数据另开语法入口。
    UI 每次改值都会重算可见性，故按表达式文本缓存。
    """
    from ..workflows.grammar import parse_text

    text = expression.strip()
    if not text:
        raise RequireError("条件不能为空")
    if "\n" in text:
        raise RequireError(f"条件必须是单行: {expression!r}")
    try:
        program = parse_text(f"if {text}\nend\n")
    except Exception as exc:  # noqa: BLE001 — Lark 的异常类型不稳定，统一包装
        raise RequireError(
            f"无法按 DSL 条件语法解析 {text!r}: {type(exc).__name__}: {exc}"
        ) from exc
    node = getattr(program.body[0], "condition", None) if program.body else None
    if node is None:
        raise RequireError(f"无法按 DSL 条件语法解析 {text!r}")
    _reject_unsupported(node, text)
    return node


def parse_require(raw: Any) -> list[Any]:
    """解析 ``require`` 的值为条件 AST 列表；列表语义是 AND。

    接受单个字符串或字符串列表；空值返回空列表（等价于无依赖）。
    """
    if raw is None or raw == "" or raw == []:
        return []
    items = raw if isinstance(raw, list) else [raw]
    conditions: list[Any] = []
    for item in items:
        if not isinstance(item, str) or not item.strip():
            raise RequireError("每个条件必须是非空字符串")
        conditions.append(parse_condition(item))
    return conditions


def _walk(node: Any):
    """深度遍历 AST 节点及其子节点（含列表元素）。"""
    yield node
    if is_dataclass(node) and not isinstance(node, type):
        for field_info in fields(node):
            yield from _walk(getattr(node, field_info.name))
    elif isinstance(node, (list, tuple)):
        for item in node:
            yield from _walk(item)


def _reject_unsupported(node: Any, text: str) -> None:
    for child in _walk(node):
        if child is None or isinstance(child, (str, int, float, bool, list, tuple)):
            continue
        if not isinstance(child, _ALLOWED_NODES):
            raise RequireError(
                f"条件 {text!r} 使用了 require 不支持的语法 "
                f"{type(child).__name__}：只能引用参数变量和字面量，"
                "不能调用函数、访问字段或读取 session / context"
            )


def referenced_names(conditions: list[Any]) -> set[str]:
    """条件里引用到的参数名集合。"""
    return {
        child.name
        for condition in conditions
        for child in _walk(condition)
        if isinstance(child, VarRef)
    }


# ─── 静态校验 ─────────────────────────────────────────────


def validate_requires(parameters: list[dict]) -> None:
    """校验一组参数定义里的全部 ``require``；不合法时抛 :class:`RequireError`。

    调用方负责包装成自己的元数据错误格式。拼错一个参数名会让控件永远不出现，
    所以引用关系必须在解析期就对账，而不是等用户发现某个参数“消失了”。
    """
    by_name = {
        str(item["name"]): item for item in parameters
        if isinstance(item, dict) and item.get("name")
    }
    graph: dict[str, set[str]] = {}
    for name, item in by_name.items():
        conditions = parse_require(item.get("require"))
        names = referenced_names(conditions)
        graph[name] = names
        if name in names:
            raise RequireError(f"参数 {name!r} 的 require 不能引用自身")
        for target_name in names:
            target = by_name.get(target_name)
            if target is None:
                raise RequireError(
                    f"参数 {name!r} 的 require 引用了未声明的参数 {target_name!r}")
            target_type = str(target.get("type", "select"))
            if target_type not in REFERENCEABLE_TYPES:
                raise RequireError(
                    f"参数 {name!r} 的 require 不能引用 {target_type} 类型的 "
                    f"{target_name!r}")
        for condition in conditions:
            _validate_direct_literals(name, condition, by_name)
    _reject_cycles(graph)


def _validate_direct_literals(
    name: str, condition: Any, by_name: dict[str, dict],
) -> None:
    """对账「$参数 == 字面量」「$参数 in [...]」里的比较值。

    只查这两种直接形态；更复杂的表达式交给作者自己负责，不在元数据层推演。
    """
    for child in _walk(condition):
        if not isinstance(child, _DIRECT_COMPARISONS):
            continue
        left = child.left
        if not isinstance(left, VarRef):
            continue
        target = by_name.get(left.name)
        if target is None:
            continue
        target_type = str(target.get("type", "select"))
        right = child.right if isinstance(child, InList) else [child.right]
        for value in _literal_values(right):
            if target_type == "bool" and not isinstance(value, bool):
                # 布尔只与布尔相等（$flag == 1 恒假），拿别的类型比一定不成立，
                # 属于写错而不是"条件暂不满足"。
                raise RequireError(
                    f"参数 {name!r} 的 require 比较 bool 参数 {left.name!r} "
                    "时只能用 true / false")
            if target_type == "number" and isinstance(value, (bool, str)):
                raise RequireError(
                    f"参数 {name!r} 的 require 比较 number 参数 {left.name!r} "
                    "时必须用数字")
            if target_type == "select":
                options = {
                    option if isinstance(option, str) else option.get("value")
                    for option in target.get("options") or []
                }
                if options and str(value) not in options:
                    raise RequireError(
                        f"参数 {name!r} 的 require 比较值 {value!r} 不在 "
                        f"{left.name!r} 的 options 中")


def _literal_values(nodes: list) -> list:
    """取出字面量值。DSL 只把字符串/布尔/null 包成 Literal，数字保持原样。"""
    values = []
    for node in nodes:
        if isinstance(node, Literal):
            values.append(node.value)
        elif isinstance(node, (bool, int, float, str)):
            values.append(node)
    return values


def _reject_cycles(graph: dict[str, set[str]]) -> None:
    """依赖成环时控件永远无法出现，解析期就要挡住。"""
    visiting: set[str] = set()
    done: set[str] = set()

    def walk(node: str, chain: list[str]) -> None:
        if node in done:
            return
        if node in visiting:
            raise RequireError(
                "参数 require 形成循环依赖: " + " → ".join([*chain, node]))
        visiting.add(node)
        for target in graph.get(node, ()):
            walk(target, [*chain, node])
        visiting.discard(node)
        done.add(node)

    for node in graph:
        walk(node, [])


# ─── 求值 ─────────────────────────────────────────────────


def visible_parameter_names(
    parameters: list[dict], values: dict[str, Any],
) -> set[str]:
    """按当前取值算出应当展示的参数名集合。

    被引用的参数自己不展示时，依赖它的参数也不展示——条件挂在一个看不见的控件
    上，用户无从满足。``require`` 已排除环，迭代必然收敛。
    """
    by_name = {
        str(item["name"]): item for item in parameters
        if isinstance(item, dict) and item.get("name")
    }
    evaluator = _RequireEvaluator(dict(values))
    conditions = {
        name: parse_require(item.get("require"))
        for name, item in by_name.items()
    }
    visible = set(by_name)
    while True:
        dropped = {
            name for name in visible
            if not _visible_now(name, conditions[name], visible, evaluator)
        }
        if not dropped:
            return visible
        visible -= dropped


def _visible_now(
    name: str, conditions: list[Any], visible: set[str],
    evaluator: _RequireEvaluator,
) -> bool:
    for condition in conditions:
        if not referenced_names([condition]) <= visible:
            return False
        if not evaluator.holds(condition):
            return False
    return True
