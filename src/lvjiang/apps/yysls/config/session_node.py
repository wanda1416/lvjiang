"""燕云插件的会话节点：session.json 的 ``yysls`` 顶层节点。

原先这些数据存在独立文件 ``config/session/yysls.json``，由 play_styles 与
graduation_session 各自实现一遍加载与原子写。改存 session.json 的插件节点后：

- 并发安全由 ``SessionStore.mutate_node`` 的文件锁统一保证，不再各写一份
  临时文件 + ``os.replace`` 的样板
- 运行态集中在一个文件里，备份/排查时不用记住还有哪些散落的旁路文件

**兼容**：仍读旧的独立 ``yysls.json``，但**不再写它**。判据是节点在不在，
不是节点空不空——用 ``None`` 哨兵区分「从没迁移过」与「迁移过、只是内容被
清空了」；若按"空就回退"来判，用户把配置清空后旧文件里的数据会自己爬回来。

节点内的顶层键（与旧文件一致，迁移无需转换）：

- ``play_styles``  流派 → 基础属性
- ``graduations``  流派 → 毕业率基准
"""
from __future__ import annotations

import json
from typing import Any, Callable

from loguru import logger

from lvjiang.constants import SESSION_CONFIG_DIR
from lvjiang.core.config.session import get_session_store

#: session.json 里的插件节点名
NODE = "yysls"

#: 迁移前的独立文件，只读不写
LEGACY_PATH = SESSION_CONFIG_DIR / "yysls.json"


def _load_legacy() -> dict:
    if not LEGACY_PATH.exists():
        return {}
    try:
        data = json.loads(LEGACY_PATH.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 旧文件损坏不该拖垮启动
        logger.error(f"加载旧的 {LEGACY_PATH.name} 失败: {exc}")
        return {}
    return data if isinstance(data, dict) else {}


def load() -> dict:
    """读取插件节点；节点尚不存在时回退到旧文件。"""
    node = get_session_store().get_node(NODE, None)
    if isinstance(node, dict):
        return node
    return _load_legacy()


def mutate(fn: Callable[[dict], dict]) -> None:
    """锁内原子读-改-写。

    首次写入时以旧文件内容为基底，等于顺带完成迁移；旧文件保持不动，
    降级回老版本仍能读到自己的数据。
    """
    def _apply(existing: Any) -> dict:
        base = existing if isinstance(existing, dict) else _load_legacy()
        return fn(dict(base))

    get_session_store().mutate_node(NODE, _apply)
