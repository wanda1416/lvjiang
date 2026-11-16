"""一次性迁移：剧情文案库目录 ``tools/auto_plot`` → ``yysls/auto_plot``。

自动剧情改为独立的燕云剧情插件（yysls_story）后，配置目录跟着插件走。用户
已经录入的选项文案不可再生成，所以搬而不是丢：旧目录里每个 ``*.json`` 原样
移到新目录；目标同名文件已存在时保留新文件、把旧文件留在原处并在日志里
点名——不覆盖任何人已经写好的内容。

迁移只跑一次：搬成功后旧目录变空并被删除，下次启动找不到旧路径即跳过，
因此不需要额外的版本标记，也不会二次迁移。
"""

from __future__ import annotations

import shutil
from pathlib import Path

from loguru import logger

#: 迁移前的位置（相对配置层根）。
LEGACY_DIR = "tools/auto_plot"
#: 迁移后的位置：插件命名空间。
TARGET_DIR = "yysls/auto_plot"


def _layer_root(layer: str) -> Path:
    from lvjiang.core.config.resolver import get_resolver

    resolver = get_resolver()
    return resolver.system_dir if layer == "system" else resolver.local_dir


def migrate_story_library() -> int:
    """把旧目录的文案库搬进插件目录，返回实际搬移的文件数。"""
    moved = 0
    for layer in ("system", "local"):
        legacy = _layer_root(layer) / LEGACY_DIR
        if not legacy.is_dir():
            continue
        target = _layer_root(layer) / TARGET_DIR
        for path in sorted(legacy.glob("*.json")):
            destination = target / path.name
            if destination.exists():
                logger.warning(
                    f"story: {layer} 层的 {path.name} 已存在于 {TARGET_DIR}，"
                    f"保留后者；旧文件仍留在 {LEGACY_DIR}")
                continue
            target.mkdir(parents=True, exist_ok=True)
            shutil.move(str(path), str(destination))
            moved += 1
            logger.info(f"story: 已把 {layer} 层 {path.name} 迁移到 {TARGET_DIR}")
        _drop_empty(legacy)
        _drop_empty(legacy.parent)
    if moved:
        logger.info(f"story: 剧情文案库迁移完成，共搬移 {moved} 个文件")
    return moved


def _drop_empty(directory: Path) -> None:
    """旧目录搬空后删掉，避免留下一个永远为空的 tools/。"""
    try:
        if directory.is_dir() and not any(directory.iterdir()):
            directory.rmdir()
    except OSError as exc:
        logger.warning(f"story: 清理空目录 {directory} 失败: {exc}")


__all__ = ["LEGACY_DIR", "TARGET_DIR", "migrate_story_library"]
