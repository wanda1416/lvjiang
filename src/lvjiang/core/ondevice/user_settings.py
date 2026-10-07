"""手机用户资料维护；仅修改名册与属性，不同步回 PC、不切换编辑用户为执行用户。"""
from __future__ import annotations

import json

from ..user_config import UserConfigManager, load_user_metadata
from . import task_runner


def _json(value: dict) -> str:
    return json.dumps(value, ensure_ascii=False)


def get_user(username: str) -> str:
    with task_runner.CONTROL_LOCK:
        try:
            if username not in task_runner._local_user_names():
                raise ValueError("用户资料不可用")
            user = load_user_metadata(username)
            if user is None:
                raise ValueError("用户资料不可用")
            return _json({"ok": True, "username": username, "attributes": user.attributes,
                          "readonly": task_runner.is_running()})
        except Exception as exc:
            return _json({"ok": False, "message": str(exc)})


def create_user(username: str) -> str:
    with task_runner.CONTROL_LOCK:
        try:
            if task_runner.is_running():
                raise ValueError("停止任务后可修改用户资料")
            if not UserConfigManager().create_user(username):
                raise ValueError("用户名称已存在或格式无效（1～32 个字母、数字、汉字、下划线或连字符）")
            return _json({"ok": True, "message": "用户已创建，请确认执行资料与游戏角色一致"})
        except Exception as exc:
            return _json({"ok": False, "message": str(exc)})


def save_attributes(username: str, payload: str, baseline: str) -> str:
    with task_runner.CONTROL_LOCK:
        try:
            if task_runner.is_running():
                raise ValueError("停止任务后可修改用户资料")
            if username not in task_runner._local_user_names():
                raise ValueError("用户已不可用，请重新加载")
            values, original = json.loads(payload), json.loads(baseline)
            for attributes in (values, original):
                if not isinstance(attributes, dict) or any(
                    not isinstance(key, str) or not key.strip() or not isinstance(value, str)
                    for key, value in attributes.items()
                ):
                    raise ValueError("属性名称不能为空，名称及内容须为文本")
            UserConfigManager().replace_user_attributes(username, values, original)
            return _json({"ok": True, "message": "已保存"})
        except Exception as exc:
            return _json({"ok": False, "message": str(exc)})
