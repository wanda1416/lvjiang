"""SessionStore —— session.json 的唯一读写咽喉

纯运行态配置（config/session/session.json）统一经本模块读写：
- 全量内存缓存：首次访问懒加载，此后所有读操作命中内存
- 线程安全：RLock 保护单进程内的并发
- 写入所有权：共享配置只有主节点可以落盘，只读选择留在内存
- 写即落盘：每次变更立即原子落盘（tmp + os.replace），杜绝半截文件

节点语义：session.json 顶层 key 即节点（daily / settings 等），
各调用方只操作自己的节点。

界面、Profile 总览、告警与服务器状态独立存于 interface.json，见 interface 模块。

不为状态文件创建跨进程锁；主节点身份与用户任务占用由 core.access 管理。
"""
from __future__ import annotations

import json
import threading
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable

from loguru import logger

from ..fs_util import atomic_write_text

#: session.json 文档格式版本。v3 起 session 只放配置与事实：当前选择（用户、
#: 布局、图库空间、工作环境、日常脚本、连接方案）已移到 interface.json 的
#: actives，统计同意记录归入 settings.network；没有该字段的文档首次写入时补上。
SESSION_VERSION = 3

# 只读节点只能在内存中改允许的临时子树，其它节点拒绝落盘。session.json 只剩配置
# 与事实，没有实例私有选择，所以这里是空的——那类状态（actives）已随新归属
# 落到 InterfaceStore.TRANSIENT_PATHS。
READONLY_TRANSIENT_PATHS: frozenset[tuple[str, ...]] = frozenset()

_MISSING = object()


def _value_at_path(data: dict, path: tuple[str, ...]) -> Any:
    value: Any = data
    for key in path:
        if not isinstance(value, dict) or key not in value:
            return _MISSING
        value = value[key]
    return value


def _overlay_path(target: dict, source: dict, path: tuple[str, ...]) -> None:
    """Copy one transient subtree from source to target, including deletion."""
    source_value = _value_at_path(source, path)
    parent: dict = target
    for key in path[:-1]:
        child = parent.get(key)
        if not isinstance(child, dict):
            if source_value is _MISSING:
                return
            child = {}
            parent[key] = child
        parent = child
    leaf = path[-1]
    if source_value is _MISSING:
        parent.pop(leaf, None)
    else:
        parent[leaf] = deepcopy(source_value)


def _overlay_readonly_transients(
    target: dict, source: dict, paths=READONLY_TRANSIENT_PATHS,
) -> None:
    for path in paths:
        _overlay_path(target, source, path)


