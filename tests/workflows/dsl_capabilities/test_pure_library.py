"""用真实 .wf 过程库验证 DSL 的集合、递归和数值计算能力。"""

from copy import deepcopy
from pathlib import Path
from shutil import copyfile

import pytest

from lvjiang.workflows.discovery import discover_scripts
from tests.workflows.conftest import make_engine, run

LIB_DIR = Path(__file__).resolve().parents[3] / "config/system/workflows/lib"


@pytest.fixture(scope="module")
def libraries():
    engines = {}
    for name in ("collections", "ordering", "numeric"):
        engine = make_engine()
        engine.load_subcalls(LIB_DIR / f"{name}.wf")
        engines[name] = engine
    return engines


def call(libraries, library, procedure, *args):
    original = deepcopy(args)
    result = libraries[library].call_subcall(procedure, list(args))
    assert args == original, f"{procedure} 修改了输入"
    return result


@pytest.mark.parametrize(
    ("items", "expected"),
    [
        ([], []),
        ([3, 1, 3, 2, 1], [3, 1, 2]),
        (["b", "a", "b", "c"], ["b", "a", "c"]),
    ],
)
def test_unique(libraries, items, expected):
    assert call(libraries, "collections", "unique", items) == expected


@pytest.mark.parametrize(
    ("groups", "expected"),
    [([], []), ([[], []], []), ([[1, [2]], [3]], [1, [2], 3])],
)
def test_flatten_once(libraries, groups, expected):
    assert call(libraries, "collections", "flatten_once", groups) == expected


@pytest.mark.parametrize(
    ("items", "size", "expected"),
    [
        ([1, 2, 3, 4, 5], 2, [[1, 2], [3, 4], [5]]),
        ([], 3, []),
        ([1], 0, []),
    ],
)
def test_chunk(libraries, items, size, expected):
    assert call(libraries, "collections", "chunk", items, size) == expected


def test_group_by_key_preserves_member_order_and_skips_missing_key(libraries):
    rows = [
        {"team": "a", "value": 1},
        {"team": "b", "value": 2},
        {"team": "a", "value": 3},
        {"value": 4},
        {"team": "", "value": 5},
    ]
    assert call(libraries, "collections", "group_by_key", rows, "team") == {
        "a": [rows[0], rows[2]],
        "b": [rows[1]],
    }


@pytest.mark.parametrize(
    "items",
    [[], [7], [3, 1, 3, -2, 0, -2], list(range(60, 0, -1))],
)
def test_quicksort(libraries, items):
    assert call(libraries, "ordering", "quicksort", items) == sorted(items)


@pytest.mark.parametrize(
    ("left", "right", "expected"),
    [
        ([], [], []),
        ([], [1, 3], [1, 3]),
        ([1, 2, 2], [2, 3], [1, 2, 2, 2, 3]),
    ],
)
def test_merge_sorted(libraries, left, right, expected):
    assert call(libraries, "ordering", "merge_sorted", left, right) == expected


@pytest.mark.parametrize(
    ("items", "value", "bound", "found"),
    [
        ([], 2, 0, -1),
        ([1, 2, 2, 4], 2, 1, 1),
        ([1, 2, 2, 4], 3, 3, -1),
        ([1, 2, 2, 4], 0, 0, -1),
        ([1, 2, 2, 4], 5, 4, -1),
    ],
)
def test_search_boundaries(libraries, items, value, bound, found):
    assert call(libraries, "ordering", "lower_bound", items, value) == bound
    assert call(libraries, "ordering", "binary_search", items, value) == found


@pytest.mark.parametrize(
    ("a", "b", "expected"),
    [(36, 24, 12), (-36, 24, 12), (0, 5, 5), (0, 0, 0)],
)
def test_gcd(libraries, a, b, expected):
    assert call(libraries, "numeric", "gcd", a, b) == expected


@pytest.mark.parametrize(
    ("items", "expected"),
    [([], []), ([2, -1, 3], [2, 1, 4]), ([0, 0], [0, 0])],
)
def test_prefix_sums(libraries, items, expected):
    assert call(libraries, "numeric", "prefix_sums", items) == expected


def test_algorithm_composition(libraries):
    values = [4, 1, 3, 1, 2]
    ordered = call(libraries, "ordering", "quicksort", values)
    assert call(libraries, "ordering", "binary_search", ordered, 1) == 0
    assert call(libraries, "numeric", "prefix_sums", ordered) == [1, 2, 4, 7, 11]


def _copy_libraries(wf_root):
    target = wf_root / "lib"
    target.mkdir()
    for source in LIB_DIR.glob("*.wf"):
        copyfile(source, target / source.name)


def test_library_import_and_procedure_call(wf_root):
    _copy_libraries(wf_root)
    wrapper = wf_root / "probe.wf"
    wrapper.write_text(
        'import "lib/collections.wf"\n'
        'import "lib/ordering.wf"\n'
        'call $distinct = unique([3, 1, 3, 2])\n'
        'call $sorted = quicksort($distinct)\n'
        'return $sorted\n',
        encoding="utf-8",
    )
    engine = make_engine()
    engine.execute(wrapper)
    assert engine.return_value == [1, 2, 3]


def test_pure_libraries_are_not_discovered_as_runnable_scripts(wf_root):
    _copy_libraries(wf_root)
    paths = {script.get("wf_file") for script in discover_scripts()}
    assert paths.isdisjoint({
        "lib/collections.wf", "lib/ordering.wf", "lib/numeric.wf",
    })


def test_list_index_assignment_is_currently_unsupported():
    """读下标可用；字段赋值仅写字典，不能悄悄改动列表。"""
    values = run('eval $items[0] = 99\neval $first = $items[0]\n', {
        "items": [1, 2],
    })
    assert values["items"] == [1, 2]
    assert values["first"] == 1
