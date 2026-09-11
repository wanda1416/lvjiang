"""用户目录与用户资料持久化。

``session.json`` 只保存用户名顺序；每个用户的资料保存在
``users/{username}.json``，工作流 Session 另存为 ``{username}.session.json``。
"""
from __future__ import annotations

import json
import re
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from fasteners import InterProcessLock
from loguru import logger

from .fs_util import atomic_write_text

_VALID_USERNAME = re.compile(r"^[\w一-鿿-]{1,32}$")
USER_DOCUMENT_TYPE = "lvjiang.user"
USER_SCHEMA_VERSION = 2
_METADATA_SAVE_LOCK = threading.RLock()


class UserMetadataConflictError(RuntimeError):
    """用户资料中的同一属性被另一个实例同时修改。"""


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
    workflow_params: dict[str, dict[str, Any]] = field(default_factory=dict)

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
            "workflow_params": {
                str(workflow_id): dict(params)
                for workflow_id, params in self.workflow_params.items()
                if workflow_id and isinstance(params, dict)
            },
        }

    @staticmethod
    def from_dict(data: dict, *, fallback_name: str = "") -> "User":
        from .user_avatars import is_safe_avatar_filename

        raw_avatar = data.get("avatar", "")
        raw_attributes = data.get("attributes", {})
        raw_workflow_params = data.get("workflow_params", {})
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
            workflow_params=(
                {str(wf_id): dict(params)
                 for wf_id, params in raw_workflow_params.items()
                 if wf_id and isinstance(params, dict)}
                if isinstance(raw_workflow_params, dict) else {}
            ),
        )


def user_metadata_path(username: str, users_dir: Path | None = None) -> Path:
    if not is_valid_username(username):
        raise ValueError(f"非法用户名: {username!r}")
    return (users_dir or _users_dir()) / f"{username}.json"


@contextmanager
def _locked_metadata(path: Path):
    lock = InterProcessLock(str(path) + ".lock")
    if not lock.acquire(blocking=True, timeout=5):
        raise TimeoutError(f"用户资料写入锁超时: {path.name}")
    try:
        yield
    finally:
        lock.release()


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
    with _METADATA_SAVE_LOCK, _locked_metadata(path):
        atomic_write_text(
            path,
            json.dumps(user.to_dict(), ensure_ascii=False, indent=2),
            prefix=f".{user.name}_metadata_",
        )


def mutate_user_metadata(
    username: str,
    mutator,
    users_dir: Path | None = None,
) -> User:
    """锁内读取最新资料并只提交 mutator 所做的修改。"""
    path = user_metadata_path(username, users_dir)
    with _METADATA_SAVE_LOCK, _locked_metadata(path):
        user = load_user_metadata(username, users_dir)
        if user is None:
            raise FileNotFoundError(f"用户资料不存在: {username}")
        mutator(user)
        atomic_write_text(
            path,
            json.dumps(user.to_dict(), ensure_ascii=False, indent=2),
            prefix=f".{username}_metadata_",
        )
        return user


def get_user_attribute(username: str, key: str, users_dir: Path | None = None):
    user = load_user_metadata(username, users_dir)
    return user.attributes.get(key) if user is not None else None


def get_user_workflow_params(
    username: str, workflow_id: str, users_dir: Path | None = None,
) -> dict[str, Any] | None:
    """读取用户对单个任务的独立参数；None 表示继续使用共享参数。"""
    user = load_user_metadata(username, users_dir)
    if user is None or workflow_id not in user.workflow_params:
        return None
    return dict(user.workflow_params[workflow_id])


def set_user_workflow_params(
    username: str, workflow_id: str, params: dict[str, Any],
    users_dir: Path | None = None,
) -> None:
    """原子替换一个用户与任务组合的独立参数。"""
    values = dict(params)
    mutate_user_metadata(
        username,
        lambda user: user.workflow_params.__setitem__(workflow_id, values),
        users_dir,
    )


def delete_user_workflow_params(
    username: str, workflow_id: str, users_dir: Path | None = None,
) -> None:
    """删除独立参数，使该用户与任务组合恢复使用共享参数。"""
    mutate_user_metadata(
        username,
        lambda user: user.workflow_params.pop(workflow_id, None),
        users_dir,
    )


class UserConfigManager:
    """用户增删查改、顺序维护与当前用户切换。"""

    def __init__(self, users_dir: Path | None = None):
        self._users_dir = users_dir or _users_dir()
        self._users_dir.mkdir(parents=True, exist_ok=True)
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
        user = User(name="default", created_at=datetime.now().isoformat())
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
        from .batch_config import remove_username_from_batch_configs
        remove_username_from_batch_configs(name)
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
        values = {str(k): str(v).strip() for k, v in attributes.items()}
        updated = mutate_user_metadata(
            name, lambda latest: latest.attributes.update(values), self._users_dir
        )
        self._users[name] = updated
        return True

    def replace_user_attributes(
        self,
        name: str,
        attributes: dict[str, str],
        baseline: dict[str, str],
    ) -> bool:
        """保存通用属性编辑结果，并合并其他实例对不同 key 的修改。"""
        if name not in self._users:
            return False
        desired = {str(key): str(value) for key, value in attributes.items()}
        original = {str(key): str(value) for key, value in baseline.items()}

        def apply(latest: User) -> None:
            for key in set(original) | set(desired):
                before = original.get(key)
                after = desired.get(key)
                if before == after:
                    continue
                current = latest.attributes.get(key)
                if current != before and current != after:
                    raise UserMetadataConflictError(f"用户属性保存冲突: {key}")
                if key in desired:
                    latest.attributes[key] = desired[key]
                else:
                    latest.attributes.pop(key, None)

        self._users[name] = mutate_user_metadata(name, apply, self._users_dir)
        return True

    def set_user_avatar(self, name: str, filename: str) -> bool:
        from .user_avatars import is_safe_avatar_filename

        user = self._users.get(name)
        if user is None or (filename and not is_safe_avatar_filename(filename)):
            return False
        updated = mutate_user_metadata(
            name, lambda latest: setattr(latest, "avatar", filename), self._users_dir
        )
        self._users[name] = updated
        logger.info(f"用户头像已更新: {name}")
        return True

    def get_active_user_name(self) -> str:
        return self._active_user

    @property
    def users_dir(self) -> Path:
        """用户资料目录，供用户级业务配置绑定到同一存储实例。"""
        return self._users_dir

    def set_active_user(self, name: str) -> bool:
        if name not in self._users:
            return False
        self._active_user = name
        from .config.session import get_session_store
        get_session_store().set_active("user", name)
        logger.info(f"已切换到用户: {name}")
        return True
