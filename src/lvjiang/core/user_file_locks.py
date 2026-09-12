"""Lock-file placement for independently persisted user documents."""

from pathlib import Path


def user_file_lock_path(data_path: Path) -> Path:
    """Return a hidden sibling-directory lock path for one user data file."""
    lock_dir = data_path.parent / ".lock"
    lock_dir.mkdir(parents=True, exist_ok=True)
    return lock_dir / f"{data_path.name}.lock"


def collect_legacy_user_file_locks(users_dir: Path) -> int:
    """Move legacy JSON sidecar locks into ``users/.lock`` when possible."""
    if not users_dir.is_dir():
        return 0
    moved = 0
    for source in users_dir.glob("*.json.lock"):
        target = users_dir / ".lock" / source.name
        if target.exists():
            continue
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            source.replace(target)
            moved += 1
        except OSError:
            # A concurrently running older process may still own this file.
            continue
    return moved
