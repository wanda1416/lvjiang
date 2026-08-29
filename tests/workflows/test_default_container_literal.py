"""default $var = {dict} / [list] — 容器字面量的默认值必须落成真实值

`default` 走 `_resolve_literal`，它原来只拆 VarRef / FieldAccess，漏了
`Literal`。而 dict/list 字面量的**标量**元素在解析阶段就被包成了 `Literal`
节点（grammar 的 dict_val_* / list_item_* 规则），于是

    default $d = {"a": true, "b": false}

存进变量表的是 `{'a': Literal(value=True), 'b': Literal(value=False)}`——
AST 节点本身，不是布尔值。

这个失效方式很隐蔽：节点对象恒为真值，`if $d.b` 在写 false 时照样成立，
开关型默认值全部失效且不报任何错。checkgroup 参数的值正是这种
`{名字: 是否启用}` 字典（purchase_bugan.wf 的 buy_keywords 用它决定买
哪些商品类目），未注入参数时回落到 default，漏拆就会把"没勾的项"也买掉。

`eval` 走的是另一条求值路径，本来就是对的，所以只有 `default` 中招——
两者必须一致。
"""

from pathlib import Path

from lvjiang.workflows.metadata import parse_metadata_file
from tests.workflows.conftest import run

_PURCHASE_BUGAN = (
    Path(__file__).parents[2]
    / "config" / "system" / "workflows" / "purchase_bugan.wf"
)


class TestDefaultContainerLiteral:
    def test_dict_scalars_are_real_values(self):
        v = run('default $d = {"a": true, "b": false, "n": 3, "s": "x"}\n')
        assert v["d"] == {"a": True, "b": False, "n": 3, "s": "x"}

    def test_list_scalars_are_real_values(self):
        assert run('default $l = ["x", "y", 1, true]\n')["l"] == ["x", "y", 1, True]

    def test_nested_containers_resolved(self):
        v = run('default $n = {"a": {"b": false}, "c": [1, true]}\n')
        assert v["n"] == {"a": {"b": False}, "c": [1, True]}

    def test_false_flag_is_falsy_in_condition(self):
        """核心症状：漏拆时 Literal(False) 恒真，if 分支会走错。"""
        code = (
            'default $d = {"on": true, "off": false}\n'
            'eval $hit = "none"\n'
            'if $d.off\n'
            '    eval $hit = "wrong"\n'
            'end\n'
        )
        assert run(code)["hit"] == "none"

    def test_matches_eval_semantics(self):
        """同一个字面量，default 和 eval 必须得到完全一样的值。"""
        lit = '{"a": true, "b": false, "c": [1, "x"]}'
        assert run(f"default $d = {lit}\n")["d"] == run(f"eval $d = {lit}\n")["d"]

    def test_varref_inside_container_still_resolved(self):
        """原本就支持的 VarRef 解析不能被这次修复破坏。"""
        assert run('eval $v = 7\ndefault $d = {"k": $v}\n')["d"] == {"k": 7}

    def test_injected_value_still_wins(self):
        """default 的语义是"未注入才赋值"，容器同样不能覆盖已注入的值。"""
        v = run('default $d = {"a": true}\n', {"d": {"a": False}})
        assert v["d"] == {"a": False}


def test_purchase_bugan_metadata_and_runtime_defaults_stay_in_sync():
    """日常页勾选项与未注入参数时的 DSL 回退值必须一致。"""
    metadata = parse_metadata_file(_PURCHASE_BUGAN)
    parameter = next(
        item for item in metadata["parameters"]
        if item["name"] == "buy_keywords"
    )
    option_values = [item["value"] for item in parameter["options"]]

    source = _PURCHASE_BUGAN.read_text(encoding="utf-8")
    default_line = next(
        line for line in source.splitlines()
        if line.startswith("default $buy_keywords = ")
    )
    runtime_defaults = run(default_line + "\n")["buy_keywords"]

    assert option_values == list(parameter["default"])
    assert runtime_defaults == parameter["default"]
    assert parameter["default"]["振玉"] is True

    # 批量任务可能直接注入升级前保存的字典，不经过日常参数面板。
    preamble = source.partition('import "subcall/navigation.wf"')[0]
    legacy = run(preamble, {"buy_keywords": {"心法": False}})
    assert legacy["buy_keywords"] == {"心法": False, "振玉": True}

    explicitly_disabled = run(
        preamble,
        {"buy_keywords": {"心法": False, "振玉": False}},
    )
    assert explicitly_disabled["buy_keywords"]["振玉"] is False
