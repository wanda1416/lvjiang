"""批量启动时收集「用户声明跳过」的组合。

用户资料里的 ``skipped_task_ids``（见 :mod:`..users.document`）是长期声明，
可以指向任意任务；一次批量只关心本次勾选的脚本，所以这里按候选用户与目标
脚本求交集，结果冻结进 :class:`BatchRunSpec.excluded`，运行期不再回读资料。

读取失败不能让批量启动失败：单个用户资料坏掉时按「未声明跳过」处理并留一条
warning，其它条目照常执行。
"""
from __future__ import annotations

from pathlib import Path

from loguru import logger

from ..users.document import get_user_skipped_tasks

__all__ = ["collect_exclusions"]


def collect_exclusions(
    usernames: list[str] | tuple[str, ...] | None,
    script_ids: list[str] | tuple[str, ...] | None,
    users_dir: Path | None = None,
) -> dict[str, tuple[str, ...]]:
    """按本次勾选的脚本收集「用户声明跳过」的组合；只保留与脚本交集。"""
    targets = {str(script_id) for script_id in (script_ids or ()) if script_id}
    if not targets:
        return {}
    exclusions: dict[str, tuple[str, ...]] = {}
    for raw_name in usernames or ():
        username = str(raw_name or "")
        if not username:
            continue
        try:
            skipped = get_user_skipped_tasks(username, users_dir)
        except Exception as exc:  # noqa: BLE001 - 读资料失败不能拖垮批量启动
            logger.warning(
                f"读取用户跳过任务失败，本次按未跳过处理: {username}: {exc}")
            continue
        # 悬空 id（任务已下架或不在本次勾选内）不进快照。
        matched = tuple(sorted(skipped & targets))
        if matched:
            exclusions[username] = matched
    return exclusions
