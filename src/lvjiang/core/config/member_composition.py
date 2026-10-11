"""r0/public 后叠加会员文件；共享登记表按 key 合并，不预检业务内容。"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

import yaml
from loguru import logger

from .resolver import merge_doc, merge_registry_list

REGISTRIES = frozenset({"scenes.yaml", "layouts.yaml"})


def _merge(base: Any, addition: Any) -> Any:
    if isinstance(addition, dict) and any(key in addition for key in ("__added__", "__removed__", "__order__")):
        return merge_registry_list(base if isinstance(base, list) else [], addition)
    if isinstance(base, list) and isinstance(addition, list):
        return base + [item for item in addition if item not in base]
    if isinstance(addition, dict):
        original = base if isinstance(base, dict) else {}
        result = merge_doc(original, addition)
        for key, value in addition.items():
            if key == "__deleted__":
                continue
            result[key] = _merge(original.get(key), value)
        return result
    return deepcopy(addition)


def compose(official: dict[str, bytes], member: dict[str, bytes]) -> dict[str, bytes]:
    result = dict(official)
    for name, payload in member.items():
        if name in REGISTRIES:
            try:
                base = yaml.safe_load(result[name].decode("utf-8")) if name in result else {}
                addition = yaml.safe_load(payload.decode("utf-8"))
                if isinstance(base, dict) and isinstance(addition, dict):
                    result[name] = yaml.safe_dump(_merge(base, addition), allow_unicode=True).encode("utf-8")
                    continue
            except (yaml.YAMLError, UnicodeError, TypeError, ValueError):
                pass
            # 无法合并也不拒绝整包：保留后层原文，问题交给该文件的消费者。
            logger.warning("登记表无法按 key 合并，按会员文件原文覆盖保存，仅影响对应配置：{}", name)
            result[name] = payload
        else:
            result[name] = payload
    return result
