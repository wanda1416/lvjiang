"""工作流文件头部的 ``#%`` YAML 元数据解析与校验。"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml
from loguru import logger

_META_LINE = re.compile(r"^\s*#%\s?(.*)$")
#: 只收**有消费点**的字段。目录已不再表达脚本语义；
#: 作者排序、停用、版本门槛这类没有当前需求的字段一律不收——
#: 加了不读的字段迟早会烂。
_TOP_LEVEL_FIELDS = {
    "id",
    "name",
    "note",
    "env",
    "parameters",
    "scope",
    "hidden",
    "runnable",
    "batchable",
    "batch_check",
}
#: 脚本 id 是稳定逻辑标识，不是路径。首字符必须是 Unicode 字母，
#: 后续允许 Unicode 字母、数字和下划线；中文脚本名属于合法 id。
#: ``[^\W\d_]`` 是 Python ``re`` 下的“Unicode 单词字符，但不是数字或
#: 下划线”，比手写 CJK 区段完整，也不会退化成只接受 ASCII。
SCRIPT_ID_RE = re.compile(r"^[^\W\d_]\w*$", re.UNICODE)
_PARAMETER_TYPES = {"select", "number", "bool", "checkgroup", "text"}
_PARAMETER_NAME = re.compile(
    r"^[a-zA-Z_\u4e00-\u9fff][a-zA-Z0-9_\u4e00-\u9fff]*$",
)
_COMMON_PARAMETER_FIELDS = {"name", "label", "type", "default", "env"}
_TYPE_PARAMETER_FIELDS = {
    "select": {"options"},
    "number": {"min", "max"},
    "bool": set(),
    "checkgroup": {"options"},
    "text": {"placeholder", "multiline"},
}


class WorkflowMetadataError(ValueError):
    """``.wf`` 文件头部元数据无法解析或不符合约定。"""


METADATA_WARNING = "⚠ 无法识别脚本元数据，请修改脚本文件开头的 #% 定义。"


def _error(path: str, message: str) -> WorkflowMetadataError:
    return WorkflowMetadataError(f"工作流元数据 {path}: {message}")


def _validate_string_list(value: Any, path: str) -> None:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise _error(path, "必须是字符串列表")


def known_envs() -> list[str]:
    """配置层提供的可用环境 key。

    与 UI 环境控件共用 :func:`load_available_envs`，包括配置缺失时的桌面
    回退，避免编辑器能选择的值和元数据校验接受的值不一致。
    """
    from ..core.config.resolver import load_available_envs

    return [key for key, _display in load_available_envs()]


def _validate_env_list(value: Any, path: str) -> None:
    """``env`` 必须是系统参数里存在的环境：拼错一个字母的脚本会在所有环境
    都不显示且无法启动，必须在解析期就报出来。"""
    _validate_string_list(value, path)
    allowed = known_envs()
    if not allowed:
        return
    unknown = [item for item in value if item not in allowed]
    if unknown:
        raise _error(
            path,
            f"包含未知环境 {', '.join(unknown)}；"
            f"系统参数中定义的环境只有 {', '.join(allowed)}",
        )


def _validate_options(
    value: Any, path: str, *, label_required: bool,
) -> list[Any]:
    if not isinstance(value, list) or not value:
        raise _error(path, "必须是非空列表")
    normalized: list[Any] = []
    for index, option in enumerate(value):
        option_path = f"{path}[{index}]"
        if isinstance(option, str):
            normalized.append(option)
            continue
        if not isinstance(option, dict):
            raise _error(option_path, "必须是字符串或 {value, label} 映射")
        if "value" not in option:
            raise _error(option_path, "缺少 value")
        if not isinstance(option["value"], str):
            raise _error(f"{option_path}.value", "必须是字符串")
        if label_required and "label" not in option:
            raise _error(option_path, "select 的映射选项缺少 label")
        if "label" in option and not isinstance(option["label"], str):
            raise _error(f"{option_path}.label", "必须是字符串")
        normalized.append({
            key: option[key] for key in ("value", "label") if key in option
        })
    return normalized


def _validate_parameter(parameter: Any, index: int) -> dict | None:
    path = f"parameters[{index}]"
    if not isinstance(parameter, dict):
        raise _error(path, "必须是键值映射")

    name = parameter.get("name")
    if not isinstance(name, str) or not name.strip():
        raise _error(f"{path}.name", "必须是非空字符串")
    if _PARAMETER_NAME.fullmatch(name) is None:
        raise _error(f"{path}.name", "不是合法的 DSL 变量名")
    if "label" in parameter and not isinstance(parameter["label"], str):
        raise _error(f"{path}.label", "必须是字符串")
    if "env" in parameter:
        _validate_env_list(parameter["env"], f"{path}.env")

    param_type = parameter.get("type", "select")
    if not isinstance(param_type, str):
        raise _error(f"{path}.type", "必须是字符串")
    if param_type not in _PARAMETER_TYPES:
        return None

    allowed = _COMMON_PARAMETER_FIELDS | _TYPE_PARAMETER_FIELDS[param_type]
    normalized = {key: value for key, value in parameter.items() if key in allowed}

    if param_type in {"select", "checkgroup"}:
        if "options" not in parameter:
            raise _error(f"{path}.options", f"{param_type} 参数必须声明 options")
        normalized["options"] = _validate_options(
            parameter["options"],
            f"{path}.options",
            label_required=param_type == "select",
        )
        option_values = {
            option if isinstance(option, str) else option["value"]
            for option in normalized["options"]
        }
        default = parameter.get("default")
        if param_type == "select" and default is not None:
            if not isinstance(default, str) or default not in option_values:
                raise _error(f"{path}.default", "必须是 options 中已有的字符串值")
        if param_type == "checkgroup" and default is not None:
            if not isinstance(default, dict):
                raise _error(f"{path}.default", "必须是 {选项值: 布尔值} 映射")
            if any(not isinstance(key, str) for key in default):
                raise _error(f"{path}.default", "所有选项键都必须是字符串")
            unknown_defaults = set(default) - option_values
            if unknown_defaults:
                raise _error(
                    f"{path}.default",
                    f"包含 options 中不存在的值: {', '.join(sorted(unknown_defaults))}",
                )
            if any(not isinstance(value, bool) for value in default.values()):
                raise _error(f"{path}.default", "所有值都必须是布尔值")
    elif param_type == "number":
        for field in ("min", "max", "default"):
            if field in parameter and (
                not isinstance(parameter[field], int)
                or isinstance(parameter[field], bool)
            ):
                raise _error(f"{path}.{field}", "必须是整数")
        minimum = parameter.get("min", 0)
        maximum = parameter.get("max", 999999)
        if minimum > maximum:
            raise _error(path, "min 不能大于 max")
        default = parameter.get("default")
        if default is not None and not minimum <= default <= maximum:
            raise _error(f"{path}.default", "必须位于 min 与 max 之间")
    elif param_type == "text":
        if "multiline" in parameter and not isinstance(parameter["multiline"], bool):
            raise _error(f"{path}.multiline", "必须是布尔值")
        # 自由文本没有可枚举的取值，只能约束类型：写成数字或布尔多半是
        # YAML 里漏了引号，当场报出来比运行时拿到 True 当兑换码强。
        for field in ("default", "placeholder"):
            if field in parameter and not isinstance(parameter[field], str):
                raise _error(f"{path}.{field}", "必须是字符串")
    elif "default" in parameter:
        default = parameter["default"]
        bool_strings = {"true", "false", "1", "0", "yes", "no", "on", "off"}
        if not isinstance(default, bool) and (
            not isinstance(default, str) or default.lower() not in bool_strings
        ):
            raise _error(f"{path}.default", "必须是布尔值或明确的布尔字符串")
    return normalized


def _validate_metadata(data: Any) -> dict:
    if not isinstance(data, dict):
        raise _error("根节点", "必须是键值映射")

    normalized = {key: value for key, value in data.items()
                  if key in _TOP_LEVEL_FIELDS}

    for field in ("name", "note"):
        if field in normalized and not isinstance(normalized[field], str):
            raise _error(field, "必须是字符串")
    if "name" in normalized and not normalized["name"].strip():
        raise _error("name", "必须是非空字符串")
    if "env" in normalized:
        _validate_env_list(normalized["env"], "env")
    if "id" in normalized and (
            not isinstance(normalized["id"], str)
            or SCRIPT_ID_RE.fullmatch(normalized["id"]) is None):
        raise _error(
            "id", "只能使用 Unicode 字母、数字和下划线，且必须以字母开头")
    if "scope" in normalized and normalized["scope"] not in {"daily", "dedicated"}:
        raise _error("scope", "必须是 daily 或 dedicated")
    if "batch_check" in normalized and (
            not isinstance(normalized["batch_check"], str)
            or _PARAMETER_NAME.fullmatch(normalized["batch_check"]) is None):
        raise _error("batch_check", "必须是合法的 DSL 子过程名")
    for field in ("hidden", "runnable", "batchable"):
        if field in normalized and not isinstance(normalized[field], bool):
            raise _error(field, "必须是布尔值")

    parameters = normalized.get("parameters", [])
    if not isinstance(parameters, list):
        raise _error("parameters", "必须是列表")
    names: set[str] = set()
    normalized_parameters: list[dict] = []
    for index, parameter in enumerate(parameters):
        item = _validate_parameter(parameter, index)
        if item is None:
            continue
        name = item["name"]
        if name in names:
            raise _error(f"parameters[{index}].name", f"参数名重复: {name}")
        names.add(name)
        normalized_parameters.append(item)
    if "parameters" in normalized:
        normalized["parameters"] = normalized_parameters
    return normalized


def parse_metadata(text: str) -> dict:
    """解析文件第一行起连续的 ``#%`` 元数据，并严格校验。"""
    lines: list[str] = []
    for raw in text.splitlines():
        match = _META_LINE.match(raw)
        if match is None:
            break
        lines.append(match.group(1))

    if not lines:
        return {}

    try:
        data = yaml.safe_load("\n".join(lines))
    except yaml.YAMLError as exc:
        raise WorkflowMetadataError(f"工作流元数据 YAML 解析失败: {exc}") from exc
    return _validate_metadata(data)


