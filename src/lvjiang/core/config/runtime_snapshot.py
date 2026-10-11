"""运行任务的配置读取上下文；不改变 UI 的活动配置与编辑器状态。"""
from __future__ import annotations

import shutil
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from functools import wraps
from pathlib import Path
from typing import Any, TypeVar

T = TypeVar("T")


@dataclass
class RuntimeSnapshot:
    resolver: Any
    original_system: Path
    original_local: Path
    values: dict[object, Any] = field(default_factory=dict)

    def relocate(self, path: Path) -> Path:
        for original, frozen in ((self.original_system, self.resolver.system_dir),
                                 (self.original_local, self.resolver.local_dir)):
            try:
                return frozen / path.relative_to(original)
            except ValueError:
                continue
        return path


_current: ContextVar[RuntimeSnapshot | None] = ContextVar("runtime_configuration", default=None)


def current_snapshot() -> RuntimeSnapshot | None:
    return _current.get()


def configuration_roots(system: Path) -> set[str]:
    """配置读取范围：官方目录与已登记消费者，不包含 local 里的仓库或诊断数据。"""
    from ..ocr_config import _LEGACY_RULES_PATH
    from .versioning import VERSIONED_DIRS, VERSIONED_FILES
    roots = {path.name for path in system.iterdir()} if system.is_dir() else set()
    roots.update({"app.yaml", "ocr.yaml", _LEGACY_RULES_PATH,
                  "scenes", "layouts", "workflows", "templates", "references", "maps"})
    roots.update(name.split("/")[0] for name in (*VERSIONED_DIRS, *VERSIONED_FILES))
    return roots


def scoped_value(key: object, factory: Callable[[], T]) -> T:
    snapshot = _current.get()
    if snapshot is None:
        return factory()
    if key not in snapshot.values:
        snapshot.values[key] = factory()
    return snapshot.values[key]


def scoped_cache(function):
    """复用已有进程缓存，但任务内另建按参数隔离的缓存，不读刷新中的全局单例。"""
    @wraps(function)
    def wrapped(*args, **kwargs):
        snapshot = _current.get()
        if snapshot is None:
            return function(*args, **kwargs)
        key = (function, args, tuple(sorted(kwargs.items())))
        factory = getattr(function, "__wrapped__", function)
        return scoped_value(key, lambda: factory(*args, **kwargs))
    if hasattr(function, "cache_clear"):
        wrapped.cache_clear = function.cache_clear
    return wrapped


@contextmanager
def configuration_snapshot() -> Iterator[RuntimeSnapshot | None]:
    """与安装共用锁复制读取层；资源延迟读取也留在独立目录，退出任务后回收。"""
    from .resolver import ConfigResolver, get_resolver
    from .update_store import UpdateStore

    active = _current.get()
    if active is not None:
        yield active
        return
    resolver = get_resolver()
    config = resolver.system_dir.parent
    # 开发环境/独立测试没有安装基底，不引入隐式文件复制或真实 config 写入。
    baseline = config / "system-baseline.json"
    if not baseline.is_file():
        yield None
        return
    import json
    identity = json.loads(baseline.read_text(encoding="utf-8"))
    store = UpdateStore(config, identity["app_series"], identity["sha256"])
    if not resolver.is_dev_mode():
        member = store._state().get("member", {})
        if member.get("files"):
            # APK 本轮不运行在线授权链，无会员安装记录时不导入桌面解密依赖。
            from .protected_remote import authorized
        if member.get("files") and not authorized(member):
            # 快照不能让新任务绕过到期/换码；只撤会员，不依赖网络，不在工作线程触发 UI。
            # 本任务从下面的独立目录加载；其他实例/UI 通过安装代次刷新。
            store.install(member_files={}, member_metadata={},
                          activate=lambda *_args: None)
    with tempfile.TemporaryDirectory(prefix="lvjiang-task-config-") as temporary:
        root = Path(temporary)
        with store.locked():
            store._recover()
            shutil.copytree(resolver.system_dir, root / "system")
            (root / "local").mkdir()
            # local 的 scripts/data/telemetry 等不是配置层，不能把诊断与用户数据复制进快照。
            for name in configuration_roots(resolver.system_dir):
                source = resolver.local_dir / name
                if source.is_dir():
                    shutil.copytree(source, root / "local" / name)
                elif source.is_file():
                    shutil.copy2(source, root / "local" / name)
        snapshot = RuntimeSnapshot(
            ConfigResolver(system_dir=root / "system", local_dir=root / "local", dev_mode=False),
            resolver.system_dir, resolver.local_dir)
        token = _current.set(snapshot)
        try:
            yield snapshot
        finally:
            _current.reset(token)


def task_configuration(function):
    """引擎统一执行入口，覆盖 DSL、类任务及无锁 Profile 子任务。"""
    @wraps(function)
    def wrapped(self, source, *args, **kwargs):
        with configuration_snapshot() as snapshot:
            previous = (self._layout, self._reference_recognizer, self._workflow)
            source_layout = getattr(source, "_layout", None)
            try:
                if snapshot is not None:
                    from copy import deepcopy

                    from ..layout_manager import load_layout_by_key
                    if isinstance(source, (str, Path)):
                        source = snapshot.relocate(Path(source))
                    layout_key = getattr(self._layout, "key", "")
                    self._layout = (load_layout_by_key(layout_key) if layout_key else None) or deepcopy(self._layout)
                    self._workflow = None
                    # 引擎可复用；每次执行重建识别器，避免上一次任务持有旧图库元数据。
                    if self._ocr is not None:
                        from ..recognizers import ReferenceRecognizer
                        from ..reference_db import ReferenceDatabase
                        space = previous[1].reference_db.get_active_space()
                        system = snapshot.resolver.system_dir / "references"
                        local = snapshot.resolver.local_dir / "references"
                        database = ReferenceDatabase(
                            system_dir=system / space, local_dir=local / space,
                            system_yaml=system / f"{space}.yaml", local_yaml=local / f"{space}.yaml",
                            system_spaces_dir=system, local_spaces_dir=local)
                        database._space = space
                        self._reference_recognizer = ReferenceRecognizer(self._ocr, database)
                    if not isinstance(source, (str, Path)) and hasattr(source, "_layout"):
                        source._layout = self._layout
                return function(self, source, *args, **kwargs)
            finally:
                if snapshot is not None:
                    self._layout, self._reference_recognizer, self._workflow = previous
                    if not isinstance(source, (str, Path)) and hasattr(source, "_layout"):
                        source._layout = source_layout
    return wrapped
