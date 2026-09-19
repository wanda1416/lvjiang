"""燕云插件的会话节点：session.json 的 ``yysls`` 顶层节点。

节点内的顶层键：

- ``play_styles``  流派 → 基础属性
- ``graduations``  流派 → 毕业率基准
"""
from __future__ import annotations

from typing import Any, Callable

from lvjiang.core.config.session import get_session_store

#: session.json 里的插件节点名
NODE = "yysls"


def load() -> dict:
    """读取插件节点。"""
    node = get_session_store().get_node(NODE, {})
    return node if isinstance(node, dict) else {}


def mutate(fn: Callable[[dict], dict]) -> None:
    """锁内原子读-改-写。"""
    def _apply(existing: Any) -> dict:
        base = existing if isinstance(existing, dict) else {}
        return fn(dict(base))

    get_session_store().mutate_node(NODE, _apply)
