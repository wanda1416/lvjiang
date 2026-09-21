"""购买袅袅之音·绑的数量决策回归测试。"""

from pathlib import Path

import pytest

from lvjiang.workflows.grammar import parse_file
from tests.workflows.conftest import make_engine

_WORKFLOW = (
    Path(__file__).parents[2]
    / "config"
    / "system"
    / "workflows"
    / "purchase_niaoniao.wf"
)


@pytest.mark.parametrize(
    ("bought_week", "changmingyu", "expected"),
    [
        (0, 199, 0),
        (0, 200, 1),
        (0, 364, 1),
        (0, 399, 1),
        (0, 400, 2),
        (1, 199, 0),
        (1, 200, 1),
        (2, 1000, 0),
        (0, None, 2),
    ],
)
def test_purchase_count_uses_affordable_quantity(
    bought_week: int,
    changmingyu: int | None,
    expected: int,
) -> None:
    program = parse_file(_WORKFLOW)
    engine = make_engine()
    engine._procs = dict(program.procs)

    result, _output = engine._run_proc(
        program.procs["niaoniao_purchase_count"],
        [bought_week, changmingyu],
    )

    assert result == expected


def test_purchase_still_uses_max_button() -> None:
    source = _WORKFLOW.read_text(encoding="utf-8")

    assert "click [general_purchase].[add_max]" in source
    assert "click [general_purchase].[add_one]" not in source
    assert 'profile_inc("niaoniao_of_week", $want)' in source
