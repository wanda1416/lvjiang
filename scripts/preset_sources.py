"""确定官方预置的输入清单：只取 git 索引里的文件，不信任工作区目录内容。

``config/system`` 在开发模式下也是编辑器的可写目录，运行时会产生
``_editor_run.wf``、``_testwf/`` 之类的草稿；``.gitignore`` 已经忽略它们，但
``read_tree`` 按磁盘打包会把它们带进预置，导致同一提交在不同机器上摘要不同。
这里以 git 索引为准，把「什么算官方配置」交给版本控制回答。
"""
from __future__ import annotations

import subprocess
from pathlib import Path


def tracked_files(root: Path, relative: str) -> list[str] | None:
    """返回 ``relative/`` 下纳入 git 索引的相对路径（POSIX 分隔）。

    不是 git 工作区（没有 ``.git``、没有 git 命令或命令失败）时返回 ``None``，
    由调用方决定回退策略；返回空列表表示该目录确实没有跟踪文件。
    """
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z", "--", relative],
            capture_output=True,
            check=False,
        )
    except OSError:
        return None
    if result.returncode != 0:
        return None
    prefix = relative.rstrip("/") + "/"
    paths: list[str] = []
    for raw in result.stdout.split(b"\0"):
        if not raw:
            continue
        path = raw.decode("utf-8")
        if path.startswith(prefix):
            paths.append(path[len(prefix):])
    return paths


def report_excluded(relative: str, present: list[str], include: list[str]) -> list[str]:
    """返回并打印被过滤掉的未跟踪文件，便于确认排除了什么。"""
    excluded = sorted(set(present) - set(include))
    if excluded:
        listed = "、".join(excluded[:10])
        more = f" 等 {len(excluded)} 个" if len(excluded) > 10 else ""
        print(f"已排除 {relative} 下未纳入 git 的文件：{listed}{more}")
    return excluded
