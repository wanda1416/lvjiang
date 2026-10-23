"""用户目录与用户资料持久化。

``session.json`` 只保存用户名顺序；每个用户的资料保存在
``users/{username}.json``，工作流 Session 另存为 ``{username}.session.json``。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from fasteners import InterProcessLock
from loguru import logger

from .access import user_write_access
from .fs_util import atomic_write_text
from .json_files import formatted_json

_VALID_USERNAME = re.compile(r"^[\w一-鿿-]{1,32}$")
USER_DOCUMENT_TYPE = "lvjiang.user"
USER_SCHEMA_VERSION = 2
_METADATA_SAVE_LOCK = threading.RLock()

# 每份用户资料自己的跨进程读改写锁。执行租约表达「这个用户正被任务独占」，
# 只管用户数据文件；界面偏好也写在同一份资料里，却与任务数据无关，不能因为
# 别的进程在跑任务就写不进去。
_METADATA_LOCKS: dict[str, tuple[threading.RLock, InterProcessLock]] = {}
_METADATA_LOCKS_GUARD = threading.Lock()


@contextmanager
def _metadata_write_lock(path: Path):
    """串行化 ``path`` 的跨进程读改写，与执行租约相互独立。"""
    key = os.path.normcase(str(path.resolve()))
    with _METADATA_LOCKS_GUARD:
        entry = _METADATA_LOCKS.get(key)
        if entry is None:
            lock_dir = path.parent / ".lock"
            lock_dir.mkdir(parents=True, exist_ok=True)
            name = hashlib.sha256(key.encode()).hexdigest()
            entry = (
                threading.RLock(),
                InterProcessLock(str(lock_dir / f"{name}.metadata.lock")),
            )
            _METADATA_LOCKS[key] = entry
    thread_lock, process_lock = entry
    with thread_lock, process_lock:
        yield


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
    ui_state: dict[str, dict[str, Any]] = field(default_factory=dict)
    # 保留插件拥有的顶层字段；公共资料编辑不能丢弃未解释的数据。
    extra_fields: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            **deepcopy(self.extra_fields),
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
            "ui_state": {
                str(key): dict(value)
                for key, value in self.ui_state.items()
                if key and isinstance(value, dict)
            },
        }

    @staticmethod
    def from_dict(data: dict, *, fallback_name: str = "") -> "User":
        from .user_avatars import is_safe_avatar_filename

        raw_avatar = data.get("avatar", "")
        raw_attributes = data.get("attributes", {})
        raw_workflow_params = data.get("workflow_params", {})
        raw_ui_state = data.get("ui_state", {})
        attributes = (
            {str(k): str(v) for k, v in raw_attributes.items() if k}
            if isinstance(raw_attributes, dict) else {}
        )
        name = str(data.get("username") or fallback_name)
        user = User(
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
            ui_state=(
                {str(key): dict(value)
                 for key, value in raw_ui_state.items()
                 if key and isinstance(value, dict)}
                if isinstance(raw_ui_state, dict) else {}
            ),
        )
        # 公共字段以序列化契约为准，避免另行维护排除列表。
        known_fields = user.to_dict().keys()
        user.extra_fields = {key: deepcopy(value) for key, value in data.items()
                             if key not in known_fields}
        return user


def user_metadata_path(username: str, users_dir: Path | None = None) -> Path:
    if not is_valid_username(username):
        raise ValueError(f"非法用户名: {username!r}")
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
    with user_write_access(user.name, path.parent), _metadata_write_lock(
            path), _METADATA_SAVE_LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(
            path,
            formatted_json(user.to_dict()),
            prefix=f".{user.name}_metadata_",
        )


def mutate_user_metadata(
    username: str,
    mutator,
    users_dir: Path | None = None,
) -> User:
    """锁内读取最新资料并只提交实际变化；相同内容不重写文件。"""
    path = user_metadata_path(username, users_dir)
    with user_write_access(username, path.parent), _metadata_write_lock(
            path), _METADATA_SAVE_LOCK:
        user = load_user_metadata(username, users_dir)
        if user is None:
            raise FileNotFoundError(f"用户资料不存在: {username}")
        before = deepcopy(user.to_dict())
        mutator(user)
        after = user.to_dict()
        if after == before:
            return user
        atomic_write_text(
            path,
            formatted_json(after),
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


_readonly_ui_states: dict[tuple[Path, str], dict[str, Any]] = {}


def get_user_ui_state(
    username: str, key: str, users_dir: Path | None = None,
) -> dict[str, Any]:
    """读取 ``users/{username}.json`` 中一项用户界面状态。"""
    from .access import is_readonly
    path = user_metadata_path(username, users_dir).resolve()
    if is_readonly() and (path, key) in _readonly_ui_states:
        return deepcopy(_readonly_ui_states[path, key])
    user = load_user_metadata(username, users_dir)
    if user is None:
        return {}
    value = user.ui_state.get(key)
    return deepcopy(value) if isinstance(value, dict) else {}


def set_user_ui_state(
    username: str,
    key: str,
    value: dict[str, Any],
    users_dir: Path | None = None,
) -> None:
    """原子替换 ``users/{username}.json`` 中一项用户界面状态。

    界面偏好与任务数据无关：另一个进程正在执行该用户的任务时也必须能写。
    这里只用资料文件自己的锁，不申请执行租约——否则一次点击槽位保存筛选
    就会变成「用户正在执行任务」的致命错误。
    """
    saved = deepcopy(value)
    path = user_metadata_path(username, users_dir)
    from .access import is_readonly
    if is_readonly():
        _readonly_ui_states[path.resolve(), key] = saved
        return
    with _metadata_write_lock(path), _METADATA_SAVE_LOCK:
        user = load_user_metadata(username, users_dir)
        if user is None:
            raise FileNotFoundError(f"用户资料不存在: {username}")
        if user.ui_state.get(key) == saved:
            return
        user.ui_state[key] = saved
        atomic_write_text(
            path,
            formatted_json(user.to_dict()),
            prefix=f".{username}_metadata_",
        )


def list_user_names() -> list[str]:
    """用户名册顺序；界面用户列表的第一行就是它。"""
    from .config.session import get_session_store

    raw = get_session_store().get_node("users", [])
    if not isinstance(raw, list):
        return []
    return [name for name in raw
            if isinstance(name, str) and is_valid_username(name)]


def resolve_active_username() -> str:
    """当前执行用户；``actives.user`` 没记录时回退名册第一行。

    「当前用户」只认这一处：界面顶部、备战方案、任务启动、离线同步都从这里
    取。否则 actives 一旦丢了，顶部照常按名册第一行展示，别处却拿到空用户名
    ——于是按空用户名去读 ``users/*.loadouts.json``，方案、装备这些数据就
    「加载不出来」。

    名册为空时不能反过来丢掉 actives 里已有的名字：手机端、离线包这类场景
    本就没有 PC 名册，但执行用户是明确的。
    """
    from .config.interface import get_interface_store

    active = get_interface_store().get_active("user", "")
    names = list_user_names()
    if not isinstance(active, str) or not active:
        return names[0] if names else ""
    if names and active not in names:
        return names[0]
    return active


class UserConfigManager:
    """用户增删查改、顺序维护与当前用户切换。"""

    def __init__(self, users_dir: Path | None = None):
        self._users_dir = users_dir or _users_dir()
        self._users_dir.mkdir(parents=True, exist_ok=True)
        self._users: dict[str, User] = {}
        self._active_user = ""
        self._load()

    def _load(self) -> None:
        from .config.interface import get_interface_store
        from .config.session import get_session_store

        store = get_session_store()
        active_store = get_interface_store()
        users_raw = store.get_node("users", [])
        if isinstance(users_raw, list):
            for value in users_raw:
                if not isinstance(value, str) or not is_valid_username(value):
                    continue
                user = load_user_metadata(value, self._users_dir)
                if user is None:
                    from .access import is_readonly
                    if is_readonly():
                        continue
                    user = User(name=value, created_at=datetime.now().isoformat())
                    save_user_metadata(user, self._users_dir)
                self._users[value] = user

        active = active_store.get_active("user", "")
        self._active_user = active if isinstance(active, str) else ""
        if self._active_user and self._active_user not in self._users:
            self._active_user = ""
        if not self._users:
            self._create_default_user()
        # actives 没记录（或记的名字已不在名册）时，界面第一行就是当前用户。
        # 不兜底就会分裂：顶部照常展示第一个用户，而只认 get_active_user_name()
        # 的备战方案、装备页拿到空用户名，读 users/*.loadouts.json 自然为空。
        if not self._active_user and self._users:
            self._active_user = next(iter(self._users))

    def _save_order(self) -> None:
        from .config.interface import get_interface_store
        from .config.session import get_session_store

        store = get_session_store()
        active_store = get_interface_store()
        store.mutate_node("users", lambda _: list(self._users))
        active_store.set_active("user", self._active_user)

    def _save_user(self, user: User) -> None:
        save_user_metadata(user, self._users_dir)

    def _create_default_user(self) -> None:
        user = User(name="default", created_at=datetime.now().isoformat())
        self._users[user.name] = user
        self._active_user = user.name
        from .access import is_readonly
        if is_readonly():
            return  # 空目录只读展示，不创建用户资料或名册。
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
        user = load_user_metadata(name, self._users_dir)
        restored = user is not None
        if user is None:
            user = User(name=name, created_at=datetime.now().isoformat())
            self._save_user(user)
        self._users[name] = user
        self._save_order()
        logger.info(f"用户已{'恢复' if restored else '创建'}: {name}")
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
        logger.info(f"用户已从列表移除，数据文件已保留: {name}")
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
        from .config.interface import get_interface_store
        get_interface_store().set_active("user", name)
        logger.info(f"已切换到用户: {name}")
        return True
