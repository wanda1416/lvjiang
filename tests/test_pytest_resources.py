"""临时目录扫描须及时释放句柄，且继续遵守 pytest 的保留和清理规则。"""

import time

import _pytest.pathlib
import pytest

from tests import pytest_resources


def test_partial_consumption_releases_handle_and_preserves_matching(tmp_path, monkeypatch):
    for name in ("pytest-0", "PYTEST-1", "unrelated"):
        (tmp_path / name).mkdir()
    native_scandir = pytest_resources.scandir
    handles = []

    def tracked_scandir(path):
        handle = native_scandir(path)
        handles.append(handle)
        return handle

    monkeypatch.setattr(pytest_resources, "scandir", tracked_scandir)
    matches = _pytest.pathlib.find_prefixed(tmp_path, "pytest-")
    first = next(matches)
    # 原生句柄已关闭，即便消费者还持有生成器且只取一项，也不会等待 GC 来释放。
    assert next(handles[0], None) is None
    remaining = list(matches)
    assert {entry.name for entry in (first, *remaining)} == {"pytest-0", "PYTEST-1"}
    assert all(entry.is_dir() for entry in (first, *remaining))
    abandoned = _pytest.pathlib.find_prefixed(tmp_path, "pytest-")
    next(abandoned)
    abandoned.close()
    assert next(handles[-1], None) is None


def test_directory_read_error_closes_handle_and_propagates(tmp_path, monkeypatch):
    (tmp_path / "pytest-0").mkdir()
    handle = pytest_resources.scandir(tmp_path)

    class FailedScan:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            handle.close()

        def __iter__(self):
            return self

        def __next__(self):
            raise OSError("directory read failed")

    monkeypatch.setattr(pytest_resources, "scandir", lambda path: FailedScan())
    with pytest.raises(OSError, match="directory read failed"):
        next(_pytest.pathlib.find_prefixed(tmp_path, "pytest-"))
    assert next(handle, None) is None


def test_numbered_cleanup_preserves_retention_and_unrelated_directories(tmp_path):
    for name in ("pytest-0", "pytest-1", "pytest-2", "pytest-3", "pytest-4", "unrelated"):
        (tmp_path / name).mkdir()
    _pytest.pathlib.cleanup_numbered_dir(tmp_path, "pytest-", 2, time.time())
    assert {entry.name for entry in tmp_path.iterdir()} == {"pytest-3", "pytest-4", "unrelated"}
