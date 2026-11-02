"""文件原子写入的共享实现。

session.json / users/*.json / *.lvtrace 等场景都需要「先写同目录临时
文件、成功后 os.replace 覆盖目标」的原子写模式，避免进程崩溃或写入
中途失败时留下半截文件。此前这套骨架在三处（session.py / users.py /
input_trace.py）各自独立实现，容易在未来加固（重试、fsync）时漏改
一处；收敛到这里做唯一实现，各调用方只保留自己的 prefix/编码差异。
"""
from __future__ import annotations

import hashlib
import os
import tempfile
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - 仅类型标注
    from fasteners import InterProcessLock

# 这个模块被「裸 Python」构建脚本导入：`scripts/build_android_preset.py` 在
# 没有安装项目依赖的构建机上 import `core.system_preset` → `core.fs_util`。
# 因此第三方依赖一律在真正加锁处再导入，模块顶层只允许标准库。

#: 等待其它实例释放写锁的上限（秒）。没有上限会让 UI 线程无声卡死，
#: 所以宁愿超时抛错，让调用方给出「另一个实例正在写入」这类可操作提示。
DEFAULT_LOCK_TIMEOUT = 30.0

_LOCKS: dict[str, tuple[threading.RLock, InterProcessLock]] = {}
_LOCKS_GUARD = threading.Lock()
_HELD_DEPTH = threading.local()


class InterprocessLockTimeout(RuntimeError):
    """跨进程写锁在超时内没有拿到。"""


def _lock_entry(
    key: str, suffix: str,
) -> tuple[threading.RLock, InterProcessLock]:
    # 缓存键必须带 suffix：同一路径换用途是两把不同的锁，只按路径缓存会让
    # 后一个调用方静默复用前一个的锁文件，进程内外的互斥范围就不一致了。
    cache_key = f"{key}|{suffix}"
    with _LOCKS_GUARD:
        entry = _LOCKS.get(cache_key)
        if entry is None:
            from fasteners import InterProcessLock
            path = lock_file(suffix, key)
            path.parent.mkdir(parents=True, exist_ok=True)
            entry = (threading.RLock(), InterProcessLock(str(path)))
            _LOCKS[cache_key] = entry
        return entry


def lock_root() -> Path:
    """所有运行时锁的唯一存放处：``config/session/.locks``。

    锁集中在这一处，不再往各数据目录旁散落 ``.locks``：退出所有节点后整目录
    删掉即可重置，下次按需重建；也不会污染用户数据目录与受版本控制的
    ``config/local``。
    """
    from ..constants import SESSION_CONFIG_DIR
    return SESSION_CONFIG_DIR / ".locks"


def lock_file(kind: str, key: str, *, root: Path | None = None) -> Path:
    """``<锁根>/<类型>_<摘要前8位>.lock``。

    ``kind`` 是锁的用途（instance / state / install / profile / document /
    execution / agent …），``key`` 是稳定身份（目标绝对路径或用户名），
    摘要只取前 8 位仅为可读——碰撞的后果是两个不同类型/目标共用一把锁而
    互相等待，不会让该互斥的地方失去互斥。``root`` 显式指定锁根，供针对
    其它安装目录的停机工具使用。
    """
    base = root if root is not None else lock_root()
    digest = hashlib.sha256(key.encode()).hexdigest()[:8]
    return base / f"{kind}_{digest}.lock"


def _acquire_process_lock(
    lock: InterProcessLock, target: Path, timeout: float | None,
) -> None:
    if lock.acquire(blocking=True, timeout=timeout):
        return
    # fasteners 获取失败时不会关闭它刚打开的文件句柄，长跑会累积句柄。
    lockfile = getattr(lock, "lockfile", None)
    if lockfile is not None:
        try:
            lockfile.close()
        finally:
            lock.lockfile = None
    raise InterprocessLockTimeout(
        f"等待其他实例释放写锁超时（{timeout:g} 秒）：{target}；"
        "请确认另一个实例没有卡在保存上")


def _release_process_lock(lock: InterProcessLock) -> None:
    """释放跨进程锁；失败不掩盖正在传播的业务异常。

    本模块会被构建宿主（生成预置配置）导入，那边不允许拉进 loguru/yaml 等
    运行时依赖，所以这里只吞掉异常、不写日志。
    """
    try:
        lock.release()
    except Exception:  # noqa: BLE001 - 释放失败不能顶掉正在传播的业务异常
        pass


@contextmanager
def interprocess_lock(
    target: Path,
    *,
    suffix: str = "state",
    timeout: float | None = DEFAULT_LOCK_TIMEOUT,
):
    """串行化同一目标的跨进程读改写。

    锁文件统一放在 ``config/session/.locks/``，按「用途 + 目标绝对路径摘要」
    命名，同一目标的多实例共享一把。同一线程嵌套获取直接放行：fasteners 的
    锁不可重入，而写入链路上会出现「批处理已持锁 → 单文件写入再取锁」的情况。

    跨进程锁永远在 ``configuration_lock`` 之后获取：全仓库统一「进程内线程锁
    → 跨进程文件锁」的顺序，反过来会和 ``batch_changes`` / ``checked_write`` /
    ``builds`` 等既有调用方互相等待而死锁。
    """
    # 先解析成绝对路径：相对目录（如 for_single_directory("config")）直接取
    # parent 会把锁目录落到进程工作目录上。
    target = target.resolve()
    key = os.path.normcase(str(target))
    depths = getattr(_HELD_DEPTH, "depths", None)
    if depths is None:
        depths = _HELD_DEPTH.depths = {}
    if depths.get(key):
        depths[key] += 1
        try:
            yield
        finally:
            depths[key] -= 1
        return
    thread_lock, process_lock = _lock_entry(key, suffix)
    with thread_lock:
        _acquire_process_lock(process_lock, target, timeout)
        depths[key] = 1
        try:
            yield
        finally:
            depths.pop(key, None)
            _release_process_lock(process_lock)


def dated_output_dir(root: Path, started_at: datetime) -> Path:
    """按启动日期定位产出目录；不搬迁旧文件，也不执行清理。"""
    return root / started_at.strftime("%Y-%m") / started_at.strftime("%d")


def task_result_path(
    username: str, task_id: str, started_at: datetime, *,
    task_run_id: str = "", interrupted: bool = False,
) -> Path:
    """PC 与设备端共用的任务结果路径，按启动用户及启动日期归档。"""
    from ..constants import OUTPUT_DIR

    directory = dated_output_dir(OUTPUT_DIR / username, started_at)
    stamp = started_at.strftime("%Y%m%d_%H%M%S_%f")
    run_suffix = f"_{task_run_id}" if task_run_id else ""
    suffix = "_interrupted" if interrupted else ""
    return directory / f"{task_id}_{stamp}{run_suffix}{suffix}.json"


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
    """同 atomic_write_bytes，但走文本模式写入。

    换行固定为 LF：配置与脚本在工作区的字节必须与仓库 ``.gitattributes``
    的 ``eol=lf`` 一致，否则 Windows 上每次保存都会翻成 CRLF——它不会在 git
    状态里显形（clean filter 会归一化），却会改变预置包摘要和跨平台产物。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=prefix, suffix=suffix)
    tmp_path = Path(tmp_name)
    try:
        try:
            with os.fdopen(fd, "w", encoding=encoding, newline="\n") as stream:
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


__all__ = [
    "DEFAULT_LOCK_TIMEOUT",
    "InterprocessLockTimeout",
    "atomic_write_bytes",
    "atomic_write_text",
    "dated_output_dir",
    "interprocess_lock",
]
