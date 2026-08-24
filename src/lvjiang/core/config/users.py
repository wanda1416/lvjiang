"""用户 Session 持久化管理器

负责从 users/{username}.json 加载/保存 session 数据。
UI 层在 Engine 创建后注入 session，并在正常结束时调用 save。
"""

import json
from pathlib import Path
from typing import Callable

from loguru import logger

from ..fs_util import atomic_write_text


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

    def _load(self, username: str, path: Path) -> dict:
        if not path.exists():
            return self._default_session(username)
        data = json.loads(path.read_text(encoding="utf-8"))
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
        path = self._users_dir / f"{username}.json"
        try:
            return self._load(username, path)
        except Exception as e:
            logger.error(f"加载 session 失败: {e}")
        # 默认 session
        return self._default_session(username)

    def save(self, username: str, session: dict):
        """保存 session 到 users/{username}.json

        Args:
            username: 用户名
            session: session 数据
        """
        path = self._users_dir / f"{username}.json"
        try:
            self._save(path, session)
            logger.debug(f"已保存 session: {username}")
        except Exception as e:
            logger.error(f"保存 session 失败: {e}")

    def update(self, username: str, mutator: Callable[[dict], None]) -> dict:
        """read-modify-write。

        用于多个入口可能同时修改同一用户 session 的场景。mutator 只修改
        自己负责的节点，可降低旧快照整文件覆盖风险。

        失败时抛出异常，调用方应自行 try/except 处理。
        """
        path = self._users_dir / f"{username}.json"
        session = self._load(username, path)
        mutator(session)
        self._save(path, session)
        logger.debug(f"已更新 session: {username}")
        return session

    def save_fn(self, username: str, session_ref: dict) -> Callable:
        """返回一个绑定了用户名和 session 引用的保存回调

        供 engine._save_callback 使用，DSL 中调用 save() 时触发。

        Args:
            username: 用户名
            session_ref: session 字典引用（通常是 engine.session）

        Returns:
            Callable: 无参保存函数
        """
        return lambda: self.save(username, session_ref)
