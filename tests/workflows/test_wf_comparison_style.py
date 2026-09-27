"""系统工作流的条件比较统一使用 == / !=。"""

from dataclasses import fields, is_dataclass

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


def test_system_workflows_use_symbolic_string_equality():
    workflows = SYSTEM_CONFIG_DIR / "workflows"
    legacy = [str(path.relative_to(workflows))
              for path in sorted(workflows.rglob("*.wf"))
              if any(_walk(parse_file(path)))]
    assert not legacy, f"条件比较请使用 == / !=；by equals 匹配模式可保留：{legacy}"
