"""运行任务的配置读取上下文；不改变 UI 的活动配置与编辑器状态。"""
from __future__ import annotations

import shutil
import tempfile
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from functools import wraps
from pathlib import Path
from typing import Any, TypeVar

from loguru import logger

T = TypeVar("T")


@dataclass
class RuntimeSnapshot:
    resolver: Any
    original_system: Path
    original_local: Path
    values: dict[object, Any] = field(default_factory=dict)
    generation: Any = None
    update_at: str = ""

    def relocate(self, path: Path) -> Path:
        for original, frozen in ((self.original_system, self.resolver.system_dir),
                                 (self.original_local, self.resolver.local_dir)):
            try:
                return frozen / path.relative_to(original)
            except ValueError:
                continue
        return path


_current: ContextVar[RuntimeSnapshot | None] = ContextVar("runtime_configuration", default=None)


@dataclass
class _Generation:
    identity: Any
    owner: Any
    system: Path
    local: Path
    values: dict[object, Any] = field(default_factory=dict)
    parse_lock: Any = field(default_factory=threading.RLock)

    def __del__(self):
        shutil.rmtree(self.owner, ignore_errors=True)

    def __deepcopy__(self, memo):
        # 资源代次由引用计数共享，不能复制 TemporaryDirectory 的 finalizer。
        return self


_generations: dict[tuple[Path, Path], _Generation] = {}


def current_snapshot() -> RuntimeSnapshot | None:
    return _current.get()


def configuration_roots(system: Path) -> set[str]:
    """配置读取范围：官方目录与已登记消费者，不包含 local 里的仓库或诊断数据。"""
    from ...apps import load_config_policies
    load_config_policies()
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
        from ..scene_definition import SceneRegistry
        reusable = key is SceneRegistry or (isinstance(key, tuple) and key[0] == "layout")
        if reusable and snapshot.generation is not None:
            from copy import deepcopy
            cached = snapshot.generation.values
            with snapshot.generation.parse_lock:
                if key not in cached:
                    cached[key] = factory()
                snapshot.values[key] = deepcopy(cached[key])
        else:
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
    from .content import configuration_lock, load_content
    from .remote import prepare_task_installation
    from .resolver import ConfigResolver, get_resolver

    active = _current.get()
    if active is not None:
        yield active
        return
    resolver = get_resolver()
    config = resolver.config_dir
    prepare_task_installation()
    with configuration_lock(config):
        content = load_content(resolver)
        # 根目录 YAML 是任务输入，每次真正读盘；不依赖 content_version。
        roots = tuple((layer, path.name, path.read_bytes())
                      for layer, directory in (("system", resolver.system_dir), ("local", resolver.local_dir))
                      if directory.is_dir() for path in sorted(directory.glob("*.yaml")))
        # WF 没有 content_version，保留现有外部脚本编辑的热加载契约；只 stat，
        # 不为判断变化读取脚本内容。导入链也必须固定到本次资源代次。
        scripts = tuple((layer, str(path.relative_to(directory)), path.stat().st_mtime_ns, path.stat().st_size)
                        for layer, directory in (("system", resolver.system_dir), ("local", resolver.local_dir))
                        for path in sorted((directory / "workflows").rglob("*"))
                        if path.is_file() and path.suffix in {".wf", ".lvtrace"})
        cache_key = (resolver.system_dir.resolve(), resolver.local_dir.resolve())
        identity = (content, roots, scripts)
        generation = _generations.get(cache_key)
        reused = generation is not None and generation.identity == identity
        if not reused:
            owner = Path(tempfile.mkdtemp(prefix="lvjiang-task-config-"))
            root = owner
            generation = _Generation(identity, owner, root / "system", root / "local")
            if resolver.system_dir.is_dir():
                shutil.copytree(resolver.system_dir, root / "system")
            else:
                (root / "system").mkdir()
            (root / "local").mkdir()
            for name in configuration_roots(resolver.system_dir):
                source = resolver.local_dir / name
                if source.is_dir():
                    shutil.copytree(source, root / "local" / name)
                elif source.is_file():
                    shutil.copy2(source, root / "local" / name)
            _generations[cache_key] = generation
        assert generation is not None
        logger.debug("任务配置 content.json update_at={}，{}", content["update_at"],
                     "复用解析缓存" if reused else "从磁盘加载新代次")
        snapshot = RuntimeSnapshot(
            ConfigResolver(system_dir=generation.system, local_dir=generation.local,
                           dev_mode=False, track_content=False, config_dir=generation.owner),
            resolver.system_dir, resolver.local_dir,
            generation=generation, update_at=content["update_at"])
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
            if snapshot is not None:
                self.configuration_update_at = snapshot.update_at
                logger.info("任务配置修订时间 content.json update_at={}", snapshot.update_at)
            previous = (self._layout, self._reference_recognizer, self._workflow)
            source_layout = getattr(source, "_layout", None)
            try:
                if snapshot is not None:
                    from copy import deepcopy

                    from ..layout_manager import load_layout_by_key
                    if isinstance(source, (str, Path)):
                        source = snapshot.relocate(Path(source))
                    layout_key = getattr(self._layout, "key", "")
                    if not isinstance(layout_key, str):
                        layout_key = ""
                    loaded = scoped_value(("layout", layout_key), lambda: load_layout_by_key(layout_key)) if layout_key else None
                    self._layout = deepcopy(loaded or self._layout)
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
