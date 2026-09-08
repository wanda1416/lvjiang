"""Private source files for editor runs, with ordinary workflow import resolution."""
import os
from contextlib import contextmanager
from pathlib import Path
from tempfile import mkstemp

from ..core.config.resolver import get_resolver


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
            stream.write(text if text.endswith("\n") else text + "\n")
        yield path
    finally:
        path.unlink(missing_ok=True)
