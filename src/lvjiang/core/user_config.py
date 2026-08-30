"""用户配置管理 - 多用户支持"""

import re
from dataclasses import dataclass
from datetime import datetime

from loguru import logger

from ..i18n import tr

#: 合法用户名：中文 + 常见 ID 命名（字母数字下划线连字符），1~32 字。
#:
#: 用户名会**直接当文件名**用（``users/{name}.json``，见 config/users.py），
#: 也会拼进 profile 告警去重的复合键（``{user}:{key}:...``，见
#: profile_engine._clean_old_alerts）。不校验的话：``../x`` 能写出 users
#: 目录之外，``a/b`` 会凭空建子目录，含 ``:`` 的名字在 Windows 上直接存不了、
#: 还会让告警键按 ``:`` 切分时错位、把有效记录当成过期的删掉。
#:
#: 校验放在这一层而不是只放 UI：``create_user`` 是产生用户名的唯一入口，
#: 挡在这里才对所有调用方（含将来的脚本/设备端）都成立。
_VALID_USERNAME = re.compile(r"^[\w一-鿿-]{1,32}$")


def is_valid_username(name: str) -> bool:
    """用户名是否合法（见 :data:`_VALID_USERNAME` 的理由）。"""
    return bool(name) and bool(_VALID_USERNAME.fullmatch(name))

# ─── 数据类 ──────────────────────────────────────────────

@dataclass
class User:
    """用户数据 — 核心标识，仅用于产出归档与 session 隔离。"""
    name: str
    created_at: str = ""      # ISO 格式时间戳

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "created_at": self.created_at,
        }

    @staticmethod
    def from_dict(d: dict) -> "User":
        return User(
            name=d.get("name", ""),
            created_at=d.get("created_at", ""),
        )


# ─── 管理器 ──────────────────────────────────────────────

class UserConfigManager:
    """用户管理：增删查改 + 当前用户切换"""

    def __init__(self):
        self._users: dict[str, User] = {}
        self._active_user: str = ""
        self._load()

    def _load(self):
        """从 session.json 加载用户相关字段（经 SessionStore 统一入口）"""
        from .config.session import get_session_store
        store = get_session_store()
        users_raw = store.get_node("users", [])
        if isinstance(users_raw, list):
            for u_data in users_raw:
                if isinstance(u_data, dict):
                    user = User.from_dict(u_data)
                    self._users[user.name] = user
        active = store.get_active("user", "")
        self._active_user = active if isinstance(active, str) else ""
        if self._active_user and self._active_user not in self._users:
            self._active_user = ""

        # 如果没有用户，创建默认用户
        if not self._users:
            self._create_default_user()

    def _save(self):
        """保存用户字段到 session.json（原子操作，经 SessionStore）

        ⚠️ 使用 mutate_node 确保并发安全
        """
        from .config.session import get_session_store
        store = get_session_store()
        # 原子化保存用户列表
        store.mutate_node("users", lambda _: [u.to_dict() for u in self._users.values()])
        # 原子化保存当前用户
        store.set_active("user", self._active_user)

    def _create_default_user(self):
        """创建默认用户"""
        default = User(
            name=tr("默认用户"),
            created_at=datetime.now().isoformat(),
        )
        self._users[default.name] = default
        self._active_user = default.name
        self._save()
        logger.info("已创建默认用户")

    # ─── 用户 CRUD ──────────────────────────────────────

    def list_users(self) -> list[str]:
        """返回所有用户名列表"""
        return list(self._users.keys())

    def get_user(self, name: str) -> User | None:
        """获取指定用户"""
        return self._users.get(name)

    def create_user(self, name: str) -> bool:
        """创建新用户，返回是否成功（名字非法或重名都返回 False）"""
        if not is_valid_username(name) or name in self._users:
            return False
        user = User(
            name=name,
            created_at=datetime.now().isoformat(),
        )
        self._users[name] = user
        self._save()
        logger.info(f"用户已创建: {name}")
        return True


    def delete_user(self, name: str) -> bool:
        """删除用户，返回是否成功"""
        if name not in self._users:
            return False
        # 不能删除最后一个用户
        if len(self._users) == 1:
            logger.warning("不能删除最后一个用户")
            return False

        del self._users[name]

        # 如果删除的是激活用户，切换到第一个
        if self._active_user == name:
            self._active_user = next(iter(self._users))

        self._save()
        logger.info(f"用户已删除: {name}")
        return True

    def reorder_users(self, names: list[str]) -> bool:
        """按给定顺序重排用户，names 必须与现有用户完全一致"""
        if len(names) != len(self._users) or set(names) != set(self._users):
            return False
        self._users = {name: self._users[name] for name in names}
        self._save()
        logger.info(f"用户顺序已更新: {names}")
        return True

    # ─── 激活用户 ────────────────────────────────────────

    def get_active_user_name(self) -> str:
        """获取当前激活用户名"""
        return self._active_user


    def set_active_user(self, name: str) -> bool:
        """切换激活用户"""
        if name not in self._users:
            return False
        self._active_user = name
        self._save()
        logger.info(f"已切换到用户: {name}")
        return True
