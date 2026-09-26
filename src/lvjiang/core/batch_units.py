"""从现有用户资料派生批量执行单元；不持久化账号或其他属性实体。"""
from __future__ import annotations

from collections import OrderedDict
from pathlib import Path

from .user_config import load_user_metadata


def group_users(
    usernames: list[str], key: str, users_dir: Path | None = None,
) -> dict[str, list[str]]:
    """返回稳定顺序的单元成员；无该属性的用户不进入属性单元。"""
    groups: dict[str, list[str]] = OrderedDict()
    for username in usernames:
        if key == "user":
            value = username
        else:
            user = load_user_metadata(username, users_dir)
            value = str(user.attributes.get(key, "")).strip() if user else ""
        if value:
            groups.setdefault(value, []).append(username)
    return groups
