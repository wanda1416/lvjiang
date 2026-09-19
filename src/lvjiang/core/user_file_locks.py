"""Lock-file placement for independently persisted user documents."""

from pathlib import Path


def user_file_lock_path(data_path: Path) -> Path:
    """Return a hidden sibling-directory lock path for one user data file."""
    lock_dir = data_path.parent / ".lock"
    lock_dir.mkdir(parents=True, exist_ok=True)
    return lock_dir / f"{data_path.name}.lock"
