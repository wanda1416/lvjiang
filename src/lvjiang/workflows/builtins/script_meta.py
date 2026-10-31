"""外置脚本的静态元数据扫描

管理界面要展示 ``config/local/workflows`` 下的 .py 脚本函数，但**不能 import**：
import 会执行模块顶层代码，管理界面为了"看一眼"而执行用户代码，等于把
「外置脚本只从本地层加载」这道安全边界又打开。所以这里只做 AST 解析，读
装饰器字面量、函数参数、docstring 与行号。

运行时加载（``WorkflowEngine._load_script_modules``）走的是编译执行，但这
两条路径共用同一个"什么算公开函数"的判据：``@builtin_func("name")`` 装饰的
顶层函数。判据只在这里写一次，界面与实际可调用范围才不会各说各话。
"""

import ast
from dataclasses import dataclass, field
from pathlib import Path

#: 脚本自身没声明 GROUP、装饰器也没写 group 时的兜底分类
DEFAULT_SCRIPT_GROUP = "本地脚本"

#: 引擎注入的首参，不展示
INJECTED_PARAMS = frozenset({"_engine", "_wf"})


@dataclass(frozen=True)
class ScannedFunction:
    """从脚本源码里静态读出的一个函数声明。"""

    name: str
    func_name: str
    group: str
    summary: str
    params: dict[str, str] = field(default_factory=dict)
    returns: str = ""
    path: Path | None = None
    rel_path: str = ""
    line: int = 0
    #: 首参为 _engine/_wf 时记录注入类型；这类函数不能在面板里试算
    injected: str | None = None
    #: (参数名, 默认值源码, 类型注解源码)，顺序与函数签名一致
    param_specs: list[tuple[str, str, str]] = field(default_factory=list)


