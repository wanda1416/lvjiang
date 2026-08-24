"""文件原子写入的共享实现。

session.json / users/*.json / *.lvtrace 等场景都需要「先写同目录临时
文件、成功后 os.replace 覆盖目标」的原子写模式，避免进程崩溃或写入
中途失败时留下半截文件。此前这套骨架在三处（session.py / users.py /
input_trace.py）各自独立实现，容易在未来加固（重试、fsync）时漏改
一处；收敛到这里做唯一实现，各调用方只保留自己的 prefix/编码差异。
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path


def atomic_write_bytes(
    path: Path,
    data: bytes,
    *,
    prefix: str,
    suffix: str = ".tmp",
    fsync: bool = False,
) -> None:
    """在 path 所在目录写临时文件，成功后原子替换为 path（二进制内容）。

    临时文件与目标同目录，确保 os.replace 落在同一文件系统内、是原子
    操作。fsync=True 用于对崩溃更敏感的场景（如 input_trace 的双文件
    保存事务），在 replace 前显式落盘。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=prefix, suffix=suffix)
    tmp_path = Path(tmp_name)
    try:
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                if fsync:
                    stream.flush()
                    os.fsync(stream.fileno())
        except BaseException:
            # os.fdopen 失败时 fd 未被接管，需手动关闭；成功后异常已由
            # with 语句关闭 stream，这里的 close 只覆盖前一种情况。
            try:
                os.close(fd)
            except OSError:
                pass
            raise
        os.replace(tmp_path, path)
    except BaseException:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def atomic_write_text(
    path: Path,
    text: str,
    *,
    prefix: str,
    suffix: str = ".tmp",
    encoding: str = "utf-8",
) -> None:
    """同 atomic_write_bytes，但走文本模式写入（保留平台默认换行转换）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=prefix, suffix=suffix)
    tmp_path = Path(tmp_name)
    try:
        try:
            with os.fdopen(fd, "w", encoding=encoding) as stream:
                stream.write(text)
        except BaseException:
            try:
                os.close(fd)
            except OSError:
                pass
            raise
        os.replace(tmp_path, path)
    except BaseException:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise


__all__ = ["atomic_write_bytes", "atomic_write_text"]
