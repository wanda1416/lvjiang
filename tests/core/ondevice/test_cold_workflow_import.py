"""冷启动子进程不继承 pytest 已初始化的 registry/SessionStore。"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from lvjiang.core.updates.system_preset import build_system_preset


@pytest.mark.parametrize("preserve", [True, False])
def test_real_bootstrap_then_immediate_import(tmp_path, preserve):
    repo = Path(__file__).resolve().parents[3]
    preset = tmp_path / "preset.zip"
    build_system_preset(repo / "config/system", preset)
    env = dict(
        os.environ,
        LVJIANG_ROOT=str(tmp_path / "phone"),
        PYTHONPATH=os.pathsep.join((str(repo / "src"), str(repo))),
        QT_QPA_PLATFORM="offscreen",
    )
    result = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).with_name("cold_workflow_import.py")),
            str(preset),
            str(preserve).lower(),
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=90,
    )
    assert result.returncode == 0, result.stdout + result.stderr
