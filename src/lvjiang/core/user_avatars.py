"""Session-scoped user avatar asset storage.

Avatar pixels live in ``config/session/avatars``.  ``session.json`` only keeps
the safe basename referenced by each user, so the JSON stays small and shared
assets can be selected again from the avatar history.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

from .fs_util import atomic_write_bytes

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_AVATAR_NAME = re.compile(r"^avatar_[0-9a-f]{16}\.png$")


def is_safe_avatar_filename(filename: str | None) -> bool:
    """Return whether *filename* is one of our generated plain basenames."""
    return isinstance(filename, str) and bool(_AVATAR_NAME.fullmatch(filename))


class UserAvatarStore:
    """Content-addressed cropped PNG avatar library."""

    def __init__(self, avatars_dir: Path | str | None = None):
        if avatars_dir is None:
            from .. import constants
            avatars_dir = constants.AVATARS_DIR
        self.directory = Path(avatars_dir)

    def path_for(self, filename: str) -> Path | None:
        if not is_safe_avatar_filename(filename):
            return None
        return self.directory / filename

    def list_filenames(self) -> tuple[str, ...]:
        if not self.directory.exists():
            return ()
        paths = [
            path for path in self.directory.iterdir()
            if path.is_file() and is_safe_avatar_filename(path.name)
        ]
        paths.sort(key=lambda path: path.stat().st_mtime_ns, reverse=True)
        return tuple(path.name for path in paths)

    def save_png(self, data: bytes) -> str:
        """Atomically save cropped PNG bytes and return their stable filename."""
        if not isinstance(data, bytes) or not data.startswith(_PNG_SIGNATURE):
            raise ValueError("头像数据不是有效的 PNG")
        digest = hashlib.sha256(data).hexdigest()[:16]
        filename = f"avatar_{digest}.png"
        path = self.directory / filename
        if not path.exists():
            atomic_write_bytes(path, data, prefix=".avatar_", suffix=".tmp")
        return filename

    def delete(self, filename: str) -> bool:
        """Delete one generated avatar asset; reject all unsafe paths."""
        path = self.path_for(filename)
        if path is None or not path.is_file():
            return False
        path.unlink()
        return True


__all__ = ["UserAvatarStore", "is_safe_avatar_filename"]
