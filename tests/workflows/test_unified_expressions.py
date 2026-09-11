"""统一 expression 在不同语法入口中的解析与运行语义。"""

import pytest

from lvjiang.workflows.grammar import parse_text
from tests.workflows.conftest import make_engine, run


def test_assignment_return_and_proc_args_share_expression_capabilities():
    code = '''eval $base = 2
eval $enabled = $base > 1 and not false
eval $items = [$base + 1, $enabled, {"ready": $base == 2}]
call $result = pack($base * 3, $base > 1, $items)
def pack($number, $flag, $values)
    return {"number": $number, "flag": $flag, "values": $values}
end
'''

    values = run(code)

    assert values["enabled"] is True
    assert values["items"] == [3, True, {"ready": True}]
    assert values["result"] == {
        "number": 6,
        "flag": True,
        "values": [3, True, {"ready": True}],
    }


def test_if_accepts_any_expression_in_boolean_context():
    values = run('''eval $hits = 0
if 1 + 1
    eval $hits = $hits + 1
end
if []
    eval $hits = $hits + 10
end
if "text"
    eval $hits = $hits + 1
end
''')

    assert values["hits"] == 2


def test_default_expression_is_lazy_when_value_was_injected():
    code = '''default $value = explode()
'''
    engine = make_engine()
    engine.variables = {"value": "injected"}
    engine._call_func = lambda _node: pytest.fail(
        "已注入 default 不应求值右侧表达式")

    engine._exec_body(parse_text(code).body)

    assert engine.variables["value"] == "injected"


def test_logical_expression_short_circuits_inside_container():
    code = '''eval $values = [true or explode(), false and explode()]
'''
    engine = make_engine()
    engine._call_func = lambda _node: pytest.fail("逻辑表达式右侧不应被求值")

    engine._exec_body(parse_text(code).body)

    assert engine.variables["values"] == [True, False]


def test_tuple_elements_accept_full_expressions():
    values = run('''eval $base = 2
eval $pair = ($base + 1, $base > 1)
''')

    assert values["pair"] == (3, True)


def test_legacy_value_forms_are_equivalent_across_expression_consumers():
    value = '{"text": "ok", "number": 3, "none": null, "flags": [true, false]}'
    code = f'''def identity($value)
    return $value
end
eval $explicit = {value}
$implicit = {value}
default $defaulted = {value}
call $returned = identity({value})
collect {value} as "collected"
'''

    engine = make_engine()
    program = parse_text(code)
    engine._procs = dict(program.procs)
    engine._exec_body(program.body)

    expected = {
        "text": "ok",
        "number": 3,
        "none": None,
        "flags": [True, False],
    }
    assert engine.variables["explicit"] == expected
    assert engine.variables["implicit"] == expected
    assert engine.variables["defaulted"] == expected
    assert engine.variables["returned"] == expected
    assert engine.output["collected"] == expected


def test_for_and_loop_accept_computed_expressions():
    values = run('''eval $data = {"items": [2, 4]}
eval $sum = 0
for item in $data.items
    eval $sum = $sum + $item
end
loop 1 + 2
    eval $sum = $sum + 1
end
''')

    assert values["sum"] == 9


def test_for_range_endpoints_accept_expressions():
    values = run('''eval $sum = 0
for value in [1 + 1...2 * 2]
    eval $sum = $sum + $value
end
''')

    assert values["sum"] == 9


def test_legacy_bare_loop_variable_is_executable():
    values = run('''eval $times = 2
eval $count = 0
loop times
    eval $count = $count + 1
end
''')

    assert values["count"] == 2
