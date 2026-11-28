"""Private source files for editor runs, with ordinary workflow import resolution."""
import os
from contextlib import contextmanager
from pathlib import Path
from tempfile import mkstemp

from ..core.config.resolver import LAYER_LOCAL, get_root_resolver

#: 编辑器草稿与试运行临时文件统一收进 ``_scratch/``：``workflows/`` 根层只留
#: 目录，不给「最外层平铺一堆 .wf」留先例。``_`` 前缀既让发现层跳过，也让它
#: 天然落在 local 层被忽略的 ``workflows/_*`` 规则里。
SCRATCH_DIR_REL = "workflows/_scratch"
EDITOR_RUN_REL = f"{SCRATCH_DIR_REL}/editor_run.wf"


def _normalized_source(text: str) -> str:
    return text if text.endswith("\n") else text + "\n"


def save_editor_snapshot(text: str) -> Path:
    """Persist the latest editor run for restoring it on the next open.

    The draft is private state, not official configuration: always write the
    local layer, even in dev mode where ordinary saves go to system.
    """
    return get_root_resolver().write_entity(
        EDITOR_RUN_REL, _normalized_source(text), layer=LAYER_LOCAL)


@contextmanager
def runtime_source(text: str):
    """Keep each run's source isolated until execution and all imports finish."""
    # Keep relative input-trace references working, but never reuse a shared filename.
    # Run sources are private too: writing them to system leaves `_editor_run_*.wf`
    # debris in the preset whenever a crash skips the unlink.
    directory = get_root_resolver().local_dir / SCRATCH_DIR_REL
    directory.mkdir(parents=True, exist_ok=True)
    fd, filename = mkstemp(dir=directory, prefix="editor_run_", suffix=".wf")
    path = Path(filename)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(_normalized_source(text))
        yield path
    finally:
        path.unlink(missing_ok=True)
