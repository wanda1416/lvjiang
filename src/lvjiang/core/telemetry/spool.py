"""事件本地缓冲：两段式 NDJSON，无需文件锁。

绝不能用 SessionStore/session.json：每次写都是「抢跨进程锁 → 重读整盘 →
全量 dumps → 原子替换」，一次调律几百次 roll 等于把整个 session.json
重写几百遍，还会把统计数据混进用户可能打包发给作者的文件。

机制：内存里攒够 ``FLUSH_EVERY`` 条或调用方显式 ``flush()`` 时，写一个
完整的临时文件到 ``pending/``，再用 ``os.replace()``（原子操作）把它
移进 ``ready/``。上报者只读 ``ready/``，永远不会读到半截文件——这就是
两段式的意义，不需要任何文件锁。

容量超限时丢最旧的 ready 文件（新数据反映当前版本/当前赛季，价值更高），
丢弃计数写进 identity.json 的 ``dropped_events``，下次批次的信封里带上，
服务端才知道这份样本被截断过。
"""
from __future__ import annotations

import json
import os
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path

from loguru import logger

from .paths import spool_dir
from .schema import ValidatedEvent

FLUSH_EVERY = 50
MAX_TOTAL_BYTES = 4 * 1024 * 1024
MAX_READY_FILES = 16


@dataclass(frozen=True)
class SpoolChunk:
    path: Path
    events: tuple[dict, ...]


class EventSpool:
    """单进程内的事件缓冲。模块级单例，见文件末尾 :func:`append` 等门面函数。"""

    def __init__(self) -> None:
        self._buffer: list[str] = []
        self._lock = threading.Lock()

    def append(self, event: ValidatedEvent) -> None:
        if not isinstance(event, ValidatedEvent):
            raise TypeError(f"spool 只接受 ValidatedEvent，收到 {type(event)!r}")
        line = json.dumps(
            {"schema": event.schema_name, "version": event.schema_version,
             **dict(event.values)},
            ensure_ascii=False)
        should_flush = False
        with self._lock:
            self._buffer.append(line)
            if len(self._buffer) >= FLUSH_EVERY:
                should_flush = True
        if should_flush:
            self.flush()

    def flush(self) -> None:
        """把内存里攒的事件整体落盘并直接封口进 ``ready/``。"""
        with self._lock:
            if not self._buffer:
                return
            lines, self._buffer = self._buffer, []
        ready = spool_dir() / "ready"
        ready.mkdir(parents=True, exist_ok=True)
        name = f"events-{os.getpid()}-{uuid.uuid4().hex[:8]}.ndjson"
        tmp = spool_dir() / "pending" / f".{name}.tmp"
        tmp.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
        os.replace(tmp, ready / name)
        self._enforce_limits()

    @staticmethod
    def _enforce_limits() -> None:
        ready = spool_dir() / "ready"
        if not ready.is_dir():
            return
        files = sorted(ready.glob("*.ndjson"), key=lambda p: p.stat().st_mtime)
        total = sum(f.stat().st_size for f in files)
        dropped = 0
        while (total > MAX_TOTAL_BYTES or len(files) > MAX_READY_FILES) and files:
            victim = files.pop(0)
            try:
                size = victim.stat().st_size
                dropped += _count_lines(victim)
                victim.unlink()
                total -= size
            except OSError:
                pass
        if dropped:
            _bump_dropped(dropped)
            logger.warning(f"[telemetry] 本地缓冲超限，丢弃 {dropped} 条最旧事件")

    def take_batches(self, max_batches: int) -> list[SpoolChunk]:
        """读取最多 ``max_batches`` 个已封口的批次，供上报者使用。"""
        ready = spool_dir() / "ready"
        if not ready.is_dir():
            return []
        files = sorted(ready.glob("*.ndjson"), key=lambda p: p.stat().st_mtime)
        chunks = []
        for path in files[:max_batches]:
            events = _read_batch_file(path)
            chunks.append(SpoolChunk(path=path, events=tuple(events)))
        return chunks

    @staticmethod
    def drop(chunk: SpoolChunk) -> None:
        """上报成功后删除该批次文件。"""
        try:
            chunk.path.unlink()
        except OSError:
            pass

    def purge(self) -> None:
        """清空本地缓冲（关闭统计/重置标识时用），不影响 identity.json。"""
        import shutil
        with self._lock:
            self._buffer = []
        d = spool_dir()
        if d.exists():
            shutil.rmtree(d, ignore_errors=True)

    def pending_event_count(self) -> int:
        """已封口但尚未上报的事件总数（不含尚在内存里未 flush 的部分）。"""
        ready = spool_dir() / "ready"
        if not ready.is_dir():
            return 0
        return sum(_count_lines(f) for f in ready.glob("*.ndjson"))


def _count_lines(path: Path) -> int:
    try:
        return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
    except OSError:
        return 0


def _read_batch_file(path: Path) -> list[dict]:
    """逐行解析；最后一行解析失败视为进程被杀导致的半行，丢弃而非报错。"""
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return []
    lines = [ln for ln in raw.splitlines() if ln.strip()]
    events: list[dict] = []
    for i, line in enumerate(lines):
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            if i == len(lines) - 1:
                continue  # 最后一行损坏：静默丢弃
            logger.warning(f"[telemetry] 缓冲文件中段损坏，跳过一行: {path.name}")
    return events


def _bump_dropped(n: int) -> None:
    from . import identity as identity_mod
    identity_mod.bump_dropped_events(n)


# ─── 模块级单例门面 ──────────────────────────────────────

_SPOOL = EventSpool()


def append(event: ValidatedEvent) -> None:
    _SPOOL.append(event)


def flush() -> None:
    _SPOOL.flush()


def take_batches(max_batches: int) -> list[SpoolChunk]:
    return _SPOOL.take_batches(max_batches)


def drop(chunk: SpoolChunk) -> None:
    _SPOOL.drop(chunk)


def purge() -> None:
    _SPOOL.purge()


def pending_event_count() -> int:
    return _SPOOL.pending_event_count()