class SessionStore:
    """session.json 唯一读写入口

    不带参构造时路径动态取自 constants.SESSION_PATH（monkeypatch 友好）；
    测试可显式传入 tmp_path 构造隔离实例。

    只有主节点落盘；进程内读改写使用 RLock，文件使用原子替换。
    只读节点允许的临时选择仅更新本实例缓存。
    """

    FORMAT_VERSION = SESSION_VERSION
    TRANSIENT_PATHS = READONLY_TRANSIENT_PATHS

    def __init__(self, path: Path | str | None = None):
        self._path_override = Path(path) if path else None
        self._thread_lock = threading.RLock()  # 单进程内线程安全
        self._data: dict = self._read_disk()  # 构造时立即加载

    # ─── 路径与加载 ──────────────────────────────────────

    @property
    def path(self) -> Path:
        if self._path_override is not None:
            return self._path_override
        from ... import constants
        return constants.SESSION_PATH

    def _read_disk(self, *, strict: bool = False) -> dict:
        path = self.path
        if not path.exists():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if strict and not isinstance(data, dict):
                raise ValueError(f"{path.name} 顶层必须为对象")
            return data if isinstance(data, dict) else {}
        except Exception as e:  # noqa: BLE001 损坏文件不应阻断启动
            if strict:
                raise
            logger.error(f"{path.name} 解析失败，按空配置处理: {path}: {e}")
            return {}

    def _write_disk_atomic(self, data: dict) -> None:
        """主节点原子写入磁盘。

        tmp 文件 + os.replace，确保不会产生半截文件
        """
        from ..access import require_primary_write
        require_primary_write()
        text = json.dumps(data, ensure_ascii=False, indent=2)
        atomic_write_text(self.path, text, prefix=f".{self.path.stem}_")

    def _mutate_disk(
        self, mutator: Callable[[dict], Any], *, stamp_version: bool = True, strict: bool = False,
    ) -> Any:
        """主节点合并最新磁盘状态；只读节点只保留允许的临时选择。"""
        with self._thread_lock:
            from ..access import is_readonly, require_primary_write
            readonly = is_readonly()
            disk_data = self._read_disk(strict=strict)
            working = deepcopy(disk_data)
            if stamp_version and not readonly:
                working.setdefault("version", self.FORMAT_VERSION)
            if readonly:
                _overlay_readonly_transients(working, self._data, self.TRANSIENT_PATHS)
            result = mutator(working)
            persisted = deepcopy(working)
            if readonly:
                _overlay_readonly_transients(persisted, disk_data, self.TRANSIENT_PATHS)
            if persisted != disk_data:
                require_primary_write()
                self._write_disk_atomic(persisted)
            self._data = deepcopy(persisted)
            if readonly:
                _overlay_readonly_transients(self._data, working, self.TRANSIENT_PATHS)
            return result

    # ─── 节点读写 ────────────────────────────────────────

    def get_node(self, key: str, default: Any = None) -> Any:
        """读顶层节点（返回深拷贝，调用方改不坏内部态）"""
        with self._thread_lock:
            value = self._data.get(key)
            return deepcopy(value) if value is not None else default

    def set_node(self, key: str, value: Any):
        """整节点替换并落盘。"""
        def _set(data: dict) -> None:
            data[key] = deepcopy(value)

        self._mutate_disk(_set)

    def update_node(self, key: str, patch: dict):
        """dict 节点一级浅合并并落盘（多组件分写同一节点用）

        节点缺失或不是 dict 时视为空 dict 重建。
        主节点在进程内互斥区读取最新磁盘值后原子写回。
        """
        def _update(data: dict) -> None:
            node = data.get(key)
            node = node if isinstance(node, dict) else {}
            node.update(patch)
            data[key] = node

        self._mutate_disk(_update)

    def mutate_node(self, key: str, fn: Callable[[Any], Any]) -> Any:
        """锁内原子读-改-写：fn(旧值) 的返回值作为新节点并落盘

        返回写入的新值。供需要 get+set 原子性的调用方（如插件节点）。
        主节点在进程内互斥区读取最新磁盘值后原子写回。
        """
        def _mutate(data: dict) -> Any:
            new_value = fn(data.get(key))
            data[key] = new_value
            return new_value

        return self._mutate_disk(_mutate)

    def delete_node(self, key: str):
        """删除顶层节点并落盘（不存在时静默）

        主节点在进程内互斥区读取最新磁盘值后原子写回。
        """
        def _delete(data: dict) -> None:
            data.pop(key, None)

        self._mutate_disk(_delete)

    def consume_node(self, key: str, consumer: Callable[[Any], None]) -> bool:
        """持锁消费并删除一个顶层节点，供一次性外迁使用。

        ``consumer`` 在最新磁盘快照的写锁内执行。只有 consumer 成功返回后
        才会删除节点并落盘；consumer 抛出异常时原节点保持不变。回调不得
        再修改本 SessionStore，避免回调结果被外层快照覆盖。

        返回 True 表示节点存在且已消费，False 表示节点原本不存在。
        """
        consumed = False

        def _consume(data: dict) -> None:
            nonlocal consumed
            if key not in data:
                return
            consumer(deepcopy(data[key]))
            data.pop(key)
            consumed = True

        self._mutate_disk(_consume, stamp_version=False)
        return consumed

    def reload(self):
        """重新读盘；只读实例保留白名单中的本实例临时选择。"""
        with self._thread_lock:
            from ..access import is_readonly
            disk_data = self._read_disk()
            if is_readonly():
                _overlay_readonly_transients(disk_data, self._data, self.TRANSIENT_PATHS)
            self._data = disk_data


# ─── 模块级单例 ──────────────────────────────────────────

_store: SessionStore | None = None


def get_session_store() -> SessionStore:
    global _store
    if _store is None:
        _store = SessionStore()
    return _store


def reset_session_store() -> None:
    """丢弃模块级单例（测试用：monkeypatch SESSION_PATH 后避免内存态跨用例残留）"""
    global _store
    _store = None
    from .interface import reset_interface_store
    reset_interface_store()


# ─── 便捷函数：settings ───────────────────

def load_settings() -> dict[str, Any]:
    """读取 session.json 的 settings 节点"""
    value = get_session_store().get_node("settings")
    return value if isinstance(value, dict) else {}


def save_settings(settings: dict[str, Any]) -> None:
    """保存配置到 session.json 的 settings 节点（保留子节点，原子操作）

    ⚠️ 使用 mutate_node 确保并发安全，禁止直接 load+save 模式
    """
    def _merge(existing):
        existing = existing if isinstance(existing, dict) else {}
        # 合并新设置
        merged = {**existing, **settings}
        return merged

    get_session_store().mutate_node("settings", _merge)


# ─── 便捷函数：env 工作环境 ──────────────────────────────────


def load_env() -> str:
    """读取当前工作环境（interface.json 的 actives.env）

    它是「当前在用哪个环境」的选择，丢了重选一次即可，所以和界面状态一起
    存在 interface.json。无记录时取 app.yaml envs 列表第一项，保证与默认
    连接方案对应。
    """
    from .interface import get_interface_store
    value = get_interface_store().get_active("env", "")
    if isinstance(value, str) and value:
        return value
    from .resolver import load_available_envs
    envs = load_available_envs()
    return envs[0][0] if envs else "desktop"


def save_env(env: str) -> None:
    """把工作环境记进 interface.json 的 actives.env。"""
    from .interface import get_interface_store
    get_interface_store().set_active("env", env)