def scan_script(
    path: Path,
    *,
    rel_path: str = "",
    fallback_group: str = DEFAULT_SCRIPT_GROUP,
) -> list[ScannedFunction]:
    """解析单个脚本，返回其声明的全部函数。

    语法错误不在这里吞掉：调用方（面板）据此提示"这个脚本有语法错误"，
    比静默跳过更有用。
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    module_group = _module_group(tree) or fallback_group
    found: list[ScannedFunction] = []
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        meta = _decorator_meta(node)
        if meta is None:
            continue
        name = meta.get("name")
        if not name:
            continue
        params = meta.get("params") or {}
        found.append(ScannedFunction(
            name=name,
            func_name=node.name,
            group=meta.get("group") or module_group,
            summary=meta.get("summary") or _docstring_summary(node),
            params={label: params[label] for label in params if isinstance(label, str)},
            returns=meta.get("returns") or _return_annotation(node),
            path=path,
            rel_path=rel_path or path.name,
            line=node.lineno,
            injected=_injected_kind(node),
            param_specs=_param_specs(node),
        ))
    return found


def scan_directory(root: Path) -> list[ScannedFunction]:
    """递归扫描目录下的全部 .py 脚本；目录不存在返回空列表。

    单个文件解析失败不影响其它文件——坏脚本要能在界面上被看见并报错，
    而不是让整个列表加载失败。
    """
    if not root.is_dir():
        return []
    scanned: list[ScannedFunction] = []
    for path in sorted(root.rglob("*.py")):
        rel_path = path.relative_to(root).as_posix()
        try:
            scanned.extend(scan_script(path, rel_path=rel_path))
        except (OSError, SyntaxError, UnicodeDecodeError):
            continue
    return scanned


def script_problems(root: Path) -> list[str]:
    """列出无法解析的脚本文件，供面板提示。"""
    if not root.is_dir():
        return []
    problems: list[str] = []
    for path in sorted(root.rglob("*.py")):
        try:
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError as exc:
            problems.append(
                f"{path.relative_to(root).as_posix()}: 语法错误（第 {exc.lineno} 行）")
        except (OSError, UnicodeDecodeError) as exc:
            problems.append(f"{path.relative_to(root).as_posix()}: 无法读取（{exc}）")
    return problems


# ─── AST 提取 ─────────────────────────────────────────────


def _decorator_meta(node) -> dict | None:
    """解析 ``@builtin_func("name", group=..., ...)``，非该装饰器返回 None。

    只认字面量：名字必须是字符串常量，params 必须是字符串到字符串的字典字面量。
    用变量拼出来的装饰器参数静态读不到，界面上会表现为缺少含义——这是静态
    扫描的固有代价，好在脚本通常不会那样写。
    """
    for decorator in node.decorator_list:
        if not isinstance(decorator, ast.Call):
            continue
        func = decorator.func
        is_builtin = (
            (isinstance(func, ast.Name) and func.id == "builtin_func")
            or (isinstance(func, ast.Attribute) and func.attr == "builtin_func")
        )
        if not is_builtin:
            continue
        meta: dict = _keyword_literals(decorator.keywords)
        if decorator.args:
            first = decorator.args[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                meta["name"] = first.value
        return meta
    return None


def _keyword_literals(keywords) -> dict:
    """收集装饰器的字面量关键字参数，非字面量忽略"""
    meta: dict = {}
    for keyword in keywords:
        if keyword.arg is None:
            continue
        value = keyword.value
        if keyword.arg == "params":
            meta["params"] = _string_dict(value)
        elif keyword.arg in {"group", "summary", "returns"}:
            text = _constant_str(value)
            if text is not None:
                meta[keyword.arg] = text
    return meta


def _string_dict(node) -> dict[str, str]:
    if not isinstance(node, ast.Dict):
        return {}
    result: dict[str, str] = {}
    for key, value in zip(node.keys, node.values, strict=False):
        key_text = _constant_str(key)
        value_text = _constant_str(value)
        if key_text is not None and value_text is not None:
            result[key_text] = value_text
    return result


def _constant_str(node) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _injected_kind(node) -> str | None:
    """首参为 _engine / _wf 时记录注入类型"""
    args = node.args.args
    if not args:
        return None
    first = args[0].arg
    if first == "_engine":
        return "engine"
    if first == "_wf":
        return "wf"
    return None


def _param_specs(node) -> list[tuple[str, str, str]]:
    """按签名顺序列出 (名字, 默认值源码, 注解源码)，跳过引擎注入首参"""
    args = node.args
    specs: list[tuple[str, str, str]] = []
    positional = [*args.posonlyargs, *args.args]
    defaults = list(args.defaults)
    offset = len(positional) - len(defaults)
    for index, arg in enumerate(positional):
        if arg.arg in INJECTED_PARAMS:
            continue
        default = ""
        if index >= offset:
            default = _unparse(defaults[index - offset])
        specs.append((arg.arg, default, _unparse(arg.annotation)))
    if args.vararg is not None:
        specs.append((f"*{args.vararg.arg}", "", _unparse(args.vararg.annotation)))
    for arg, kw_default in zip(args.kwonlyargs, args.kw_defaults, strict=False):
        specs.append((arg.arg, _unparse(kw_default), _unparse(arg.annotation)))
    if args.kwarg is not None:
        specs.append((f"**{args.kwarg.arg}", "", _unparse(args.kwarg.annotation)))
    return specs


def _unparse(node) -> str:
    if node is None:
        return ""
    try:
        return ast.unparse(node)
    except Exception:  # noqa: BLE001 - 源码无法还原时留空即可
        return ""


def _module_group(tree) -> str:
    """模块级 ``GROUP = "..."`` 声明"""
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == "GROUP":
                text = _constant_str(node.value)
                if text:
                    return text
    return ""


def _docstring_summary(node) -> str:
    doc = ast.get_docstring(node) or ""
    return doc.splitlines()[0].strip() if doc else ""


def _return_annotation(node) -> str:
    if node.returns is None:
        return ""
    try:
        return ast.unparse(node.returns)
    except Exception:  # noqa: BLE001 - 注解无法还原时留空即可
        return ""
