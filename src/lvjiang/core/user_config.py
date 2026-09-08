"""用户目录与用户资料持久化。

``session.json`` 只保存用户名顺序；每个用户的资料保存在
``users/{username}.json``，工作流 Session 另存为 ``{username}.session.json``。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from loguru import logger

from ..i18n import tr
from .fs_util import atomic_write_text

_VALID_USERNAME = re.compile(r"^[\w一-鿿-]{1,32}$")
USER_DOCUMENT_TYPE = "lvjiang.user"
USER_SCHEMA_VERSION = 1
USER_ATTRIBUTE_KEYS = ("account", "role", "role_index", "tail")


def is_valid_username(name: str) -> bool:
    return bool(name) and bool(_VALID_USERNAME.fullmatch(name))


def _users_dir() -> Path:
    from ..constants import USERS_DIR
    return USERS_DIR


@dataclass
class User:
    """应用用户身份及其业务资料。``name`` 是稳定的内部用户名。"""

    name: str
    created_at: str = ""
    avatar: str = ""
    attributes: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "document_type": USER_DOCUMENT_TYPE,
            "schema_version": USER_SCHEMA_VERSION,
            "username": self.name,
            "created_at": self.created_at,
            "avatar": self.avatar,
            "attributes": {
                key: str(value)
                for key, value in self.attributes.items()
                if key and value is not None
            },
        }

    @staticmethod
    def from_dict(data: dict, *, fallback_name: str = "") -> "User":
        from .user_avatars import is_safe_avatar_filename

        raw_avatar = data.get("avatar", "")
        raw_attributes = data.get("attributes", {})
        attributes = (
            {str(k): str(v) for k, v in raw_attributes.items() if k}
            if isinstance(raw_attributes, dict) else {}
        )
        name = str(data.get("username") or data.get("name") or fallback_name)
        return User(
            name=name,
            created_at=str(data.get("created_at", "")),
            avatar=raw_avatar if is_safe_avatar_filename(raw_avatar) else "",
            attributes=attributes,
        )


def user_metadata_path(username: str, users_dir: Path | None = None) -> Path:
    return (users_dir or _users_dir()) / f"{username}.json"


def load_user_metadata(username: str, users_dir: Path | None = None) -> User | None:
    path = user_metadata_path(username, users_dir)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("用户资料必须是 JSON 对象")
        if data.get("document_type") != USER_DOCUMENT_TYPE:
            raise ValueError("文件不是用户资料")
        user = User.from_dict(data, fallback_name=username)
        if user.name != username:
            raise ValueError("用户资料中的用户名与文件名不一致")
        return user
    except Exception as exc:
        logger.error(f"加载用户资料失败: {path}: {exc}")
        return None


def save_user_metadata(user: User, users_dir: Path | None = None) -> None:
    path = user_metadata_path(user.name, users_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(
        path,
        json.dumps(user.to_dict(), ensure_ascii=False, indent=2),
        prefix=f".{user.name}_metadata_",
    )


def get_user_attribute(username: str, key: str, users_dir: Path | None = None):
    user = load_user_metadata(username, users_dir)
    return user.attributes.get(key) if user is not None else None


class UserConfigManager:
    """用户增删查改、顺序维护与当前用户切换。"""

    def __init__(self, users_dir: Path | None = None):
        self._users_dir = users_dir or _users_dir()
        self._users_dir.mkdir(parents=True, exist_ok=True)
        from .config.session import get_session_store
        from .config.user_storage_migration import migrate_user_storage

        migrate_user_storage(get_session_store(), self._users_dir)
        self._users: dict[str, User] = {}
        self._active_user = ""
        self._load()

    def _load(self) -> None:
        from .config.session import get_session_store

        store = get_session_store()
        users_raw = store.get_node("users", [])
        if isinstance(users_raw, list):
            for value in users_raw:
                if not isinstance(value, str) or not is_valid_username(value):
                    continue
                user = load_user_metadata(value, self._users_dir)
                if user is None:
                    user = User(name=value, created_at=datetime.now().isoformat())
                    save_user_metadata(user, self._users_dir)
                self._users[value] = user

        active = store.get_active("user", "")
        self._active_user = active if isinstance(active, str) else ""
        if self._active_user and self._active_user not in self._users:
            self._active_user = ""
        if not self._users:
            self._create_default_user()

    def _save_order(self) -> None:
        from .config.session import get_session_store

        store = get_session_store()
        store.mutate_node("users", lambda _: list(self._users))
        store.set_active("user", self._active_user)

    def _save_user(self, user: User) -> None:
        save_user_metadata(user, self._users_dir)

    def _create_default_user(self) -> None:
        user = User(name=tr("默认用户"), created_at=datetime.now().isoformat())
        self._users[user.name] = user
        self._active_user = user.name
        self._save_user(user)
        self._save_order()
        logger.info("已创建默认用户")

    def list_users(self) -> list[str]:
        return list(self._users)

    def get_user(self, name: str) -> User | None:
        return self._users.get(name)

    def create_user(self, name: str) -> bool:
        if not is_valid_username(name) or name in self._users:
            return False
        user = User(name=name, created_at=datetime.now().isoformat())
        self._save_user(user)
        self._users[name] = user
        self._save_order()
        logger.info(f"用户已创建: {name}")
        return True

    def delete_user(self, name: str) -> bool:
        if name not in self._users or len(self._users) == 1:
            return False
        del self._users[name]
        if self._active_user == name:
            self._active_user = next(iter(self._users))
        self._save_order()
        for suffix in (".json", ".session.json", ".notes.json", ".loadouts.json"):
            try:
                (self._users_dir / f"{name}{suffix}").unlink(missing_ok=True)
            except OSError as exc:
                logger.warning(f"清理用户 {name} 文件失败: {exc}")
        logger.info(f"用户已删除: {name}")
        return True

    def reorder_users(self, names: list[str]) -> bool:
        if len(names) != len(self._users) or set(names) != set(self._users):
            return False
        self._users = {name: self._users[name] for name in names}
        self._save_order()
        logger.info(f"用户顺序已更新: {names}")
        return True

    def update_user_attributes(self, name: str, attributes: dict[str, str]) -> bool:
        user = self._users.get(name)
        if user is None:
            return False
        previous = dict(user.attributes)
        user.attributes.update({str(k): str(v).strip() for k, v in attributes.items()})
        try:
            self._save_user(user)
        except Exception:
            user.attributes = previous
            raise
        return True

    def set_user_avatar(self, name: str, filename: str) -> bool:
        from .user_avatars import is_safe_avatar_filename

        user = self._users.get(name)
        if user is None or (filename and not is_safe_avatar_filename(filename)):
            return False
        previous = user.avatar
        user.avatar = filename
        try:
            self._save_user(user)
        except Exception:
            user.avatar = previous
            raise
        logger.info(f"用户头像已更新: {name}")
        return True

    def get_active_user_name(self) -> str:
        return self._active_user

    def set_active_user(self, name: str) -> bool:
        if name not in self._users:
            return False
        self._active_user = name
        from .config.session import get_session_store
        get_session_store().set_active("user", name)
        logger.info(f"已切换到用户: {name}")
        return True
