"""保存前的乐观并发检查；只读取本次要写的文件。"""
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

Baseline = tuple[bytes | None, bytes | None]
_handler: Callable[[str], bool] | None = None


class ConfigWriteConflict(ValueError):
    pass


def set_conflict_handler(handler: Callable[[str], bool] | None) -> None:
    global _handler
    _handler = handler


def file_baseline(resolver, rel_path: str) -> Baseline:
    def read(root: Path):
        path = root / rel_path
        return path.read_bytes() if path.is_file() else None
    return read(resolver.system_dir), read(resolver.local_dir)


def check_conflicts(resolver, baselines: dict[str, Baseline]) -> dict[str, Baseline]:
    """弹窗不持锁；返回确认后的基线，写入锁内必须再次核对。"""
    current = {name: file_baseline(resolver, name) for name in baselines}
    changed = [name for name in baselines if baselines[name] != current[name]]
    if changed and (_handler is None or not _handler("\n".join(changed))):
        raise ConfigWriteConflict("磁盘配置已更新，请刷新后重试；本次修改尚未保存")
    return current


def checked_write(resolver, baselines: dict[str, Baseline], write):
    confirmed = check_conflicts(resolver, baselines)
    from .content import config_write_lock, configuration_lock
    # 弹窗已在锁外完成；这里按全仓库统一的「线程锁 → 文件锁」顺序写入，
    # 顺序反过来会和 _configuration_write 互相等待而永久死锁。
    with configuration_lock(resolver.config_dir), config_write_lock(
            resolver.config_dir, resolver.local_dir):
        if any(file_baseline(resolver, name) != expected for name, expected in confirmed.items()):
            raise ConfigWriteConflict("确认期间磁盘配置再次更新，请重新保存")
        if hasattr(resolver, "_read_baselines"):
            from .versioning import spec_for
            resolver._read_baselines.update({
                name: baseline for name, baseline in confirmed.items()
                if (spec := spec_for(name)) is not None and spec.has_content_version})
        return write()
