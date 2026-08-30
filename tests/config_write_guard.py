"""Pytest 进程的项目配置写入门禁。

测试必须把所有可变配置指向 ``tmp_path``。这层门禁位于业务存储 API
之下，直接监听 Python audit 事件，因此即使新测试绕过 SessionStore、
直接使用 Path/open/os.replace，也不能改写工作区里的真实 ``config/``。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


class ProjectConfigWriteBlocked(RuntimeError):
    """测试试图修改工作区真实 config 目录。"""


_WRITE_FLAGS = (
    os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND
)

# event -> (路径参数位置, 对应 dir_fd 参数位置)。
# dir_fd 不能忽略：shutil.rmtree 会相对已打开的临时目录传入诸如
# ``config`` 的名称，若一律按 cwd 解析，会误判为项目根目录的 config。
_MUTATING_EVENTS: dict[str, tuple[tuple[int, int | None], ...]] = {
    "os.remove": ((0, 1),),
    "os.rmdir": ((0, 1),),
    "os.mkdir": ((0, 2),),
    "os.rename": ((0, 2), (1, 3)),
    "os.replace": ((0, 2), (1, 3)),
    "os.chmod": ((0, 2),),
    "os.chown": ((0, 3),),
    "os.truncate": ((0, None),),
    "os.utime": ((0, 3),),
    "os.link": ((0, 2), (1, 3)),
    "os.symlink": ((1, 2),),
}

_installed_roots: set[Path] = set()


def _as_path(value, dir_fd=None) -> Path | None:
    if isinstance(value, int) or value is None:
        return None
    try:
        raw = os.fsdecode(value)
    except TypeError:
        return None
    path = Path(raw)
    if not path.is_absolute():
        base = Path.cwd()
        if isinstance(dir_fd, int) and dir_fd >= 0:
            try:
                base = Path(f"/proc/self/fd/{dir_fd}").resolve(strict=True)
            except OSError:
                # 非 Linux 或 fd 已关闭时无法可靠还原基准目录；回退到 cwd
                # 与不带 dir_fd 的 Python 路径语义一致。
                pass
        path = base / path
    try:
        return path.resolve(strict=False)
    except OSError:
        return Path(os.path.abspath(path))


def _inside(path_value, protected_root: Path, dir_fd=None) -> bool:
    path = _as_path(path_value, dir_fd)
    return path is not None and (
        path == protected_root or protected_root in path.parents
    )


def _open_is_write(mode, flags) -> bool:
    if isinstance(mode, str) and any(char in mode for char in "wax+"):
        return True
    return isinstance(flags, int) and bool(flags & _WRITE_FLAGS)


def _blocked(event: str, path_value, protected_root: Path, dir_fd=None) -> None:
    path = _as_path(path_value, dir_fd)
    raise ProjectConfigWriteBlocked(
        "测试禁止修改项目真实配置目录："
        f"event={event}, path={path}, protected_root={protected_root}。"
        "请将配置路径 monkeypatch 到 tmp_path。"
    )


def install_project_config_write_guard(config_root: Path) -> None:
    """安装进程级门禁；同一路径重复安装无效果。"""
    protected_root = config_root.resolve(strict=False)
    if protected_root in _installed_roots:
        return
    _installed_roots.add(protected_root)

    def _audit(event: str, args: tuple) -> None:
        if event == "open" and args:
            mode = args[1] if len(args) > 1 else None
            flags = args[2] if len(args) > 2 else None
            if _open_is_write(mode, flags) and _inside(args[0], protected_root):
                _blocked(event, args[0], protected_root)
            return

        # sqlite 默认连接可创建数据库、journal 与 WAL；无法从 connect 事件
        # 判断后续是否只读，因此真实 config 下的数据库连接一律禁止。
        if event == "sqlite3.connect" and args:
            if _inside(args[0], protected_root):
                _blocked(event, args[0], protected_root)
            return

        indexes = _MUTATING_EVENTS.get(event)
        if indexes is None:
            return
        for path_index, dir_fd_index in indexes:
            dir_fd = (
                args[dir_fd_index]
                if dir_fd_index is not None and dir_fd_index < len(args)
                else None
            )
            if path_index < len(args) and _inside(
                args[path_index], protected_root, dir_fd,
            ):
                _blocked(event, args[path_index], protected_root, dir_fd)

    sys.addaudithook(_audit)
