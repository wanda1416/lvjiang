"""find 指令语法解析与执行测试"""

import pytest

from lvjiang.workflows.grammar import Find, VarRef, parse_text
from lvjiang.workflows.grammar.ast_nodes import Click


class TestFindGrammar:
    """find 指令语法解析"""


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


    def test_by_clause_required(self):
        """find 必须有 by 子句"""
        with pytest.raises(Exception):  # noqa: B017  验证解析失败即可，不限定具体异常类型
            parse_text('find as $found\n')


class TestFoundRegion:
    """FoundRegion 数据类测试"""

    def test_center_ratios(self):
        from lvjiang.core.layout_models import FoundRegion
        fr = FoundRegion(x_ratio=0.1, y_ratio=0.2, w_ratio=0.3, h_ratio=0.4)
        cx, cy = fr.center_ratios()
        assert abs(cx - 0.25) < 1e-9
        assert abs(cy - 0.4) < 1e-9
