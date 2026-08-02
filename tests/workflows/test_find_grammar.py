"""find 指令语法解析与执行测试"""

import pytest

from lvjiang.workflows.grammar import Find, VarRef, parse_text
from lvjiang.workflows.grammar.ast_nodes import ByClause, Click, Literal


class TestFindGrammar:
    """find 指令语法解析"""

    def test_full_canvas_search(self):
        """find as $var by contains "文字" — 全画布搜索"""
        prog = parse_text('find as $found by contains "调律"\n')
        assert len(prog.body) == 1
        node = prog.body[0]
        assert isinstance(node, Find)
        assert node.var_name == "found"
        assert node.search_scene is None
        assert node.search_region is None
        assert isinstance(node.by, ByClause)
        assert node.by.match_mode == "contains"
        assert isinstance(node.by.target, Literal)
        assert node.by.target.value == "调律"

    def test_with_search_area(self):
        """find [scene].[area] as $var by contains "文字" — 指定区域搜索"""
        prog = parse_text('find [general_action].[btn_area] as $btn by contains "确认"\n')
        node = prog.body[0]
        assert isinstance(node, Find)
        assert node.search_scene == "general_action"
        assert node.search_region == "btn_area"
        assert node.var_name == "btn"
        assert node.by.match_mode == "contains"
        assert node.by.target.value == "确认"

    def test_with_by_equals(self):
        """find as $var by equals "文字" — 精确匹配"""
        prog = parse_text('find as $found by equals "攻击"\n')
        node = prog.body[0]
        assert isinstance(node, Find)
        assert node.by.match_mode == "equals"
        assert node.by.target.value == "攻击"

    def test_with_by_contains_any(self):
        """find as $var by contains_any $list — 顺序匹配"""
        prog = parse_text('find as $found by contains_any $targets\n')
        node = prog.body[0]
        assert isinstance(node, Find)
        assert node.by.match_mode == "contains_any"
        assert isinstance(node.by.target, VarRef)
        assert node.by.target.name == "targets"

    def test_with_by_equals_any(self):
        """find as $var by equals_any $list — 列表匹配"""
        prog = parse_text('find as $found by equals_any $keywords\n')
        node = prog.body[0]
        assert isinstance(node, Find)
        assert node.by.match_mode == "equals_any"
        assert isinstance(node.by.target, VarRef)
        assert node.by.target.name == "keywords"

    def test_dynamic_scene_and_region(self):
        """find $scene.$region as $var by ... — 动态场景和区域"""
        prog = parse_text('find $scene.$region as $found by contains "文字"\n')
        node = prog.body[0]
        assert isinstance(node, Find)
        assert isinstance(node.search_scene, VarRef)
        assert node.search_scene.name == "scene"
        assert isinstance(node.search_region, VarRef)
        assert node.search_region.name == "region"

    def test_find_with_click_found(self):
        """find + click $found 混排"""
        text = 'find as $found by contains "调律"\nclick $found\n'
        prog = parse_text(text)
        assert len(prog.body) == 2
        assert isinstance(prog.body[0], Find)
        click_node = prog.body[1]
        assert isinstance(click_node, Click)
        assert isinstance(click_node.target, VarRef)
        assert click_node.target.name == "found"

    def test_find_in_if_block(self):
        """find 在 if 块内使用"""
        text = '''\
find as $found by contains "调律"
if $found
    click $found
end
'''
        prog = parse_text(text)
        assert len(prog.body) == 2
        assert isinstance(prog.body[0], Find)

    def test_by_clause_required(self):
        """find 必须有 by 子句"""
        with pytest.raises(Exception):  # noqa: B017  验证解析失败即可，不限定具体异常类型
            parse_text('find as $found\n')

    def test_area_with_field_list(self):
        """find [scene].[a1, a2] as $var by ... — 多字段取首个"""
        prog = parse_text('find [scene].[area1, area2] as $found by contains "文字"\n')
        node = prog.body[0]
        assert isinstance(node, Find)
        assert node.search_scene == "scene"
        # field_list 取第一个字段
        assert node.search_region == "area1"


class TestFoundRegion:
    """FoundRegion 数据类测试"""

    def test_center_ratios(self):
        from lvjiang.core.scene_registry import FoundRegion
        fr = FoundRegion(x_ratio=0.1, y_ratio=0.2, w_ratio=0.3, h_ratio=0.4)
        cx, cy = fr.center_ratios()
        assert abs(cx - 0.25) < 1e-9
        assert abs(cy - 0.4) < 1e-9

    def test_with_text(self):
        from lvjiang.core.scene_registry import FoundRegion
        fr = FoundRegion(x_ratio=0.1, y_ratio=0.2, w_ratio=0.3, h_ratio=0.4, text="调律")
        assert fr.text == "调律"
