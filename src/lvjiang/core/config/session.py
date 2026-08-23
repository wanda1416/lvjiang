"""SessionStore —— session.json 的唯一读写咽喉

纯运行态配置（config/session/session.json）统一经本模块读写：
- 全量内存缓存：首次访问懒加载，此后所有读操作命中内存
- 线程安全：RLock 保护单进程内的并发
- 多进程安全：文件锁（fasteners）保护跨进程的并发写入（自动处理 Windows/Unix）
- 写即落盘：每次变更立即原子落盘（tmp + os.replace），杜绝半截文件
- 写锁最小化：仅在磁盘写入时持有锁（6-14ms），不阻塞读操作
- 失败重试：写入失败时询问用户是否重试（最多3次）

节点语义：session.json 顶层 key 即节点（ui_state / daily / settings /
active_layout / active_space 等），各调用方只操作自己的节点。

ui_state 的页面子节点禁止直接通过 update_node 嵌套写入；该方法只做
顶层浅合并，会整体覆盖同名页面。页面状态统一使用
load_ui_page_state / update_ui_page_state。

⚠️ 多进程约束：
   1. 只在写入瞬间申请文件锁（不全程持有）
   2. 锁超时时询问用户是否重试
   3. 所有写入都支持 3 次重试机制
   4. 设置 UI 回调 set_ui_callback() 以显示失败提示
   5. fasteners 库自动处理跨平台锁定（Windows/Linux/macOS）
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable

from fasteners import InterProcessLock
from loguru import logger


class LockTimeoutError(Exception):
    """文件锁获取超时"""
    pass


class SessionStore:
    """session.json 唯一读写入口

    不带参构造时路径动态取自 constants.SESSION_PATH（monkeypatch 友好）；
    测试可显式传入 tmp_path 构造隔离实例。

    多进程安全：
    - 使用文件锁（fasteners）保护写入（跨平台：Windows/Unix/macOS）
    - 锁仅在磁盘I/O时持有（6-14ms）
    - 写入失败时询问用户重试
    """

    LOCK_TIMEOUT = 5  # 文件锁超时秒数
    MAX_RETRIES = 3   # 最大重试次数

    def __init__(self, path: Path | str | None = None):
        self._path_override = Path(path) if path else None
        self._thread_lock = threading.RLock()  # 单进程内线程安全
        self._data: dict = self._read_disk()  # 构造时立即加载
        self._ui_callback: Callable | None = None  # UI反馈回调
        # fasteners 跨平台文件锁（自动处理 Windows/Unix 差异）
        self._file_lock = InterProcessLock(str(self.path) + ".lock")

    def set_ui_callback(self, callback: Callable) -> None:
        """设置 UI 回调，用于用户交互（失败提示/重试确认）

        callback(action: str, *args) -> bool
            action="confirm": 返回 True 表示用户选择重试
        """
        self._ui_callback = callback

    # ─── 路径与加载 ──────────────────────────────────────

    @property
    def path(self) -> Path:
        if self._path_override is not None:
            return self._path_override
        from ... import constants
        return constants.SESSION_PATH

    def _read_disk(self) -> dict:
        path = self.path
        if not path.exists():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception as e:  # noqa: BLE001 损坏文件不应阻断启动
            logger.error(f"session.json 解析失败，按空配置处理: {path}: {e}")
            return {}

    def _acquire_write_lock(self, timeout: float = LOCK_TIMEOUT) -> Any:
        """【关键】获取写锁（跨平台），超时则抛异常

        fasteners 自动处理 Windows/Unix 差异
        """
        try:
            # 尝试获取写锁（带超时）
            acquired = self._file_lock.acquire(blocking=True, timeout=timeout)
            if not acquired:
                raise LockTimeoutError(
                    f"无法在 {timeout}s 内获取写锁（另一个进程在写入）"
                )
            return self._file_lock
        except Exception as e:
            if isinstance(e, LockTimeoutError):
                raise
            raise LockTimeoutError(f"获取文件锁失败: {e}") from e

    def _write_disk_atomic(self, data: dict) -> None:
        """【关键】原子写入磁盘（必须在持有锁的情况下调用）

        tmp 文件 + os.replace，确保不会产生半截文件
        """
        path = self.path
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(
                dir=str(path.parent), prefix=".session_", suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                os.replace(tmp, path)
            except BaseException:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise
        except Exception as e:
            raise IOError(f"写入磁盘失败: {e}") from e

    def _ask_user_retry(self, title: str, message: str) -> bool:
        """询问用户是否重试写入

        返回 True: 用户选择重试
        返回 False: 用户选择取消或无 UI
        """
        if self._ui_callback:
            try:
                return bool(self._ui_callback("confirm", title, message))
            except Exception as e:
                logger.warning(f"UI 回调异常: {e}")
                return False
        else:
            logger.warning(f"{title}\n{message}")
            return False

    def _mutate_disk_with_retry(
        self, mutator: Callable[[dict], Any]
    ) -> Any:
        """在文件锁内对最新磁盘快照执行变更并原子落盘。

        - 线程锁串行化同一进程内对 fasteners 锁实例的访问
        - 文件锁内重新读取磁盘，避免其他进程的同节点更新被陈旧缓存覆盖
        - 失败时询问用户重试（最多3次）
        - 使用 fasteners 确保 Windows/Unix 兼容
        """
        with self._thread_lock:
            for attempt in range(self.MAX_RETRIES):
                try:
                    # 步骤 1: 获取写锁（短暂）
                    self._acquire_write_lock(self.LOCK_TIMEOUT)

                    try:
                        # 步骤 2: 持有锁期间进行操作
                        #   - 再次读磁盘（防止被其他进程修改后的中间状态）
                        #   - 对最新快照应用本次修改
                        #   - 写入磁盘（原子操作）
                        disk_data = self._read_disk()
                        result = mutator(disk_data)
                        self._write_disk_atomic(disk_data)
                        self._data = disk_data
                    finally:
                        # 步骤 3: 释放锁
                        try:
                            self._file_lock.release()
                        except Exception:  # noqa: BLE001 清理失败不遮蔽原异常
                            pass
                    return result

                except LockTimeoutError as e:
                    # 锁超时 → 询问用户
                    if attempt < self.MAX_RETRIES - 1:
                        if self._ask_user_retry(
                            "写入超时",
                            f"session.json 写入超时（第 {attempt+1} 次尝试失败）。\n"
                            f"另一个进程正在修改数据。是否重试？\n"
                            f"错误: {e}"
                        ):
                            time.sleep(0.5 * (attempt + 1))  # 退避
                            continue
                        raise IOError("用户取消写入") from e
                    else:
                        raise IOError(
                            f"多次写入失败（已尝试 {self.MAX_RETRIES} 次）。\n"
                            f"请检查：\n"
                            f"  1. 磁盘空间是否充足\n"
                            f"  2. 文件权限是否正确\n"
                            f"  3. 是否有其他进程长期锁定文件"
                        ) from e

                except IOError as e:
                    logger.error(f"写入 session.json 失败: {e}")
                    raise

    # ─── 节点读写 ────────────────────────────────────────

    def get_node(self, key: str, default: Any = None) -> Any:
        """读顶层节点（返回深拷贝，调用方改不坏内部态）"""
        with self._thread_lock:
            value = self._data.get(key)
            return deepcopy(value) if value is not None else default

    def set_node(self, key: str, value: Any):
        """整节点替换并落盘（带多进程文件锁 + 重试）"""
        def _set(data: dict) -> None:
            data[key] = deepcopy(value)

        self._mutate_disk_with_retry(_set)

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

        self._mutate_disk_with_retry(_update)

    def mutate_node(self, key: str, fn: Callable[[Any], Any]) -> Any:
        """锁内原子读-改-写：fn(旧值) 的返回值作为新节点并落盘

        返回写入的新值。供需要 get+set 原子性的调用方（如插件节点）。
        使用文件锁确保多进程安全。
        """
        def _mutate(data: dict) -> Any:
            new_value = fn(data.get(key))
            data[key] = new_value
            return new_value

        return self._mutate_disk_with_retry(_mutate)

    def delete_node(self, key: str):
        """删除顶层节点并落盘（不存在时静默）

        使用文件锁确保多进程安全。
        """
        def _delete(data: dict) -> None:
            data.pop(key, None)

        self._mutate_disk_with_retry(_delete)

    def reload(self):
        """重新读盘，刷新内存态"""
        with self._thread_lock:
            self._data = self._read_disk()


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


# ─── UI 页面状态安全入口 ──────────────────────────────────

def load_ui_page_state(page_key: str) -> dict[str, Any]:
    """读取 ``ui_state.<page_key>``，非法或缺失时返回空字典。"""
    state = get_session_store().get_node("ui_state", {})
    if not isinstance(state, dict):
        return {}
    page = state.get(page_key)
    return page if isinstance(page, dict) else {}


def update_ui_page_state(page_key: str, patch: dict[str, Any]) -> dict:
    """原子浅合并 ``ui_state.<page_key>``，保留该页面其他字段。

    这是页面级 UI 状态的唯一写入口。不得写成
    ``update_node("ui_state", {page_key: patch})``，后者会整体替换页面，
    例如保存页签索引时删除窗口大小。
    """
    if not isinstance(page_key, str) or not page_key:
        raise ValueError("page_key 必须是非空字符串")
    if not isinstance(patch, dict):
        raise TypeError("patch 必须是 dict")

    def _merge(old):
        state = dict(old) if isinstance(old, dict) else {}
        page = state.get(page_key)
        page = dict(page) if isinstance(page, dict) else {}
        page.update(patch)
        state[page_key] = page
        return state

    return get_session_store().mutate_node("ui_state", _merge)


# ─── 便捷函数：settings / material_grid ────────────────────

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


def load_material_grid() -> dict[str, Any]:
    """读取 session.json 的 settings.material_grid 节点"""
    value = load_settings().get("material_grid")
    return value if isinstance(value, dict) else {}


def save_material_grid(grid: dict[str, Any]) -> None:
    """保存材料网格参数到 session.json 的 settings.material_grid 节点（原子操作）

    ⚠️ 使用 mutate_node 确保并发安全，禁止直接 load+save 模式
    """
    def _merge(existing):
        existing = existing if isinstance(existing, dict) else {}
        existing["material_grid"] = grid
        return existing

    get_session_store().mutate_node("settings", _merge)


# ─── 便捷函数：equip_display 装备展示参数 ────────────────────

_EQUIP_DISPLAY_DEFAULTS: dict[str, Any] = {
    "name_font_size": 13,
    "level_font_size": 12,
    "affix_font_size": 11,
    "card_min_height": 180,
    "grid_columns": 4,
}


def load_equip_display() -> dict[str, Any]:
    """读取 session.json 的 settings.equip_display 节点，缺失字段用默认值补齐"""
    value = load_settings().get("equip_display")
    if not isinstance(value, dict):
        return dict(_EQUIP_DISPLAY_DEFAULTS)
    merged = dict(_EQUIP_DISPLAY_DEFAULTS)
    merged.update(value)
    return merged


def save_equip_display(params: dict[str, Any]) -> None:
    """保存装备展示参数到 session.json 的 settings.equip_display 节点（原子操作）

    ⚠️ 使用 mutate_node 确保并发安全，禁止直接 load+save 模式
    """
    def _merge(existing):
        existing = existing if isinstance(existing, dict) else {}
        existing["equip_display"] = params
        return existing

    get_session_store().mutate_node("settings", _merge)


# ─── 装备筛选配置 ────────────────────────────────────────────

_EQUIP_FILTER_DEFAULTS: dict[str, str] = {
    "type": "all",        # all | main_weapon | sub_weapon | ring | pendant | head | chest | leg | wrist
    "level": "all",       # all | 110 | 105
    "affix": "all",       # all | dingyin | full_tuning
}


def load_equip_filter() -> dict[str, str]:
    """读取 session.json 的 settings.equip_filter 节点，缺失字段用默认值补齐"""
    value = load_settings().get("equip_filter")
    if not isinstance(value, dict):
        return dict(_EQUIP_FILTER_DEFAULTS)
    merged = dict(_EQUIP_FILTER_DEFAULTS)
    merged.update(value)
    return merged


def save_equip_filter(filters: dict[str, str]) -> None:
    """保存装备筛选配置到 session.json 的 settings.equip_filter 节点（原子操作）

    ⚠️ 使用 mutate_node 确保并发安全，禁止直接 load+save 模式
    """
    def _merge(existing):
        existing = existing if isinstance(existing, dict) else {}
        existing["equip_filter"] = filters
        return existing

    get_session_store().mutate_node("settings", _merge)


# ─── 便捷函数：env 工作环境 ──────────────────────────────────


def load_env() -> str:
    """读取当前工作环境（session.json 的 settings.env 节点）

    无配置时取 app.yaml envs 列表第一项，保证与默认布局对应。
    """
    value = load_settings().get("env")
    if isinstance(value, str) and value:
        return value
    from .resolver import load_available_envs
    envs = load_available_envs()
    return envs[0][0] if envs else "desktop"


def save_env(env: str) -> None:
    """保存工作环境到 session.json 的 settings.env 节点"""
    existing = load_settings()
    existing["env"] = env
    get_session_store().set_node("settings", existing)


# ─── 便捷函数：alert_info 告警存储 ────────────────────────────


def get_alerts() -> list[dict[str, Any]]:
    """读取 session.json 的 alert_info 节点（告警列表，最新在前）"""
    value = get_session_store().get_node("alert_info")
    return value if isinstance(value, list) else []


def add_alert(alert_id: str, message: str, timestamp: str) -> bool:
    """追加告警到栈顶（列表头部），最新优先展示。返回 True 表示新增成功，False 表示已存在"""
    added = False

    def _mutate(current):
        nonlocal added
        alerts = current if isinstance(current, list) else []
        # 去重：同 ID 告警不重复添加
        for alert in alerts:
            if alert.get("id") == alert_id:
                return alerts
        new_alert = {"id": alert_id, "message": message, "timestamp": timestamp}
        added = True
        return ([new_alert] + alerts)[:200]  # 插入到头部，截断防无限膨胀

    get_session_store().mutate_node("alert_info", _mutate)
    return added


def dismiss_alert(alert_id: str) -> None:
    """移除指定 ID 的告警"""
    def _mutate(current):
        alerts = current if isinstance(current, list) else []
        return [a for a in alerts if a.get("id") != alert_id]
    get_session_store().mutate_node("alert_info", _mutate)
