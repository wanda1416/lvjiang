"""系统工作流的条件比较统一使用 == / !=。"""

from dataclasses import fields, is_dataclass
from pathlib import Path

import pytest

from lvjiang.core.config.resolver import SYSTEM_CONFIG_DIR
from lvjiang.workflows.grammar import Equals, parse_file


def _walk(value):
    if isinstance(value, Equals):
        yield value
    if is_dataclass(value):
        for field in fields(value):
            yield from _walk(getattr(value, field.name))
    elif isinstance(value, dict):
        for item in value.values():
            yield from _walk(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _walk(item)


def _system_wf_files() -> list[Path]:
    workflows_dir = SYSTEM_CONFIG_DIR / "workflows"
    return sorted(workflows_dir.rglob("*.wf"))


@pytest.mark.parametrize(
    "wf_path", _system_wf_files(),
    ids=lambda p: p.relative_to(SYSTEM_CONFIG_DIR / "workflows").as_posix())
def test_system_workflows_use_symbolic_string_equality(wf_path):
    """条件比较请使用 == / !=；by equals 匹配模式可保留。

    按脚本参数化：每个脚本都要读盘解析，折成单项会让 xdist 只能在一个 worker
    上串行跑完，成为整条流水线的长尾。
    """
    assert not any(_walk(parse_file(wf_path))), (
        f"{wf_path.relative_to(SYSTEM_CONFIG_DIR / 'workflows')}: "
        "条件比较请使用 == / !=")
