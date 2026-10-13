"""停机清理本轮已废弃的锁；不迁移、不清理用户数据或当前占用锁。"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from lvjiang.core.access import AccessDeniedError, Lease  # noqa: E402
from lvjiang.core.paths import session_directory  # noqa: E402


def cleanup(config: Path, *, apply: bool = False) -> int:
    session = session_directory(config.resolve())
    primary = Lease(session / ".locks/configuration.lock")
    if not primary.acquire():
        raise AccessDeniedError("主节点仍在运行，请退出所有律匠节点后再清理")
    try:
        candidates = [session / name for name in (
            "config.lock", "session.json.lock", "interface.json.lock", "batch.json.lock",
            "yysls/.lock", "agent/.lock", "agent/generation.lock",
            "users/.session-v2.migration.lock", "users/.user-storage-v1.migration.lock",
        )]
        old = session / "users/.locks"
        if old.is_dir() and not old.is_symlink():
            candidates.extend(old.glob("*.execution.lock"))
        current = session / "users/.lock"
        if current.is_dir() and not current.is_symlink():
            candidates.extend(path for path in current.glob("*.json.lock"))
        # 出装搭配的旧侧车位于 local，不读取/改写搭配本体。
        from lvjiang.core.paths import get_config_local_dir
        gears = get_config_local_dir(config.resolve()) / "yysls/gear_sets/.locks"
        if gears.is_dir() and not gears.is_symlink():
            candidates.extend(gears.glob("*.lock"))
        candidates = [path for path in candidates if path.is_file()
                      and not any(parent.is_symlink() for parent in (path, *path.parents))]
        probes = list(candidates)
        if current.is_dir():
            probes.extend(current.glob("*.execution.lock"))
        for path in probes:
            lease = Lease(path)
            if not lease.acquire():
                raise AccessDeniedError("仍有用户任务或旧存储操作占用，请退出所有节点后再清理")
            lease.release()
        if apply:
            for path in candidates:
                path.unlink()
        return len(candidates)
    finally:
        primary.release()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True, help="明确指定客户端配置根目录")
    parser.add_argument("--apply", action="store_true", help="实际删除已废弃锁；默认只盘点数量")
    args = parser.parse_args()
    print(f"{'已删除' if args.apply else '可清理'} {cleanup(args.config, apply=args.apply)} 个废弃锁文件；用户数据与当前占用锁不变")


if __name__ == "__main__":
    main()
