"""会员共享登记表只能补充公开内容；实体不能靠下载先后决定归属。"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

import yaml

REGISTRIES = frozenset({"scenes.yaml", "layouts.yaml"})


def _new(value: Any, path: str) -> Any:
    if isinstance(value, dict):
        if "__added__" in value:
            return _add([], value, path)
        if any(key.startswith("__") for key in value):
            raise ValueError(f"会员登记表包含不支持的删除或排序指令: {path}")
        return {key: _new(item, f"{path}.{key}") for key, item in value.items()}
    return deepcopy(value)


def _add(base: Any, addition: Any, path: str) -> Any:
    if isinstance(base, list) and isinstance(addition, dict):
        if set(addition) - {"__added__", "__order__", "__removed__"} or addition.get("__removed__"):
            raise ValueError(f"会员包不能移除公开登记项: {path}")
        addition = addition.get("__added__", [])
    if isinstance(base, list) and isinstance(addition, list):
        return base + [item for item in addition if item not in base]
    if isinstance(base, dict) and isinstance(addition, dict):
        result = deepcopy(base)
        for key, value in addition.items():
            if key == "content_version":
                continue
            if key.startswith("__"):
                raise ValueError(f"会员登记表不能修改公开条目顺序或删除条目: {path}")
            result[key] = _add(result[key], value, f"{path}.{key}") if key in result else _new(value, f"{path}.{key}")
        return result
    if base != addition:
        raise ValueError(f"公开与会员登记项冲突: {path}")
    return deepcopy(base)


def compose(official: dict[str, bytes], member: dict[str, bytes]) -> dict[str, bytes]:
    result = dict(official)
    for name, payload in member.items():
        if name in REGISTRIES:
            base = yaml.safe_load(result[name].decode("utf-8")) if name in result else {}
            addition = yaml.safe_load(payload.decode("utf-8"))
            if not isinstance(base, dict) or not isinstance(addition, dict):
                raise ValueError(f"登记表必须是映射: {name}")
            result[name] = yaml.safe_dump(_add(base, addition, name), allow_unicode=True).encode("utf-8")
        elif name not in result:
            result[name] = payload
        else:
            raise ValueError(f"公开与会员文件归属冲突: {name}")
    return result
