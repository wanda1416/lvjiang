"""可重建的实体修订索引；不改变来源优先级，不校验业务内容。"""
from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from loguru import logger

from ..fs_util import atomic_write_bytes
from ..json_files import formatted_json_bytes
from ..paths import session_directory
from . import versioning

_mutexes: dict[Path, threading.RLock] = {}


@contextmanager
def configuration_lock(config: Path, *, blocking: bool = True) -> Iterator[bool]:
    """主节点内协调保存、安装与任务快照，不产生文件锁。"""
    config = config.resolve()
    mutex = _mutexes.setdefault(config, threading.RLock())
    acquired = mutex.acquire(timeout=5) if blocking else mutex.acquire(blocking=False)
    if not acquired and blocking:
        raise TimeoutError("配置操作等待超过 5 秒，请等待当前保存或安装完成后重试")
    if not acquired:
        yield False
        return
    try:
        yield True
    finally:
        mutex.release()


def indexed_paths(resolver) -> dict[tuple[str, ...], Path]:
    """只枚举名称；任务启动无需读取全部文件内容。"""
    from ...apps import load_config_policies
    load_config_policies()
    paths: dict[tuple[str, ...], Path] = {}
    for root in (resolver.system_dir, resolver.local_dir):
        for path in versioning.iter_versioned_files(root):
            rel = path.relative_to(root)
            if len(rel.parts) == 1:
                continue  # 根 YAML 每次直接读取，不参与实体索引。
            paths[tuple(rel.with_suffix("").parts)] = path
    return paths


def _lookup(data: dict, keys: tuple[str, ...]) -> Any:
    value: Any = data
    for key in keys:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def rebuild_content(resolver) -> dict:
    """刷新和启动的强制路径；重建只读修订号，不修改配置文件。"""
    with configuration_lock(resolver.config_dir):
        path = session_directory(resolver.config_dir) / "content.json"
        data: dict[str, Any] = {}
        now = datetime.now(timezone.utc)
        try:
            previous = json.loads(path.read_text(encoding="utf-8"))
            previous_time = datetime.fromisoformat(previous["update_at"])
            if previous_time.tzinfo is not None and now <= previous_time:
                now = previous_time + timedelta(microseconds=1)
        except (OSError, ValueError, KeyError, TypeError):
            pass
        data["update_at"] = now.isoformat(timespec="microseconds")
        for keys, source in indexed_paths(resolver).items():
            node = data
            for key in keys[:-1]:
                node = node.setdefault(key, {})
            node[keys[-1]] = versioning.read_version(source) or 1
        from ..access import is_readonly
        if not is_readonly():
            atomic_write_bytes(path, formatted_json_bytes(data), prefix=".content-")
        resolver.clear_read_cache()
        return data


def load_content(resolver) -> dict:
    """不存在、损坏或 key 缺失一律重建，不阻断任务。"""
    path = session_directory(resolver.config_dir) / "content.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not isinstance(data.get("update_at"), str):
            raise ValueError("缺少 update_at")
        if datetime.fromisoformat(data["update_at"]).tzinfo is None:
            raise ValueError("update_at 缺少时区")
        expected = indexed_paths(resolver)
        for keys in expected:
            value = _lookup(data, keys)
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                raise ValueError("实体修订记录缺失")
        # 文件删除同样会使缓存失效。
        def leaves(node, prefix=()):
            for key, value in node.items():
                if not prefix and key == "update_at":
                    continue
                if isinstance(value, dict):
                    yield from leaves(value, (*prefix, key))
                else:
                    yield (*prefix, key)
        if set(leaves(data)) != set(expected):
            raise ValueError("实体列表变化")
        return data
    except (OSError, ValueError, TypeError):
        logger.debug("配置修订索引缺失或不完整，从磁盘重建")
        return rebuild_content(resolver)


def content_changed(resolver) -> None:
    from ..access import is_readonly
    if is_readonly():
        resolver.clear_read_cache()
        return
    try:
        rebuild_content(resolver)
    except OSError as exc:
        # 不能保留一份可能让下次任务误用旧缓存的记录。
        (session_directory(resolver.config_dir) / "content.json").unlink(missing_ok=True)
        logger.warning("配置已写入，修订索引将在下次任务重建：{}", exc)
