"""用户 Session 持久化管理器

负责从 users/{username}.session.json 加载/保存 session 数据。
UI 层在 Engine 创建后注入 session，并在正常结束时调用 save。
"""

import json
import threading
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
from typing import Callable

from fasteners import InterProcessLock
from loguru import logger

from ..fs_util import atomic_write_text


class SessionConflictError(RuntimeError):
    """The persisted value changed since this session was loaded."""


class SessionSnapshot(dict):
    """A JSON-compatible session carrying its own optimistic baseline."""

    def __init__(self, data, users_dir: Path | None = None):
        super().__init__(data)
        self.baseline = deepcopy(data)
        self.users_dir = users_dir


_MISSING = object()
_SAVE_LOCK = threading.RLock()


@contextmanager
def _locked_file(path: Path):
    lock = InterProcessLock(str(path) + ".lock")
    if not lock.acquire(blocking=True, timeout=5):
        raise TimeoutError(f"用户 Session 写入锁超时: {path.name}")
    try:
        yield
    finally:
        lock.release()


def _merge_changes(base: dict, current: dict, disk: dict, path: str = "") -> dict:
    result = deepcopy(disk)
    for key, value in current.items():
        before = base.get(key, _MISSING)
        latest = disk.get(key, _MISSING)
        field = f"{path}.{key}" if path else key
        if before == value:
            continue
        if isinstance(value, dict) and isinstance(before, dict) and isinstance(latest, dict):
            result[key] = _merge_changes(before, value, latest, field)
        elif isinstance(value, dict) and before is _MISSING and isinstance(latest, dict):
            result[key] = _merge_changes({}, value, latest, field)
        elif latest == before or latest == value:
            result[key] = deepcopy(value)
        else:
            raise SessionConflictError(f"Session 保存冲突: {field}")
    # Missing keys are not implicit deletes. Explicit deletion uses update().
    return result


class SessionManager:
    """Session 持久化管理器"""

    def __init__(self, users_dir: Path | None = None):
        if users_dir is None:
            from ...constants import USERS_DIR
            users_dir = USERS_DIR
        self._users_dir = users_dir
        self._users_dir.mkdir(parents=True, exist_ok=True)

    def _default_session(self, username: str) -> dict:
        return {"current_user": username}

    @staticmethod
    def _validate_username(username: str) -> None:
        from ..user_config import is_valid_username

        if not is_valid_username(username):
            raise ValueError(f"非法用户名: {username!r}")

    def _load(self, username: str, path: Path) -> dict:
        if not path.exists():
            return self._default_session(username)
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("用户 Session 必须是 JSON 对象")
        logger.debug(f"已加载 session: {username} ({len(data)} 个字段)")
        return data

    def _save(self, path: Path, session: dict) -> None:
        payload = json.dumps(session, ensure_ascii=False, indent=2)
        atomic_write_text(path, payload, prefix=f".{path.stem}_")

    def load(self, username: str) -> dict:
        """从 users/{username}.json 加载 session

        Args:
            username: 用户名

        Returns:
            dict: session 数据（至少包含 current_user 字段）
        """
        self._validate_username(username)
        path = self._users_dir / f"{username}.session.json"
        try:
            return SessionSnapshot(self._load(username, path), self._users_dir)
        except Exception as e:
            logger.error(f"加载 session 失败: {e}")
        # 默认 session
        return SessionSnapshot(self._default_session(username), self._users_dir)

    def save(self, username: str, session: dict):
        """保存 session 到 users/{username}.json

        Args:
            username: 用户名
            session: session 数据
        """
        self._validate_username(username)
        path = self._users_dir / f"{username}.session.json"
        with _SAVE_LOCK, _locked_file(path):
            disk = self._load(username, path)
            baseline = session.baseline if isinstance(session, SessionSnapshot) else {}
            merged = _merge_changes(baseline, session, disk)
            self._save(path, merged)
            if isinstance(session, SessionSnapshot):
                # Do not replace the running dictionary or invalidate nested references.
                session.baseline = deepcopy(dict(session))
        logger.debug(f"已保存 session: {username}")

    def update(self, username: str, mutator: Callable[[dict], None]) -> dict:
        """read-modify-write。

        用于多个入口可能同时修改同一用户 session 的场景。mutator 只修改
        自己负责的节点，可降低旧快照整文件覆盖风险。

        失败时抛出异常，调用方应自行 try/except 处理。
        """
        self._validate_username(username)
        path = self._users_dir / f"{username}.session.json"
        with _SAVE_LOCK, _locked_file(path):
            session = self._load(username, path)
            mutator(session)
            self._save(path, session)
        logger.debug(f"已更新 session: {username}")
        return SessionSnapshot(session, self._users_dir)

    def save_fn(self, username: str, session_ref: dict) -> Callable:
        """返回一个绑定了用户名和 session 引用的保存回调

        供 engine._save_callback 使用，DSL 中调用 save() 时触发。

        Args:
            username: 用户名
            session_ref: session 字典引用（通常是 engine.session）

        Returns:
            Callable: 无参保存函数
        """
        if isinstance(session_ref, SessionSnapshot):
            return lambda: self.save(username, session_ref)
        snapshot = SessionSnapshot({})

        def persist():
            snapshot.clear()
            snapshot.update(session_ref)
            self.save(username, snapshot)

        return persist
