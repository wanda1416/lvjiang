"""二维码图片的缓存位置：系统临时目录优先，客户端 session 兜底。

二维码是"取到就能用、丢了能重下"的临时素材，不承载任何用户状态。写进
``config/session`` 会和用户、头像、历史这类真正需要持久化的数据混在一起，
既污染 session 目录，也容易被清理或随包同步的逻辑当成状态处理。因此默认
落在系统临时目录（Windows 下是每个用户各自的 ``%TEMP%``，不跨用户共享）；
临时目录不可写（受限环境、只读挂载、安卓上某些运行方式）时才退回
session，保证这两种情况下仍然可用。

读写都按"临时目录 → session"的顺序尝试：读命中第一个存在的文件即返回，
写成功第一个可写位置即返回。写全失败不算错误——调用方手上的图片字节仍然
可用于本次显示，下次打开重新下载即可。
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from loguru import logger

from .fs_util import atomic_write_bytes
from .paths import session_directory

#: 临时目录下的子目录名，与其它运行态文件区分开。
TEMP_SUBDIR = "lvjiang-qrcode"


def temp_cache_directory() -> Path:
    """临时缓存根目录。

    单独成函数是为了让测试能把它指到 ``tmp_path``：否则用例会写进真实的
    系统临时目录，既污染本机又会让上一次运行留下的文件影响下一次断言。
    """
    return Path(tempfile.gettempdir()) / TEMP_SUBDIR


def cache_paths(filename: str) -> tuple[Path, ...]:
    """按优先级返回该文件的全部候选缓存路径。"""
    return (temp_cache_directory() / filename, session_directory() / filename)


def cached_bytes(filename: str) -> bytes | None:
    """返回第一个存在的缓存文件内容；都没有返回 None。"""
    for path in cache_paths(filename):
        try:
            if path.is_file():
                return path.read_bytes()
        except OSError:
            # 单个位置读不了（权限/被占用）不影响换下一个位置再试。
            continue
    return None


def cache_path(filename: str) -> Path:
    """当前缓存文件的位置：已存在的那个，都不存在时给首选写入位置。"""
    candidates = cache_paths(filename)
    for path in candidates:
        if path.is_file():
            return path
    return candidates[0]


def write_cache(filename: str, data: bytes, *, prefix: str) -> Path | None:
    """按优先级写入，返回真正落盘的路径；全部失败返回 None。"""
    for path in cache_paths(filename):
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_bytes(path, data, prefix=prefix)
        except OSError as exc:
            logger.debug(f"[二维码] 无法写入缓存 {path}：{exc}")
            continue
        return path
    logger.warning("[二维码] 临时目录与 session 均不可写，本次仅在内存中使用")
    return None


__all__ = [
    "TEMP_SUBDIR",
    "cache_path",
    "cache_paths",
    "cached_bytes",
    "temp_cache_directory",
    "write_cache",
]
