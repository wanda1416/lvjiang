"""反馈草稿：写了一半的反馈别丢。

落 ``config/session/feedback/``（运行态，不是配置）：它属于「用户此刻正在做的事」，
和 ``config/local``（用户主动维护的覆盖层）不是一类东西。提交成功后删除。

不做后台重发队列：正文没提交成功时用户就在对话框前，重试按钮比一个看不见的
队列更可靠——队列会把「到底发出去没有」变成一件需要查日志才能回答的事。
"""
from __future__ import annotations

import json
from pathlib import Path

from loguru import logger

from ..json_files import formatted_json


def draft_dir() -> Path:
    from ...constants import SESSION_CONFIG_DIR
    return SESSION_CONFIG_DIR / "feedback"


def draft_path() -> Path:
    return draft_dir() / "draft.json"


def load_draft() -> dict:
    """读取草稿；不存在或损坏时返回空 dict（损坏不该挡住打开对话框）。"""
    try:
        data = json.loads(draft_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_draft(data: dict) -> None:
    """保存草稿；写盘失败只记日志，不打断用户。"""
    if not data:
        clear_draft()
        return
    path = draft_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(formatted_json(data), encoding="utf-8", newline="\n")
    except OSError as error:
        logger.warning("反馈草稿保存失败：{}", error)


def clear_draft() -> None:
    """删除草稿（提交成功或用户清空时）。"""
    try:
        draft_path().unlink(missing_ok=True)
    except OSError as error:
        logger.warning("反馈草稿删除失败：{}", error)
