"""清理历史遗留的锁文件；不碰用户数据，也不删正在使用的锁。

锁已统一到 ``config/session/.locks``，文件名统一为 ``<类型>_<摘要8位>.lock``。
本脚本删两类东西：旧版本散落在数据目录旁的侧车与旧 ``.lock`` 目录，以及锁根里
名字不符合当前规则的孤儿。每一把候选锁都会先试加锁，被占用就跳过——所以客户端
开着也能跑。

用法::

    python scripts/one_off/cleanup_obsolete_locks.py --config config
    python scripts/one_off/cleanup_obsolete_locks.py --config config --apply
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from lvjiang.core.access import Lease  # noqa: E402
from lvjiang.core.paths import get_config_local_dir, session_directory  # noqa: E402

#: 当前合法的锁文件名。锁根里不符合它的都是改名前的孤儿。
CURRENT_NAME = re.compile(
    r"^(instance|state|install|profile|document|execution|metadata|agent)_[0-9a-f]{8}\.lock$")

#: 相对于 session 目录的历史遗留（目录或文件）。
LEGACY_IN_SESSION = (
    ".lock", "users/.lock", "users/.locks", "yysls/.lock", "yysls/.locks",
    "history-transfer/.locks", "agent/.lock", "agent/generation.lock",
    "agent/.locks", "config.lock", "session.json.lock", "interface.json.lock",
    "batch.json.lock", "mcp.json.lvjiang.lock",
    "users/.session-v2.migration.lock", "users/.user-storage-v1.migration.lock",
)


def _files_under(path: Path) -> list[Path]:
    if path.is_symlink():
        return []
    if path.is_file():
        return [path]
    if path.is_dir():
        return [p for p in path.rglob("*") if p.is_file() and not p.is_symlink()]
    return []


def _candidates(session: Path) -> tuple[list[Path], list[Path]]:
    """返回 (待删文件, 待删空目录)。"""
    roots = [session / name for name in LEGACY_IN_SESSION]
    roots.append(get_config_local_dir(session.parent) / "yysls/gear_sets/.locks")
    files: list[Path] = []
    dirs: list[Path] = []
    for path in roots:
        if path.is_dir() and not path.is_symlink():
            dirs.append(path)
        files.extend(_files_under(path))
    lock_root = session / ".locks"
    if lock_root.is_dir() and not lock_root.is_symlink():
        files.extend(path for path in sorted(lock_root.glob("*.lock"))
                     if not CURRENT_NAME.match(path.name))
    return files, dirs


def cleanup(config: Path, *, apply: bool = False) -> tuple[int, int]:
    session = session_directory(config.resolve())
    files, dirs = _candidates(session)
    free: list[Path] = []
    skipped = 0
    for path in files:
        probe = Lease(path)
        if not probe.acquire():
            skipped += 1
            continue
        probe.release()
        free.append(path)
    if apply:
        # 先释放探锁再删：持有句柄时 Windows 不允许删除文件。
        for path in free:
            path.unlink()
        for directory in sorted(dirs, key=lambda p: len(p.parts), reverse=True):
            for nested in sorted((p for p in directory.rglob("*") if p.is_dir()),
                                 key=lambda p: len(p.parts), reverse=True):
                if not any(nested.iterdir()):
                    nested.rmdir()
            if not any(directory.iterdir()):
                directory.rmdir()
    return len(free), skipped


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True, help="客户端配置根目录")
    parser.add_argument("--apply", action="store_true", help="实际删除；默认只盘点")
    args = parser.parse_args()
    removed, skipped = cleanup(args.config, apply=args.apply)
    action = "已删除" if args.apply else "可清理"
    print(f"{action} {removed} 个遗留锁文件"
          + (f"，{skipped} 个正在使用已跳过" if skipped else ""))


if __name__ == "__main__":
    main()
