"""SessionStore —— session.json 的唯一读写咽喉

纯运行态配置（config/session/session.json）统一经本模块读写：
- 全量内存缓存：首次访问懒加载，此后所有读操作命中内存
- 线程安全：RLock 保护单进程内的并发
- 多进程安全：文件锁（fasteners）保护跨进程的并发写入（自动处理 Windows/Unix）
- 写即落盘：每次变更立即原子落盘（tmp + os.replace），杜绝半截文件
- 写锁最小化：仅在磁盘写入时持有锁（6-14ms），不阻塞读操作

节点语义：session.json 顶层 key 即节点（daily / settings /
actives 等），各调用方只操作自己的节点。

界面、Profile 总览、告警与服务器状态独立存于 interface.json，见 interface 模块。

⚠️ 多进程约束：
   1. 只在写入瞬间申请文件锁（不全程持有）
   2. 文件锁只尝试一次，失败直接交给调用方处理
   3. fasteners 库自动处理跨平台锁定（Windows/Linux/macOS）
"""
from __future__ import annotations

import json
import threading
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable

from fasteners import InterProcessLock
from loguru import logger

from ..fs_util import atomic_write_text

#: session.json 文档格式版本。v2 即 0.12.0 起的结构（users 为用户名列表、
#: 激活项集中在 actives）；没有该字段的文档在首次写入时补上。
SESSION_VERSION = 2

# 合法的激活项 kind。plan 是机器级方案（图库+环境+布局+模式的组合）。
_ACTIVE_KINDS = frozenset({"user", "layout", "space", "plan"})

