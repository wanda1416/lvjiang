"""配置基础设施 —— 全部配置读写的唯一收口

两类数据、两个入口：
- 元数据（config/system + config/local）→ ConfigResolver
  双层合并读、按模式路由写（开发→system，用户→local 影子/diff/墓碑）
- 运行态（config/session/session.json）→ SessionStore
  全量内存缓存 + 线程锁 + 写即原子落盘，节点级 get/set

另有：
- SessionManager：users/{name}.json 的用户级 session 持久化
- load_yaml / save_yaml：通用 YAML 读写助手
"""
from pathlib import Path
from typing import Any

import yaml

from .resolver import (
    DELETED_KEY,
    TOMBSTONE_SUFFIX,
    ConfigResolver,
    compute_diff,
    get_resolver,
    merge_doc,
)
from .session import SessionStore, get_session_store
from .users import SessionManager

__all__ = [
    "DELETED_KEY",
    "TOMBSTONE_SUFFIX",
    "ConfigResolver",
    "compute_diff",
    "get_resolver",
    "merge_doc",
    "SessionStore",
    "get_session_store",
    "SessionManager",
    "load_yaml",
    "save_yaml",
]


def load_yaml(path: Path) -> dict[str, Any]:
    """加载 YAML 文件（不存在返回空 dict）"""
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data if data else {}


def save_yaml(path: Path, data: dict[str, Any]) -> None:
    """保存 YAML 文件（自动建父目录，保序不排序键）"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
