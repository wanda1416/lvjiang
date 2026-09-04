"""global 指令：声明跨过程共享、未声明变量继续隔离。"""

from lvjiang.workflows.grammar import Global, parse_text
from tests.workflows.conftest import run


def test_parse_one_or_more_global_names():
    program = parse_text("global $first, $second\n")

    node = program.body[0]
    assert isinstance(node, Global)
    assert node.names == ["first", "second"]
    assert node.line_no == 1


def test_global_value_is_shared_and_written_back_by_proc():
    variables = run(
        """global $counter
eval $counter = 1
def increment()
    eval $counter = $counter + 1
end
call increment()
"""
    )

    assert variables["counter"] == 2


def test_global_value_flows_through_nested_calls():
    variables = run(
        """global $counter
eval $counter = 0
def inner()
    eval $counter = $counter + 1
end
def outer()
    eval $counter = $counter + 1
    call inner()
end
call outer()
"""
    )

    assert variables["counter"] == 2


def test_nested_call_result_can_update_global_value():
    variables = run(
        """global $state
eval $state = 1
def read_new_state()
    return 5
end
def update()
    call $state = read_new_state()
end
call update()
"""
    )

    assert variables["state"] == 5


def test_global_declared_in_proc_becomes_visible_to_caller():
    variables = run(
        """def publish()
    global $result
    eval $result = "ready"
end
call publish()
eval $copy = $result
"""
    )

    assert variables["result"] == "ready"
    assert variables["copy"] == "ready"


def test_global_promotes_existing_value():
    variables = run(
        """eval $name = "before"
global $name
def update()
    eval $name = "after"
end
call update()
"""
    )

    assert variables["name"] == "after"


def test_undeclared_variables_remain_call_local():
    variables = run(
        """eval $value = "caller"
def update()
    eval $value = "callee"
end
call update()
"""
    )

    assert variables["value"] == "caller"
