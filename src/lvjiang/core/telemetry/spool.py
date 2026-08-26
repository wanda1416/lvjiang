"""事件本地缓冲：两段式 NDJSON，无需文件锁。

绝不能用 SessionStore/session.json：每次写都是「抢跨进程锁 → 重读整盘 →
全量 dumps → 原子替换」，一次调律几百次 roll 等于把整个 session.json
重写几百遍，还会把统计数据混进用户可能打包发给作者的文件。

机制：**每条事件产出即刻追加落盘**到 ``pending/`` 里的当前 open 批次
文件，攒够 ``FLUSH_EVERY`` 条后用 ``os.replace()``（原子操作）封口移进
``ready/``。上报者只读 ``ready/``，永远不会读到半截文件——这就是两段式的
意义，不需要任何文件锁。

「产出即落盘」不是性能取舍而是正确性要求：早先的实现把事件攒在内存里、
凑够一批才写文件，于是不足一批的尾巴会随进程退出一起消失。调律一次几十
到几百条，尾巴丢失意味着每次运行的最后一段数据系统性缺失，而且缺的恰好
是"这件装备最后怎么处理的"。落盘与上报必须解耦：落盘要即时，上报可以攒。

容量超限时丢最旧的 ready 文件（新数据反映当前版本/当前赛季，价值更高），
只打一条 warning。**不上报丢弃计数**：丢弃与事件内容无关（按文件 mtime 删），
对按 roll 估计的概率分布不产生偏倚，只损失精度，而精度已经由样本数 n 表达。
知道丢了多少不会让任何结论更准，属于本地排查信息，不该占一个上报字段。
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
        self._lock = threading.Lock()
        self._open_name: str | None = None   # pending/ 里正在追加的批次文件名
        self._open_count = 0                 # 该文件已写入的事件条数

    def _open_path(self) -> Path:
        """当前可追加的批次文件；不存在时新建一个（调用方持锁）。"""
        pending = spool_dir() / "pending"
        pending.mkdir(parents=True, exist_ok=True)
        if self._open_name is None:
            self._open_name = f"events-{os.getpid()}-{uuid.uuid4().hex[:8]}.ndjson"
            self._open_count = 0
        return pending / self._open_name

    def append(self, event: ValidatedEvent) -> None:
        if not isinstance(event, ValidatedEvent):
            raise TypeError(f"spool 只接受 ValidatedEvent，收到 {type(event)!r}")
        line = json.dumps(
            {"schema": event.schema_name, "version": event.schema_version,
             **dict(event.values)},
            ensure_ascii=False)
        with self._lock:
            path = self._open_path()
            with path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
            self._open_count += 1
            should_seal = self._open_count >= FLUSH_EVERY
        if should_seal:
            self.flush()

    def flush(self) -> None:
        """把当前 open 批次封口移进 ``ready/``，使其可被上报。

        事件早已在 append 时落盘，这里只做原子改名——所以即使 flush 从未
        被调用（进程被杀），数据也还在 ``pending/`` 里，下次启动会被
        ``_adopt_orphans()`` 接管，不会丢。
        """
        with self._lock:
            name, self._open_name, self._open_count = self._open_name, None, 0
            if name is None:
                return
            src = spool_dir() / "pending" / name
            if not src.exists():
                return
            ready = spool_dir() / "ready"
            ready.mkdir(parents=True, exist_ok=True)
            os.replace(src, ready / name)
        self._enforce_limits()

    def _adopt_orphans(self) -> None:
        """把上次运行遗留在 ``pending/`` 里的批次收进 ``ready/``。

        进程被杀时当前 open 文件停在 pending/，没有任何人会再追加它。
        启动时认领一次，这些事件就不会因为"没攒够一批"而永远滞留。
        自己这轮正在写的文件除外。
        """
        pending = spool_dir() / "pending"
        if not pending.is_dir():
            return
        ready = spool_dir() / "ready"
        for path in sorted(pending.glob("*.ndjson")):
            if path.name == self._open_name:
                continue
            try:
                ready.mkdir(parents=True, exist_ok=True)
                os.replace(path, ready / path.name)
            except OSError:
                pass

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
            logger.warning(f"[telemetry] 本地缓冲超限，丢弃 {dropped} 条最旧事件")

    def take_batches(self, max_batches: int) -> list[SpoolChunk]:
        """读取最多 ``max_batches`` 个已封口的批次，供上报者使用。"""
        self._adopt_orphans()
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
            self._open_name = None
            self._open_count = 0
        d = spool_dir()
        if d.exists():
            shutil.rmtree(d, ignore_errors=True)

    def pending_event_count(self) -> int:
        """本地尚未上报的事件总数（含未封口的当前批次——它也已经落盘了）。"""
        total = 0
        for sub in ("ready", "pending"):
            d = spool_dir() / sub
            if d.is_dir():
                total += sum(_count_lines(f) for f in d.glob("*.ndjson"))
        return total


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