def parse_metadata_file(path: str | Path) -> dict:
    """读取 ``.wf`` 文件并解析其文件头元数据。"""
    source = Path(path)
    try:
        return parse_metadata(source.read_text(encoding="utf-8-sig"))
    except UnicodeError as exc:
        raise WorkflowMetadataError(f"{source}: 文件不是有效的 UTF-8") from exc
    except OSError as exc:
        logger.warning(f"读取工作流文件失败: {exc}")
        return {}
    except WorkflowMetadataError as exc:
        raise WorkflowMetadataError(f"{source}: {exc}") from exc


def metadata_for_script_config(path: str | Path) -> tuple[dict, str]:
    """为脚本配置读取元数据；单文件错误降级为该脚本自己的警告。

    严格解析器仍负责指出具体错误，但脚本发现和手动加载属于用户入口，
    不能让一个 ``.wf`` 的元数据问题中断调用方或污染其他脚本。
    """
    try:
        return parse_metadata_file(path), ""
    except Exception as exc:  # noqa: BLE001 — 单个用户/remote WF 不得拖垮发现全集
        logger.error(f"{METADATA_WARNING} {type(exc).__name__}: {exc}")
        return {}, METADATA_WARNING


def metadata_error(text: str) -> str:
    """返回可展示的 front-matter 错误；空字符串表示通过。"""
    try:
        parse_metadata(text)
    except Exception as exc:  # noqa: BLE001 — 编辑器必须把问题显示出来
        return f"{type(exc).__name__}: {exc}"
    return ""


