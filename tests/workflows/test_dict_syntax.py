"""测试 DSL 字典变量语法增强"""
from lvjiang.workflows.grammar import parse_text
from lvjiang.workflows.grammar import Eval, EvalFieldChainAssign, FuncCall, Literal, Log


def test_empty_dict():
    """eval $dict = {}"""
    p = parse_text('eval $mydata = {}')
    n = p.body[0]
    assert isinstance(n, Eval)
    assert n.func_name == '__dict__'
    assert n.target == 'mydata'
    print("  eval $dict = {}: OK")


def test_field_assign_string():
    """eval $dict.key = "string" """
    p = parse_text('eval $mydata.name = "hello"')
    n = p.body[0]
    assert isinstance(n, EvalFieldChainAssign)
    assert n.target.root.name == 'mydata'
    assert n.target.field_name == 'name'
    assert isinstance(n.value, Literal)
    assert n.value.value == 'hello'
    print('  eval $dict.key = "string": OK')


def test_field_assign_number():
    """eval $dict.key = 123"""
    p = parse_text('eval $mydata.count = 123')
    n = p.body[0]
    assert isinstance(n, EvalFieldChainAssign)
    assert n.target.root.name == 'mydata'
    assert n.target.field_name == 'count'
    assert n.value == 123.0  # number -> float
    print("  eval $dict.key = 123: OK")


def test_log_func_call():
    """log concat("text", $var)"""
    p = parse_text('log concat("current: ", $mydata.name)')
    n = p.body[0]
    assert isinstance(n, Log)
    assert isinstance(n.message, FuncCall)
    assert n.message.func_name == 'concat'
    assert len(n.message.func_args) == 2
    print('  log concat("text", $var): OK')


def test_existing_syntax_still_works():
    """确保已有语法不被破坏"""
    # eval $var = "string"
    p = parse_text('eval $x = "hello"')
    n = p.body[0]
    assert isinstance(n, Eval)
    assert n.func_name == '__literal__'
    assert n.target == 'x'

    # eval $var = func($arg)
    p = parse_text('eval $result = is_good($scan)')
    n = p.body[0]
    assert isinstance(n, Eval)
    assert n.func_name == 'is_good'
    assert n.target == 'result'

    # log "string"
    p = parse_text('log "hello world"')
    n = p.body[0]
    assert isinstance(n, Log)
    assert isinstance(n.message, Literal)
    assert n.message.value == 'hello world'

    print("  existing syntax compatibility: OK")


if __name__ == '__main__':
    print("=== DSL 字典变量语法增强测试 ===")
    test_empty_dict()
    test_field_assign_string()
    test_field_assign_number()
    test_log_func_call()
    test_existing_syntax_still_works()
    print("\n所有测试通过!")
