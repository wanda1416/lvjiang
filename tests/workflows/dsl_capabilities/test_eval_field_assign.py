"""eval $dict.$key = value — 动态字段名赋值测试

覆盖修复：$key 是 int/float 变量时（如 panel_rows() 返回的行号）之前会
在两处炸：
1. _extract_field_chain 直接把原始 int 塞进 field_chain，写路径用 int
   当 dict key，跟 scan/recognize 结果本来就是 str key（"1"/"2"/...）不
   一致，读写各用一把不同的 key。
2. 就算赋值本身没报错，随后的 debug 日志 '.'.join(field_chain) 也会因为
   field_chain 里混了非 str 元素直接抛 TypeError，把整条工作流打断
   （scan_wallet.wf 用 `eval $money2.$rows = $money2_last.$rows` 补合并
   补扫的最后一行时线上实际触发过）。

修复方向：dict key 必须是字符串（跟 JSON object key 语义对齐），引擎不
对动态 key 做 int/float 隐式转 str——那是读路径 _eval_field_raw 专门为
`$var.[3]` 字面量按行列取值设计的兼容行为，写路径不应该悄悄模仿。用非
字符串变量当动态 key 时，引擎记错误日志、放弃这次赋值，不崩、也不静默
产生一个类型对不上的 key；.wf 脚本需要用数值构造 key 时自己显式转换
（`eval $key = "" + $rows`），类型一目了然。
"""

from tests.workflows.conftest import run


class TestDynamicKeyMustBeString:
    def test_int_var_as_dynamic_key_is_rejected_not_crashed(self):
        """int 变量直接当 key：不再抛异常，赋值被放弃（不产生任何 key）。"""
        code = (
            'eval $rows = 3\n'
            'eval $dst = {}\n'
            'eval $dst.$rows = "value"\n'
        )
        result = run(code)  # 不抛异常即通过
        assert result["dst"] == {}  # 赋值被拒绝，dst 保持空

    def test_float_var_as_dynamic_key_is_rejected_not_crashed(self):
        code = (
            'eval $k = 2.0\n'
            'eval $dst = {}\n'
            'eval $dst.$k = "value"\n'
        )
        result = run(code)
        assert result["dst"] == {}

    def test_str_var_as_dynamic_key_still_works(self):
        """字符串变量当 key：一直支持，不受这次修复影响。"""
        code = (
            'eval $key = "left_1"\n'
            'eval $dst = {}\n'
            'eval $dst.$key = "value"\n'
        )
        result = run(code)
        assert result["dst"] == {"left_1": "value"}

    def test_explicit_string_conversion_before_use_as_key(self):
        """脚本自己用 "" + $num 显式转成字符串后再当 key：正常工作。"""
        code = (
            'eval $rows = 3\n'
            'eval $row_key = "" + $rows\n'
            'eval $dst = {}\n'
            'eval $dst.$row_key = "value"\n'
        )
        result = run(code)
        assert result["dst"] == {"3": "value"}

    def test_merge_row_matches_str_keyed_recognize_result(self):
        """跟 scan_wallet.wf 里补扫最后一行、合并回 $money2 的场景等价：
        显式转出字符串 key 后合并，其余行数据不受影响。
        """
        code = (
            'eval $rows = 3\n'
            'eval $row_key = "" + $rows\n'
            'eval $money2 = {"1": {"1": "old"}, "2": {"1": "old"}}\n'
            'eval $fresh = {"3": {"1": "new"}}\n'
            'eval $money2.$row_key = $fresh.$row_key\n'
        )
        result = run(code)
        assert result["money2"]["3"] == {"1": "new"}
        assert result["money2"]["1"] == {"1": "old"}
        assert result["money2"]["2"] == {"1": "old"}

    def test_reading_with_int_variable_key_still_works(self):
        """读路径不受这次修复影响：$data.$r（$r 是 int）依然能靠既有的
        int→str 兜底读到 str-key 数据，这次修复只收紧了写路径。
        """
        code = (
            'eval $rows = 3\n'
            'eval $money2 = {"1": {"1": "old"}, "3": {"1": "new"}}\n'
            'eval $cell = $money2.$rows."1"\n'
        )
        result = run(code)
        assert result["cell"] == "new"
