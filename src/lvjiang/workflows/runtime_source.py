"""Private source files for editor runs, with ordinary workflow import resolution."""
import os
from contextlib import contextmanager
from pathlib import Path
from tempfile import mkstemp

from ..core.config.resolver import get_resolver

EDITOR_RUN_REL = "workflows/_editor_run.wf"


def _normalized_source(text: str) -> str:
    return text if text.endswith("\n") else text + "\n"


def save_editor_snapshot(text: str) -> Path:
    """Persist the latest editor run for restoring it on the next open."""
    return get_resolver().write_entity(EDITOR_RUN_REL, _normalized_source(text))


@contextmanager
def runtime_source(text: str):
    """Keep each run's source isolated until execution and all imports finish."""
    # Keep relative input-trace references working, but never reuse a shared filename.
    directory = get_resolver().write_dir("workflows")
    directory.mkdir(parents=True, exist_ok=True)
    fd, filename = mkstemp(dir=directory, prefix="_editor_run_", suffix=".wf")
    path = Path(filename)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(_normalized_source(text))
        yield path
    finally:
        path.unlink(missing_ok=True)