def script_traits(meta: dict) -> dict:
    """取出「性质」类字段并套用默认值。

    **未声明即不可注册、不可批量**：发现层递归扫描 workflows 全树，被
    import 的过程库、批量生命周期钩子、归档文件都和正式脚本混在同一棵树
    里，只有作者显式声明 ``runnable`` / ``batchable`` 的才注册为脚本。反
    过来写默认值 true，任何漏写元数据的内部文件都会冒到用户的日常列表里。

    ``batchable`` 隐含 ``runnable``——能排进批量的必然有独立入口，作者不
    需要为同一个脚本写两行。
    """
    return {
        "runnable": bool(meta.get("runnable", False))
        or bool(meta.get("batchable", False)),
        "batchable": bool(meta.get("batchable", False)),
        "hidden": bool(meta.get("hidden", False)),
        "scope": meta.get("scope") or "daily",
    }


def build_flow_config(path: str | Path) -> dict:
    """由 ``.wf`` 路径及其元数据构造 GUI 使用的 flow 配置。"""
    source = Path(path)
    meta, warning = metadata_for_script_config(source)
    return {
        "id": f"__loaded__:{source.stem}",
        "name": meta.get("name") or f"[外部] {source.name}",
        "note": warning or meta.get("note") or "",
        "wf_file": str(source),
        "runnable": True,      # 用户显式加载的文件就是要跑的
        "batchable": False,    # 临时项不参与批量编排
        "batch_check": "",
        "scope": meta.get("scope") or "daily",
        "parameters": meta.get("parameters") or [],
        "env": meta.get("env") or [],
    }
