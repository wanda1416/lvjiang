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

from ..fs_util import atomic_write_bytes, interprocess_lock
from ..json_files import formatted_json_bytes
from ..paths import session_directory
from . import versioning

_mutexes: dict[Path, threading.RLock] = {}


@contextmanager
def configuration_lock(config: Path, *, blocking: bool = True) -> Iterator[bool]:
    """进程内协调保存、安装与任务快照；跨进程串行由 :func:`config_write_lock`。

    阻塞调用等待完成；``blocking=False`` 时返回是否拿到（不等待）。
    """
    config = config.resolve()
    mutex = _mutexes.setdefault(config, threading.RLock())
    acquired = mutex.acquire(blocking=blocking)
    if not acquired:
        yield False
        return
    try:
        yield True
    finally:
        mutex.release()


@contextmanager
def config_write_lock(local_dir: Path) -> Iterator[None]:
    """配置写的跨进程锁，与 :func:`configuration_lock` 组成统一加锁顺序。

    全仓库统一「进程内线程锁 ``configuration_lock`` → 跨进程文件锁」：
    ``_configuration_write``、``checked_write``、``batch_changes``、出装搭配
    保存都是这个顺序，反过来会互相等待而永久死锁。

    锁统一放在 ``config/session/.locks``：同一实例的 system/local 写入共用一把，
    也不会把运行时锁文件写进受版本控制的 config 目录。
    """
    with interprocess_lock(local_dir, suffix="install"):
        yield


def indexed_paths(resolver) -> dict[tuple[str, ...], Path]:
    """只枚举名称；任务启动无需读取全部文件内容。"""
    paths: dict[tuple[str, ...], Path] = {}
    for root in (resolver.system_dir, resolver.local_dir):
        for path in versioning.iter_versioned_files(root):
            rel = path.relative_to(root)
            if len(rel.parts) == 1:
                continue  # 根 YAML 每次直接读取，不参与实体索引。
            paths[tuple(rel.with_suffix("").parts)] = path
    return paths


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
        atomic_write_bytes(path, formatted_json_bytes(data), prefix=".content-")
        resolver.clear_read_cache()
        return data


def content_changed(resolver) -> None:
    try:
        rebuild_content(resolver)
    except OSError as exc:
        # 不能保留一份可能让下次任务误用旧缓存的记录。
        (session_directory(resolver.config_dir) / "content.json").unlink(missing_ok=True)
        logger.warning("配置已写入，修订索引将在下次任务重建：{}", exc)
