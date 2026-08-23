"""DSL import 依赖图解析语义。"""

from collections import Counter
from pathlib import Path

import pytest

import lvjiang.workflows.engine.core as engine_core
from lvjiang.workflows.engine.signals import WorkflowUserError
from tests.workflows.conftest import make_engine


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def test_duplicate_direct_import_is_loaded_once(tmp_path, monkeypatch):
    """同一文件被直接重复 import 时只解析一次。"""
    shared = _write(tmp_path / "shared.wf", "def shared()\n    return 1\nend\n")
    root = _write(
        tmp_path / "root.wf",
        'import "shared.wf"\nimport "shared.wf"\ncall shared()\n',
    )
    parse_counts: Counter[Path] = Counter()
    real_parse_file = engine_core.parse_file

    def counting_parse_file(path):
        parse_counts[Path(path).resolve()] += 1
        return real_parse_file(path)

    monkeypatch.setattr(engine_core, "parse_file", counting_parse_file)
    make_engine()._load_and_validate(root.resolve())

    assert parse_counts[shared.resolve()] == 1


def test_diamond_import_is_loaded_once(tmp_path, monkeypatch):
    """菱形依赖中的公共文件只解析一次。"""
    common = _write(tmp_path / "common.wf", "def common()\n    return 1\nend\n")
    _write(
        tmp_path / "left.wf",
        'import "common.wf"\ndef left()\n    return 1\nend\n',
    )
    _write(
        tmp_path / "right.wf",
        'import "common.wf"\ndef right()\n    return 1\nend\n',
    )
    root = _write(
        tmp_path / "root.wf",
        'import "left.wf"\nimport "right.wf"\ncall common()\n',
    )
    parse_counts: Counter[Path] = Counter()
    real_parse_file = engine_core.parse_file

    def counting_parse_file(path):
        parse_counts[Path(path).resolve()] += 1
        return real_parse_file(path)

    monkeypatch.setattr(engine_core, "parse_file", counting_parse_file)
    engine = make_engine()
    engine._load_and_validate(root.resolve())

    assert parse_counts[common.resolve()] == 1
    assert set(engine._procs) == {"common", "left", "right"}


def test_circular_import_reports_real_dependency_order(tmp_path):
    """循环 import 错误按真实递归顺序展示依赖链。"""
    first = _write(tmp_path / "first.wf", 'import "second.wf"\n')
    second = _write(tmp_path / "second.wf", 'import "third.wf"\n')
    third = _write(tmp_path / "third.wf", 'import "first.wf"\n')

    with pytest.raises(WorkflowUserError) as exc_info:
        make_engine()._load_and_validate(first.resolve())

    message = str(exc_info.value)
    expected_chain = " -> ".join(map(str, [
        first.resolve(), second.resolve(), third.resolve(), first.resolve(),
    ]))
    assert message == f"循环 import 检测: {expected_chain}"


def test_same_proc_from_different_files_is_an_error(tmp_path):
    """不同文件的同名过程不再根据 import 顺序静默覆盖。"""
    first = _write(tmp_path / "first.wf", "def duplicate()\n    return 1\nend\n")
    second = _write(tmp_path / "second.wf", "def duplicate()\n    return 2\nend\n")
    root = _write(
        tmp_path / "root.wf",
        'import "first.wf"\nimport "second.wf"\n',
    )

    with pytest.raises(WorkflowUserError) as exc_info:
        make_engine()._load_and_validate(root.resolve())

    message = str(exc_info.value)
    assert "过程 duplicate 定义冲突" in message
    assert str(first.resolve()) in message
    assert str(second.resolve()) in message


def test_root_proc_cannot_shadow_imported_proc(tmp_path):
    """根工作流也不能隐式覆盖导入过程。"""
    imported = _write(
        tmp_path / "imported.wf", "def duplicate()\n    return 1\nend\n")
    root = _write(
        tmp_path / "root.wf",
        'import "imported.wf"\ndef duplicate()\n    return 2\nend\n',
    )

    with pytest.raises(WorkflowUserError) as exc_info:
        make_engine()._load_and_validate(root.resolve())

    message = str(exc_info.value)
    assert str(imported.resolve()) in message
    assert str(root.resolve()) in message
