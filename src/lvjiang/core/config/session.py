"""SessionStore —— session.json 的唯一读写咽喉

纯运行态配置（config/session/session.json）统一经本模块读写：
- 全量内存缓存：首次访问懒加载，此后所有读操作命中内存
- 线程安全：RLock 保护全部读写，UI 主线程与工作流后台线程并发安全
- 写即落盘：每次变更立即原子落盘（tmp + os.replace），杜绝半截文件
- 单写者快照：所有写入都基于同一份内存态 read-modify-write，
  根除多入口各自读盘再写回造成的相互覆盖

节点语义：session.json 顶层 key 即节点（ui_state / daily / settings /
active_layout / active_space 等），各调用方只操作自己的节点。

ui_state 的页面子节点禁止直接通过 update_node 嵌套写入；该方法只做
顶层浅合并，会整体覆盖同名页面。页面状态统一使用
load_ui_page_state / update_ui_page_state。
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable

from loguru import logger


class SessionStore:
    """session.json 唯一读写入口

    不带参构造时路径动态取自 constants.SESSION_PATH（monkeypatch 友好）；
    测试可显式传入 tmp_path 构造隔离实例。
    """

    def __init__(self, path: Path | str | None = None):
        self._path_override = Path(path) if path else None
        self._lock = threading.RLock()
        self._data: dict = self._read_disk()  # 构造时立即加载

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

    def _flush(self):
        """原子落盘：tmp 文件 + os.replace（调用方须已持锁）

        Windows 下目标文件被锁定时会重试 3 次，仍失败则降级为直接写入。
        """
        path = self.path
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(
                dir=str(path.parent), prefix=".session_", suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(self._data, f, ensure_ascii=False, indent=2)
                # Windows 下文件被锁定时重试 3 次
                import time
                for attempt in range(3):
                    try:
                        os.replace(tmp, path)
                        break
                    except PermissionError:
                        if attempt < 2:
                            time.sleep(0.1)
                        else:
                            # 重试失败，降级为直接写入
                            logger.warning(f"原子写入失败，降级为直接写入: {path}")
                            with open(path, "w", encoding="utf-8") as f:
                                json.dump(self._data, f, ensure_ascii=False, indent=2)
                            try:
                                os.unlink(tmp)
                            except OSError:
                                pass
            except BaseException:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise
        except Exception as e:  # noqa: BLE001 落盘失败仅告警，不阻断业务
            logger.warning(f"session.json 落盘失败: {path}: {e}")

    # ─── 节点读写 ────────────────────────────────────────

    def get_node(self, key: str, default: Any = None) -> Any:
        """读顶层节点（返回深拷贝，调用方改不坏内部态）"""
        with self._lock:
            value = self._data.get(key)
            return deepcopy(value) if value is not None else default

    def set_node(self, key: str, value: Any):
        """整节点替换并落盘"""
        with self._lock:
            self._data[key] = value
            self._flush()

    def update_node(self, key: str, patch: dict):
        """dict 节点一级浅合并并落盘（多组件分写同一节点用）

        节点缺失或不是 dict 时视为空 dict 重建。
        """
        with self._lock:
            node = self._data.get(key)
            node = node if isinstance(node, dict) else {}
            node.update(patch)
            self._data[key] = node
            self._flush()

    def mutate_node(self, key: str, fn: Callable[[Any], Any]) -> Any:
        """锁内原子读-改-写：fn(旧值) 的返回值作为新节点并落盘

        返回写入的新值。供需要 get+set 原子性的调用方（如插件节点）。
        """
        with self._lock:
            new_value = fn(self._data.get(key))
            self._data[key] = new_value
            self._flush()
            return new_value

    def delete_node(self, key: str):
        """删除顶层节点并落盘（不存在时静默）"""
        with self._lock:
            if key in self._data:
                del self._data[key]
                self._flush()

    def reload(self):
        """重新读盘，刷新内存态"""
        with self._lock:
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
    """保存配置到 session.json 的 settings 节点（保留 material_grid 子节点）"""
    existing = load_settings()
    material_grid = existing.get("material_grid")
    merged = {**existing, **settings}
    if material_grid is not None:
        merged["material_grid"] = material_grid
    get_session_store().set_node("settings", merged)


def load_material_grid() -> dict[str, Any]:
    """读取 session.json 的 settings.material_grid 节点"""
    value = load_settings().get("material_grid")
    return value if isinstance(value, dict) else {}


def save_material_grid(grid: dict[str, Any]) -> None:
    """保存材料网格参数到 session.json 的 settings.material_grid 节点（保留其他 settings 字段）"""
    existing = load_settings()
    existing["material_grid"] = grid
    get_session_store().set_node("settings", existing)


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
    """保存装备展示参数到 session.json 的 settings.equip_display 节点"""
    existing = load_settings()
    existing["equip_display"] = params
    get_session_store().set_node("settings", existing)


# ─── 装备筛选配置 ────────────────────────────────────────────

_EQUIP_FILTER_DEFAULTS: dict[str, str] = {
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
    """保存装备筛选配置到 session.json 的 settings.equip_filter 节点"""
    existing = load_settings()
    existing["equip_filter"] = filters
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