# 只读实例仅隔离会改变“当前实例正在使用什么”的选择状态。未列出的节点和
# 字段仍按普通 SessionStore 语义持锁、合并并原子落盘。
#
# 每个元组表示一棵完整的临时子树。名单必须保持窄小；新增 Session 字段默认
# 可写，只有确认属于实例私有选择时才允许加入这里。
READONLY_TRANSIENT_PATHS: frozenset[tuple[str, ...]] = frozenset({
    ("actives",),
    ("settings", "env"),
    ("daily", "workflow_id"),
})

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

    多进程安全：
    - 使用文件锁（fasteners）保护写入（跨平台：Windows/Unix/macOS）
    - 锁仅在磁盘I/O时持有（6-14ms）
    """

    FORMAT_VERSION = SESSION_VERSION
    TRANSIENT_PATHS = READONLY_TRANSIENT_PATHS

    LOCK_TIMEOUT = 5  # 文件锁超时秒数

    def __init__(self, path: Path | str | None = None):
        self._path_override = Path(path) if path else None
        self._thread_lock = threading.RLock()  # 单进程内线程安全
        self._data: dict = self._read_disk()  # 构造时立即加载
        # fasteners 跨平台文件锁（自动处理 Windows/Unix 差异）
        self._file_lock = InterProcessLock(str(self.path) + ".lock")

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

    def _acquire_write_lock(self, timeout: float = LOCK_TIMEOUT) -> None:
        """【关键】获取写锁（跨平台），超时则抛异常

        fasteners 自动处理 Windows/Unix 差异
        """
        acquired = self._file_lock.acquire(blocking=True, timeout=timeout)
        if not acquired:
            raise TimeoutError(f"无法在 {timeout}s 内获取 {self.path.name} 写锁")

    def _write_disk_atomic(self, data: dict) -> None:
        """【关键】原子写入磁盘（必须在持有锁的情况下调用）

        tmp 文件 + os.replace，确保不会产生半截文件
        """
        text = json.dumps(data, ensure_ascii=False, indent=2)
        atomic_write_text(self.path, text, prefix=f".{self.path.stem}_")

    def _mutate_disk(
        self, mutator: Callable[[dict], Any], *, stamp_version: bool = True, strict: bool = False,
    ) -> Any:
        """在文件锁内对最新磁盘快照执行变更并原子落盘。

        - 线程锁串行化同一进程内对 fasteners 锁实例的访问
        - 文件锁内重新读取磁盘，避免其他进程的同节点更新被陈旧缓存覆盖
        - 使用 fasteners 确保 Windows/Unix 兼容
        """
        with self._thread_lock:
            from ..access import is_readonly
            readonly = is_readonly()
            self._acquire_write_lock(self.LOCK_TIMEOUT)
            try:
                disk_data = self._read_disk(strict=strict)
                working = deepcopy(disk_data)
                if stamp_version:
                    working.setdefault("version", self.FORMAT_VERSION)
                if readonly:
                    _overlay_readonly_transients(working, self._data, self.TRANSIENT_PATHS)
                result = mutator(working)

                persisted = deepcopy(working)
                if readonly:
                    _overlay_readonly_transients(persisted, disk_data, self.TRANSIENT_PATHS)
                if persisted != disk_data:
                    self._write_disk_atomic(persisted)

                self._data = deepcopy(persisted)
                if readonly:
                    _overlay_readonly_transients(self._data, working, self.TRANSIENT_PATHS)
                return result
            finally:
                self._file_lock.release()

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
        使用文件锁确保多进程安全。
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
        使用文件锁确保多进程安全。
        """
        def _mutate(data: dict) -> Any:
            new_value = fn(data.get(key))
            data[key] = new_value
            return new_value

        return self._mutate_disk(_mutate)

    def delete_node(self, key: str):
        """删除顶层节点并落盘（不存在时静默）

        使用文件锁确保多进程安全。
        """
        def _delete(data: dict) -> None:
            data.pop(key, None)

        self._mutate_disk(_delete)

    def consume_node(self, key: str, consumer: Callable[[Any], None]) -> bool:
        """持锁消费并删除一个顶层节点，供一次性外迁使用。

        ``consumer`` 在最新磁盘快照的写锁内执行。只有 consumer 成功返回后
        才会删除节点并落盘；consumer 抛出异常时原节点保持不变。回调不得
        再调用本 SessionStore，避免锁重入跨进程文件锁。

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

    # ─── 激活项（actives）─────────────────────────────────

    def get_active(self, kind: str, default: Any = None) -> Any:
        """读取 ``actives.<kind>``。"""
        if kind not in _ACTIVE_KINDS:
            raise KeyError(f"unknown active kind: {kind}")
        with self._thread_lock:
            actives = self._data.get("actives")
            if isinstance(actives, dict) and kind in actives:
                return deepcopy(actives[kind])
            return default

    def set_active(self, kind: str, value: Any) -> None:
        """写入一个激活项。"""
        if kind not in _ACTIVE_KINDS:
            raise KeyError(f"unknown active kind: {kind}")

        def _set(data: dict) -> None:
            current = data.get("actives")
            actives = deepcopy(current) if isinstance(current, dict) else {}
            actives[kind] = deepcopy(value)
            data["actives"] = actives

        self._mutate_disk(_set)

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


# ─── 便捷函数：settings / reference_grid ───────────────────

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


def load_reference_grid() -> dict[str, Any]:
    """读取 settings.reference_grid。"""
    value = load_settings().get("reference_grid")
    return value if isinstance(value, dict) else {}


def save_reference_grid(grid: dict[str, Any]) -> None:
    """保存参考图网格。

    ⚠️ 使用 mutate_node 确保并发安全，禁止直接 load+save 模式
    """
    def _merge(existing):
        existing = existing if isinstance(existing, dict) else {}
        existing["reference_grid"] = grid
        return existing

    get_session_store().mutate_node("settings", _merge)


# ─── 便捷函数：env 工作环境 ──────────────────────────────────


def load_env() -> str:
    """读取当前工作环境（session.json 的 settings.env 节点）

    无配置时取 app.yaml envs 列表第一项，保证与默认连接方案对应。
    """
    value = load_settings().get("env")
    if isinstance(value, str) and value:
        return value
    from .resolver import load_available_envs
    envs = load_available_envs()
    return envs[0][0] if envs else "desktop"


def save_env(env: str) -> None:
    """保存工作环境到 session.json 的 settings.env 节点

    ⚠️ 走 update_node 浅合并，禁止 load+set_node：后者会把读盘到写盘之间
    别的组件写进 settings 的内容整体覆盖掉。
    """
    get_session_store().update_node("settings", {"env": env})
