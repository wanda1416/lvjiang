"""诊断帧按会话落盘到 logs/diagnostics/<timestamp>/（异步单写线程，不阻塞 UI）。"""

from __future__ import annotations

import json
import queue
import re
import threading
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger

from .. import constants
from .task_diagnostics import DiagnosticFrame

_SAFE_SOURCE_RE = re.compile(r"[^0-9A-Za-z._-]+")


def parse_frame_metadata(frame: DiagnosticFrame) -> dict[str, Any]:
    """解析 DiagnosticFrame.metadata JSON；损坏时返回空字典。"""
    try:
        data = json.loads(frame.metadata.decode("utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def frame_identity(
    frame: DiagnosticFrame, meta: dict[str, Any] | None = None,
) -> tuple[str, int]:
    """返回帧的唯一标识 (capture_scope, frame_seq)。"""
    info = parse_frame_metadata(frame) if meta is None else meta
    scope = str(info.get("capture_scope") or "")
    try:
        seq = int(info.get("frame_seq", 0))
    except (TypeError, ValueError):
        seq = 0
    return (scope, seq)


def default_diagnostics_dump_root() -> Path:
    return constants.PROJECT_ROOT / "logs" / "diagnostics"


def _allocate_session_dir(base_dir: Path) -> Path:
    base_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
    candidate = base_dir / stamp
    suffix = 1
    while candidate.exists():
        candidate = base_dir / f"{stamp}_{suffix}"
        suffix += 1
    candidate.mkdir(parents=True, exist_ok=False)
    return candidate


def _safe_source_token(source: str) -> str:
    cleaned = _SAFE_SOURCE_RE.sub("_", source.strip()).strip("._-")
    return (cleaned or "frame")[:48]


class DiagnosticFrameDumper:
    """将诊断帧（当前缓存 + 后续新帧）异步写入独立会话目录。

    每次 start() 生成一个新目录 ``logs/diagnostics/<timestamp>/``：
    - 每帧以原 WebP 字节落盘（``0001_seq0001_<source>.webp``），零重编码开销；
    - 元数据追加写入 ``frames.jsonl``（UTF-8 / LF）。
    """

    def __init__(self, base_dir: Path | None = None) -> None:
        self._base_dir = base_dir
        self._lock = threading.Lock()
        self._queue: queue.Queue[DiagnosticFrame | None] | None = None
        self._worker: threading.Thread | None = None
        self._seen: set[tuple[str, int]] = set()
        self._session_dir: Path | None = None
        self._active = False
        self._written = 0
        self._errors = 0

    @property
    def active(self) -> bool:
        with self._lock:
            return self._active

    @property
    def session_dir(self) -> Path | None:
        with self._lock:
            return self._session_dir

    @property
    def written_count(self) -> int:
        with self._lock:
            return self._written

    @property
    def error_count(self) -> int:
        with self._lock:
            return self._errors

    def start(self, base_dir: Path | None = None) -> Path:
        """开启一次新的落盘会话并创建新目录；若已开启则先停止旧会话。"""
        if self.active:
            self.stop()
        root = base_dir or self._base_dir or default_diagnostics_dump_root()
        session_dir = _allocate_session_dir(root)
        q: queue.Queue[DiagnosticFrame | None] = queue.Queue()
        worker = threading.Thread(
            target=self._write_loop,
            args=(session_dir, q),
            name="task-diagnostic-dumper",
            daemon=True,
        )
        with self._lock:
            self._session_dir = session_dir
            self._queue = q
            self._worker = worker
            self._seen = set()
            self._written = 0
            self._errors = 0
            self._active = True
        worker.start()
        return session_dir

    def enqueue_frame(self, frame: DiagnosticFrame) -> bool:
        """去重入队一帧；未开启或已写入过返回 False。"""
        key = frame_identity(frame)
        with self._lock:
            if not self._active or self._queue is None or key in self._seen:
                return False
            self._seen.add(key)
            self._queue.put(frame)
            return True

    def enqueue_many(self, frames: Iterable[DiagnosticFrame]) -> int:
        added = 0
        for frame in frames:
            if self.enqueue_frame(frame):
                added += 1
        return added

    def stop(self, *, timeout: float = 2.0) -> None:
        """结束当前落盘会话并等待写线程刷盘退出。"""
        with self._lock:
            if not self._active:
                return
            self._active = False
            q = self._queue
            worker = self._worker
            self._queue = None
            self._worker = None
        if q is not None:
            q.put(None)
        if worker is not None and worker.is_alive():
            worker.join(timeout=timeout)

    def _write_loop(
        self, session_dir: Path, q: queue.Queue[DiagnosticFrame | None],
    ) -> None:
        index_path = session_dir / "frames.jsonl"
        index = 0
        while True:
            frame = q.get()
            if frame is None:
                return
            index += 1
            meta = parse_frame_metadata(frame)
            seq = int(meta.get("frame_seq", 0) or 0)
            source_token = _safe_source_token(str(meta.get("source") or "frame"))
            filename = f"{index:04d}_seq{seq:04d}_{source_token}.webp"
            record = {
                "index": index,
                "filename": filename,
                **meta,
                "nbytes": frame.nbytes,
                "dumped_at": datetime.now().isoformat(timespec="milliseconds"),
            }
            try:
                (session_dir / filename).write_bytes(frame.webp)
                with index_path.open("a", encoding="utf-8", newline="\n") as fp:
                    fp.write(json.dumps(record, ensure_ascii=False) + "\n")
                with self._lock:
                    self._written += 1
            except OSError as exc:
                logger.warning("诊断帧落盘失败: {}", exc)
                with self._lock:
                    self._errors += 1
