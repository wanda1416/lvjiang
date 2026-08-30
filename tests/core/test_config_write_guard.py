"""测试进程不得修改工作区真实 config，且默认 SessionStore 必须隔离。"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from lvjiang.core.config.session import get_session_store


def _run_guard_script(config_root: Path, target: Path, operation: str):
    script = f"""
from pathlib import Path
from tests.config_write_guard import install_project_config_write_guard
root = Path({str(config_root)!r})
target = Path({str(target)!r})
root.mkdir(parents=True, exist_ok=True)
target.parent.mkdir(parents=True, exist_ok=True)
install_project_config_write_guard(root)
{operation}
"""
    return subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).parents[2],
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )


def test_default_session_store_uses_per_test_tmp_path(tmp_path):
    store = get_session_store()
    store.set_node("proof", {"isolated": True})

    assert store.path.is_relative_to(tmp_path)
    assert store.path.exists()


def test_guard_blocks_direct_write_before_file_changes(tmp_path):
    protected = tmp_path / "workspace" / "config"
    target = protected / "session" / "session.json"

    result = _run_guard_script(
        protected, target, 'target.write_text("forbidden", encoding="utf-8")')

    assert result.returncode != 0
    assert "测试禁止修改项目真实配置目录" in result.stderr
    assert not target.exists()


def test_guard_blocks_atomic_replace_destination(tmp_path):
    protected = tmp_path / "workspace" / "config"
    target = protected / "session" / "session.json"
    source = tmp_path / "replacement.json"
    source.write_text("replacement", encoding="utf-8")

    result = _run_guard_script(
        protected,
        target,
        f'Path({str(source)!r}).replace(target)',
    )

    assert result.returncode != 0
    assert "event=os.rename" in result.stderr
    assert source.exists()
    assert not target.exists()


def test_guard_allows_writes_outside_protected_config(tmp_path):
    protected = tmp_path / "workspace" / "config"
    target = tmp_path / "isolated" / "session.json"

    result = _run_guard_script(
        protected, target, 'target.write_text("allowed", encoding="utf-8")')

    assert result.returncode == 0, result.stderr
    assert target.read_text(encoding="utf-8") == "allowed"


@pytest.mark.skipif(
    os.rmdir not in os.supports_dir_fd,
    reason="当前平台不支持 rmdir(dir_fd=...)，不存在该相对路径场景",
)
def test_guard_resolves_relative_mutation_against_dir_fd(tmp_path):
    """临时目录下同名 config 不能被误判为项目 config。"""
    protected = tmp_path / "workspace" / "config"
    outside = tmp_path / "pytest-cleanup"
    target = outside / "config"
    target.mkdir(parents=True)

    script = f"""
import os
from pathlib import Path
from tests.config_write_guard import install_project_config_write_guard
protected = Path({str(protected)!r})
outside = Path({str(outside)!r})
install_project_config_write_guard(protected)
fd = os.open(outside, os.O_RDONLY)
try:
    os.rmdir("config", dir_fd=fd)
finally:
    os.close(fd)
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).parents[2],
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert not target.exists()
