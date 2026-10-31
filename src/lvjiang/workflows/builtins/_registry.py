"""内置函数注册表核心

提供装饰器、可展示元数据与查询接口，各子模块通过 builtin_func 装饰器注册函数。

元数据为什么写在装饰器参数里：管理界面（脚本编辑 → 内置函数）与文档都靠它。
写在函数定义旁边，新增函数时漏不掉；写成外部表格则会漂移——本仓库的实际
证据是文档里 101 个、注册表里 102 个。

参数名、顺序、默认值与类型注解**不在这里重复声明**：它们由签名现取，
``params`` 只补"这个名字是什么意思"。这样签名改了不会留下对不上的旧文案。
"""

import inspect
import sys
import threading
from collections.abc import Callable, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field

# 全局函数注册表
_FUNCTION_REGISTRY: dict[str, Callable] = {}

# 外置脚本加载期间的捕获栈：非空时 @builtin_func 写进栈顶，不进全局表。
# 必须按线程隔离：工作流跑在 WorkflowWorker 线程，管理界面的「试算」跑在
# 界面线程，两边同时加载脚本时共用一个栈会让函数落进别人的桶。
_CAPTURE_LOCAL = threading.local()


def _capture_stack() -> list[dict[str, Callable]]:
    """当前线程的捕获栈，首次访问时创建"""
    stack = getattr(_CAPTURE_LOCAL, "stack", None)
    if stack is None:
        stack = []
        _CAPTURE_LOCAL.stack = stack
    return stack

#: 模块未声明 ``GROUP`` 且装饰器未指定 group 时的兜底分类
FALLBACK_GROUP = "其他"

#: 引擎自动注入的首参，不在管理界面的参数表里展示
INJECTED_PARAMS = frozenset({"_engine", "_wf"})


@dataclass(frozen=True)
class FunctionMeta:
    """内置函数的可展示元数据。"""

    name: str
    group: str
    summary: str
    params: dict[str, str] = field(default_factory=dict)
    returns: str = ""
    module: str = ""
    line: int | None = None


@dataclass(frozen=True)
class ParamSpec:
    """从真实签名推导出的单个参数，含义取自元数据。"""

    name: str
    default: str
    annotation: str
    meaning: str


def builtin_func(
    name: str,
    *,
    group: str | None = None,
    summary: str | None = None,
    params: Mapping[str, str] | None = None,
    returns: str | None = None,
):
    """装饰器：注册内置函数，并记录管理界面要展示的元数据。

    除 name 外全部可选，缺省时自动推导：分组取模块级 ``GROUP``、简介取
    docstring 首行、返回值取返回注解。但 ``params`` 的参数含义无法推导，
    新增函数时应顺手写全，否则管理界面只能显示参数名。

    用法：
        @builtin_func(
            "split",
            group="字符串处理",
            summary="按分隔符拆分为列表",
            params={"s": "待拆分文本", "sep": "分隔符；空串表示按字符拆"},
            returns="list[str]",
        )
        def _split(s, *args):
            ...

    变参用 ``"*args"`` / ``"**kwargs"`` 作为 key；``_engine`` / ``_wf``
    是引擎注入参数，不要写进 params。
    """
    def decorator(fn: Callable):
        fn._inject = _inject_kind(fn)
        fn._meta = FunctionMeta(
            name=name,
            group=group or _module_group(fn),
            summary=summary or _docstring_summary(fn),
            params=dict(params or {}),
            returns=_annotation_text(fn) if returns is None else returns,
            module=getattr(fn, "__module__", "") or "",
            line=_first_line(fn),
        )
        stack = _capture_stack()
        if stack:
            stack[-1][name] = fn
        else:
            _FUNCTION_REGISTRY[name] = fn
        return fn
    return decorator


@contextmanager
def capture_registrations():
    """捕获块内 ``@builtin_func`` 的注册结果，不写入全局注册表。

    外置脚本（``config/local/workflows`` 下的 .py）用它把函数装进引擎私有
    命名空间：捕获栈按线程隔离，并发执行的引擎各捕获各自的，互不覆盖；
    脚本改完重新执行即生效，捕获结果随块退出即丢弃。
    """
    bucket: dict[str, Callable] = {}
    stack = _capture_stack()
    stack.append(bucket)
    try:
        yield bucket
    finally:
        stack.pop()


