"""用户 Session 持久化管理器

负责从 users/{username}.json 加载/保存 session 数据。
UI 层在 Engine 创建后注入 session，并在正常结束时调用 save。
"""

import json
from pathlib import Path
from typing import Callable

from loguru import logger


class SessionManager:
    """Session 持久化管理器"""

    def __init__(self, users_dir: Path | None = None):
        if users_dir is None:
            from ...constants import USERS_DIR
            users_dir = USERS_DIR
        self._users_dir = users_dir
        self._users_dir.mkdir(parents=True, exist_ok=True)

    def load(self, username: str) -> dict:
        """从 users/{username}.json 加载 session

        Args:
            username: 用户名

        Returns:
            dict: session 数据（至少包含 current_user 字段）
        """
        path = self._users_dir / f"{username}.json"
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                logger.debug(f"已加载 session: {username} ({len(data)} 个字段)")
                return data
            except Exception as e:
                logger.error(f"加载 session 失败: {e}")
        # 默认 session
        return {"current_user": username}

    def save(self, username: str, session: dict):
        """保存 session 到 users/{username}.json

        Args:
            username: 用户名
            session: session 数据
        """
        path = self._users_dir / f"{username}.json"
        try:
            path.write_text(
                json.dumps(session, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            logger.info(f"已保存 session: {username}")
        except Exception as e:
            logger.error(f"保存 session 失败: {e}")

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
