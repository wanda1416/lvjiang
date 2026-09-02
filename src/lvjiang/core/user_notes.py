"""Per-user sticky-note persistence.

Sticky notes are user-authored content, not Profile fields and not workflow
session state.  Each user therefore gets an independent
``users/{username}.notes.json`` file so workflow snapshot saves cannot overwrite
notes edited from the UI.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, TypeVar
from uuid import uuid4

from fasteners import InterProcessLock

from .fs_util import atomic_write_text

SCHEMA_VERSION = 1
MAX_NOTES = 200
MAX_TEXT_LENGTH = 500

_T = TypeVar("_T")
_LOCKS: dict[str, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def _thread_lock(path: Path) -> threading.RLock:
    key = str(path.resolve())
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(key, threading.RLock())


def _normalize_text(text: str) -> str:
    normalized = str(text).strip()
    if not normalized:
        raise ValueError("便利贴内容不能为空")
    if len(normalized) > MAX_TEXT_LENGTH:
        raise ValueError(f"便利贴内容不能超过 {MAX_TEXT_LENGTH} 个字符")
    return normalized


@dataclass(frozen=True)
class UserNote:
    """One short user-authored note."""

    id: str
    text: str
    created_at: str
    updated_at: str

    @classmethod
    def from_dict(cls, data: dict) -> "UserNote":
        if not isinstance(data, dict):
            raise ValueError("便利贴条目必须是对象")
        note_id = data.get("id")
        text = data.get("text")
        created_at = data.get("created_at")
        updated_at = data.get("updated_at")
        if (
            not isinstance(note_id, str)
            or not isinstance(text, str)
            or not isinstance(created_at, str)
            or not isinstance(updated_at, str)
        ):
            raise ValueError("便利贴条目字段无效")
        if not note_id or not text.strip():
            raise ValueError("便利贴 ID 和内容不能为空")
        return cls(note_id, text, created_at, updated_at)

    def to_dict(self) -> dict[str, str]:
        return {
            "id": self.id,
            "text": self.text,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class UserNotesRepository:
    """Atomic CRUD for one user's sticky-note file."""

    def __init__(self, username: str, users_dir: Path | None = None):
        from .user_config import is_valid_username

        if not is_valid_username(username):
            raise ValueError(f"非法用户名: {username!r}")
        if users_dir is None:
            from ..constants import USERS_DIR

            users_dir = USERS_DIR
        self.username = username
        self.path = users_dir / f"{username}.notes.json"
        self._lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        self._thread_lock = _thread_lock(self.path)

    @staticmethod
    def _empty_state() -> dict:
        return {"schema_version": SCHEMA_VERSION, "revision": 0, "notes": []}

    def _load_state(self) -> dict:
        if not self.path.exists():
            return self._empty_state()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"无法读取便利贴文件: {exc}") from exc
        if not isinstance(data, dict):
            raise ValueError("便利贴文件根节点必须是对象")
        version = data.get("schema_version", 0)
        if version != SCHEMA_VERSION:
            raise ValueError(f"不支持的便利贴文件版本: {version!r}")
        revision = data.get("revision")
        notes = data.get("notes")
        if not isinstance(revision, int) or revision < 0 or not isinstance(notes, list):
            raise ValueError("便利贴文件结构无效")
        # Parse every entry before returning.  A damaged file must never be
        # silently rewritten with only the entries that happened to parse.
        parsed = [UserNote.from_dict(item).to_dict() for item in notes]
        return {
            "schema_version": SCHEMA_VERSION,
            "revision": revision,
            "notes": parsed,
        }

    def _save_state(self, state: dict) -> None:
        payload = json.dumps(state, ensure_ascii=False, indent=2)
        atomic_write_text(self.path, payload, prefix=f".{self.path.stem}_")

    def _mutate(self, fn: Callable[[dict], _T]) -> _T:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._thread_lock:
            process_lock = InterProcessLock(str(self._lock_path))
            if not process_lock.acquire(blocking=True, timeout=5):
                raise TimeoutError("便利贴文件正在被其他进程修改")
            try:
                state = self._load_state()
                result = fn(state)
                state["revision"] += 1
                self._save_state(state)
                return result
            finally:
                process_lock.release()

    def list_notes(self) -> list[UserNote]:
        """Return notes in display order (newest first)."""
        with self._thread_lock:
            state = self._load_state()
        return [UserNote.from_dict(item) for item in state["notes"]]

    def add(self, text: str) -> UserNote:
        normalized = _normalize_text(text)

        def _add(state: dict) -> UserNote:
            if len(state["notes"]) >= MAX_NOTES:
                raise ValueError(f"每个用户最多保存 {MAX_NOTES} 条便利贴")
            now = _now_iso()
            note = UserNote(uuid4().hex, normalized, now, now)
            state["notes"].insert(0, note.to_dict())
            return note

        return self._mutate(_add)

    def update(self, note_id: str, text: str) -> UserNote:
        normalized = _normalize_text(text)

        def _update(state: dict) -> UserNote:
            for index, item in enumerate(state["notes"]):
                note = UserNote.from_dict(item)
                if note.id == note_id:
                    updated = UserNote(
                        note.id, normalized, note.created_at, _now_iso()
                    )
                    state["notes"][index] = updated.to_dict()
                    return updated
            raise KeyError(note_id)

        return self._mutate(_update)

    def delete(self, note_id: str) -> None:
        def _delete(state: dict) -> None:
            for index, item in enumerate(state["notes"]):
                if item.get("id") == note_id:
                    del state["notes"][index]
                    return
            raise KeyError(note_id)

        self._mutate(_delete)

    def delete_file(self) -> None:
        """Remove all sticky-note content for this user."""
        with self._thread_lock:
            if not self.path.exists():
                return
            process_lock = InterProcessLock(str(self._lock_path))
            if not process_lock.acquire(blocking=True, timeout=5):
                raise TimeoutError("便利贴文件正在被其他进程修改")
            try:
                self.path.unlink(missing_ok=True)
            finally:
                process_lock.release()


def delete_user_notes(username: str, users_dir: Path | None = None) -> None:
    """Delete a user's sticky-note file if it exists."""
    UserNotesRepository(username, users_dir).delete_file()


__all__ = [
    "MAX_NOTES",
    "MAX_TEXT_LENGTH",
    "SCHEMA_VERSION",
    "UserNote",
    "UserNotesRepository",
    "delete_user_notes",
]