def get_function(name: str) -> Callable | None:
    """获取已注册的内置函数，不存在返回 None"""
    return _FUNCTION_REGISTRY.get(name)


def list_functions() -> list[str]:
    """返回所有已注册函数名"""
    return list(_FUNCTION_REGISTRY.keys())


def describe_function(name: str) -> FunctionMeta | None:
    """返回函数的展示元数据，不存在或无元数据返回 None"""
    fn = _FUNCTION_REGISTRY.get(name)
    return getattr(fn, "_meta", None) if fn is not None else None


def iter_function_metas() -> list[FunctionMeta]:
    """按函数名排序列出全部函数的元数据"""
    metas = []
    for name in sorted(_FUNCTION_REGISTRY):
        meta = getattr(_FUNCTION_REGISTRY[name], "_meta", None)
        if meta is not None:
            metas.append(meta)
    return metas


def signature_params(fn: Callable) -> list[ParamSpec]:
    """按真实签名列出参数，含义取自元数据（未声明则为空串）

    引擎注入的 ``_engine`` / ``_wf`` 首参不展示：它们在 DSL 里不是用户参数。
    """
    meta = getattr(fn, "_meta", None)
    meanings = meta.params if meta is not None else {}
    specs: list[ParamSpec] = []
    for param in inspect.signature(fn).parameters.values():
        if param.name in INJECTED_PARAMS:
            continue
        if param.kind is inspect.Parameter.VAR_POSITIONAL:
            label = f"*{param.name}"
        elif param.kind is inspect.Parameter.VAR_KEYWORD:
            label = f"**{param.name}"
        else:
            label = param.name
        default = (
            "" if param.default is inspect.Parameter.empty else repr(param.default)
        )
        specs.append(ParamSpec(
            name=label,
            default=default,
            annotation=_type_text(param.annotation),
            meaning=meanings.get(label, ""),
        ))
    return specs


def validate_function_meta(name: str) -> list[str]:
    """检查单个函数的元数据与真实签名是否自洽，返回问题清单。

    供测试与面板共用：测试里当门禁，面板里把问题标给用户看。
    """
    fn = _FUNCTION_REGISTRY.get(name)
    if fn is None:
        return [f"{name}: 未注册"]
    meta = getattr(fn, "_meta", None)
    if meta is None:
        return [f"{name}: 缺少元数据"]
    problems: list[str] = []
    if not meta.group:
        problems.append(f"{name}: 缺少分组")
    if not meta.summary:
        problems.append(f"{name}: 缺少简介")
    try:
        labels = {spec.name for spec in signature_params(fn)}
    except (TypeError, ValueError):
        return problems
    unknown = sorted(set(meta.params) - labels)
    if unknown:
        problems.append(
            f"{name}: params 里的 {unknown} 不在签名 {sorted(labels)} 中")
    return problems


# ─── 推导辅助 ─────────────────────────────────────────────


def _inject_kind(fn: Callable) -> str | None:
    """首参为 _engine / _wf 时记录注入类型，避免每次调用都反射"""
    try:
        parameters = list(inspect.signature(fn).parameters)
    except (TypeError, ValueError):
        return None
    if parameters and parameters[0] == "_engine":
        return "engine"
    if parameters and parameters[0] == "_wf":
        return "wf"
    return None


def _module_group(fn: Callable) -> str:
    """模块级 GROUP 声明，作为该文件所有函数的缺省分组"""
    module = sys.modules.get(getattr(fn, "__module__", ""))
    return getattr(module, "GROUP", FALLBACK_GROUP)


def _docstring_summary(fn: Callable) -> str:
    doc = inspect.getdoc(fn) or ""
    return doc.splitlines()[0].strip() if doc else ""


def _annotation_text(fn: Callable) -> str:
    """返回注解的展示文本；无注解返回空串"""
    try:
        annotation = inspect.signature(fn).return_annotation
    except (TypeError, ValueError):
        return ""
    return _type_text(annotation)


def _type_text(annotation) -> str:
    if annotation is inspect.Signature.empty:
        return ""
    if isinstance(annotation, str):
        return annotation
    if isinstance(annotation, type):
        return annotation.__name__
    return str(annotation).replace("typing.", "")


def _first_line(fn: Callable) -> int | None:
    code = getattr(fn, "__code__", None)
    return getattr(code, "co_firstlineno", None)
