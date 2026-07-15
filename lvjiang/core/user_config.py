"""用户配置管理 - 多用户支持"""

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from loguru import logger

from ..constants import USER_CONFIG_DIR


# ─── 路径常量 ────────────────────────────────────────────

CONFIG_FILE = USER_CONFIG_DIR / "config.json"


# ─── 数据类 ──────────────────────────────────────────────

@dataclass
class User:
    """用户数据"""
    name: str
    created_at: str = ""  # ISO 格式时间戳

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
        USER_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        self._users: dict[str, User] = {}
        self._active_user: str = ""
        self._load()

    def _load(self):
        """从 config.json 加载用户相关字段"""
        if CONFIG_FILE.exists():
            try:
                data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
                for u_data in data.get("users", []):
                    user = User.from_dict(u_data)
                    self._users[user.name] = user
                self._active_user = data.get("active_user", "")
                if self._active_user and self._active_user not in self._users:
                    self._active_user = ""
            except Exception as e:
                logger.error(f"加载 config.json 用户数据失败: {e}")
                self._users = {}
                self._active_user = ""

        # 如果没有用户，创建默认用户
        if not self._users:
            self._create_default_user()

    def _save(self):
        """保存用户字段到 config.json（保留其他字段）"""
        data = {}
        if CONFIG_FILE.exists():
            try:
                data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            except Exception:
                pass
        data["users"] = [u.to_dict() for u in self._users.values()]
        data["active_user"] = self._active_user
        USER_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _create_default_user(self):
        """创建默认用户"""
        default = User(
            name="默认用户",
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
        """创建新用户，返回是否成功"""
        if not name or name in self._users:
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

    # ─── 激活用户 ────────────────────────────────────────

    def get_active_user_name(self) -> str:
        """获取当前激活用户名"""
        return self._active_user

    def get_active_user(self) -> User | None:
        """获取当前激活用户对象"""
        return self._users.get(self._active_user)

    def set_active_user(self, name: str) -> bool:
        """切换激活用户"""
        if name not in self._users:
            return False
        self._active_user = name
        self._save()
        logger.info(f"已切换到用户: {name}")
        return True

