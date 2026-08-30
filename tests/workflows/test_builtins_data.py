"""字典/列表与字符串内置函数测试（DSL 层集成）

归档自 P1 开发期冒烟测试（scripts/_phase3_smoke.py、_phase4_smoke.py）。
通过 DSL 脚本执行验证函数在引擎中的实际行为。
"""
from tests.workflows.conftest import run

# ─── 字典/列表函数 ─────────────────────────────────────────

class TestCollectionFuncs:
    def test_len(self):
        assert run('eval $n = len($d)', {"d": {"a": 1, "b": 2, "c": ""}})["n"] == 3
        assert run('eval $n = len($l)', {"l": [1, 2, 3, 4]})["n"] == 4
        assert run('eval $n = len($s)', {"s": "hello"})["n"] == 5

    def test_keys_with_for(self):
        code = '''eval $sum = 0
for k in keys($d)
    eval $sum = $sum + 1
end
'''
        v = run(code, {"d": {"a": 1, "b": 2, "c": 3}})
        assert v["sum"] == 3.0

    def test_values(self):
        v = run('eval $vs = values($d)', {"d": {"a": 1, "b": 2}})
        assert v["vs"] == [1, 2]

    def test_has_key(self):
        code_tpl = '''eval $hit = has_key($d, "%s")
if $hit
    eval $hit = 1
else
    eval $hit = 0
end
'''
        assert run(code_tpl % "a", {"d": {"a": 1}})["hit"] == 1.0
        assert run(code_tpl % "missing", {"d": {"a": 1}})["hit"] == 0.0

    def test_del_key(self):
        v = run('eval del_key($d, "a")\neval $n = len($d)\n', {"d": {"a": 1, "b": 2}})
        assert v["n"] == 1
        assert "a" not in v["d"]

    def test_remove(self):
        v = run('eval remove($l, "b")\neval $n = len($l)\n', {"l": ["a", "b", "c"]})
        assert v["n"] == 2
        assert "b" not in v["l"]

    def test_slice(self):
        v = run('eval $s = slice($l, 1, 3)', {"l": [10, 20, 30, 40, 50]})
        assert v["s"] == [20, 30, 40]

    def test_type_mismatch_tolerance(self):
        """类型不匹配时返回宽容默认值而非报错"""
        assert run('eval $n = len($x)', {"x": 123})["n"] == 0
        assert run('eval $ks = keys($x)', {"x": 123})["ks"] == []
        assert run('eval $h = has_key($x, "a")', {"x": 123})["h"] is False

    def test_keys_for_dynamic_field(self):
        code = '''eval $total = 0
for k in keys($scores)
    eval $total = $total + $scores.$k
end
'''
        v = run(code, {"scores": {"math": 90, "english": 80, "physics": 85}})
        assert v["total"] == 255.0


# ─── 字符串函数 ───────────────────────────────────────────

class TestStringFuncs:
    def test_substr(self):
        assert run('eval $s = substr($text, 0, 4)', {"text": "hello world"})["s"] == "hello"
        assert run('eval $s = substr($text, 6)', {"text": "hello world"})["s"] == "world"
        assert run('eval $s = substr($text, -5)', {"text": "hello world"})["s"] == "world"

    def test_split(self):
        assert run('eval $parts = split($text, ",")', {"text": "a,b,c"})["parts"] == ["a", "b", "c"]
        assert run('eval $parts = split($text, " ")', {"text": "hello world"})["parts"] == ["hello", "world"]

    def test_replace(self):
        assert run('eval $s = replace($text, "world", "DSL")', {"text": "hello world"})["s"] == "hello DSL"
        # 没匹配到不变
        assert run('eval $s = replace($text, "x", "y")', {"text": "aaa"})["s"] == "aaa"

    def test_match(self):
        # DSL 字符串不做转义处理，"\d" 就是两个字符 \ 和 d
        code = r'''eval $hit = match($text, "^\d+$")
if $hit
    eval $r = 1
else
    eval $r = 0
end
'''
        assert run(code, {"text": "12345"})["r"] == 1.0
        assert run(code, {"text": "123abc"})["r"] == 0.0

    def test_trim(self):
        assert run('eval $s = trim($text)', {"text": "  hello  "})["s"] == "hello"

    def test_upper_lower(self):
        assert run('eval $s = upper($text)', {"text": "hello"})["s"] == "HELLO"
        assert run('eval $s = lower($text)', {"text": "HELLO"})["s"] == "hello"

    def test_to_num(self):
        assert run('eval $n = to_num($text)', {"text": "3.14"})["n"] == 3.14
        assert run('eval $n = to_num($text)', {"text": "not_a_number"})["n"] == 0.0

    def test_extract_int(self):
        """整数提取允许 0，并拒绝把小数或格式错误数字截成整数。"""
        assert run('eval $n = extract_int($text)', {"text": "当周已获取 1234 声望"})["n"] == 1234
        assert run('eval $n = extract_int($text)', {"text": "当周已获取 0 声望"})["n"] == 0
        assert run('eval $n = extract_int($text)', {"text": "a1b23"})["n"] == 1
        assert run('eval $n = extract_int($text)', {"text": "乱码o12.5x"})["n"] == -1
        assert run('eval $n = extract_int($text)', {"text": "1,500"})["n"] == -1
        assert run('eval $n = extract_int($text)', {"text": "没有任何数字"})["n"] == -1

    def test_extract_num(self):
        """通用数字提取接受整数和小数，并以 -1 表示失败。"""
        assert run('eval $n = extract_num($text)', {"text": "当周已获取 1234 声望"})["n"] == 1234
        assert run('eval $n = extract_num($text)', {"text": "数值为0"})["n"] == 0
        assert run('eval $n = extract_num($text)', {"text": "乱码o12.5x"})["n"] == 12.5
        assert run('eval $n = extract_num($text)', {"text": "a1b23"})["n"] == 1
        assert run('eval $n = extract_num($text)', {"text": "没有任何数字"})["n"] == -1
        assert run('eval $n = extract_num($text)', {"text": ""})["n"] == -1

    def test_split_with_for(self):
        code = '''eval $count = 0
for item in split($csv, ",")
    eval $count = $count + 1
end
'''
        v = run(code, {"csv": "apple,banana,cherry,date"})
        assert v["count"] == 4.0

    def test_chained_steps(self):
        """组合链：replace → upper → substr（DSL 不支持嵌套函数调用，需拆步）"""
        code = '''eval $tmp = replace($text, "world", "earth")
eval $s = upper($tmp)
eval $sub = substr($s, 0, 4)
'''
        v = run(code, {"text": "hello world"})
        assert v["s"] == "HELLO EARTH"
        assert v["sub"] == "HELLO"
