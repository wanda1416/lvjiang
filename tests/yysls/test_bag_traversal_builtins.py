"""背包遍历内置函数测试

覆盖 check_scroll / notify_scroll / scroll_advance 的核心逻辑。
"""

import pytest

# 导入以注册内置函数
import lvjiang.apps.yysls.workflows.builtins.bag_traversal  # noqa: F401
from lvjiang.workflows.builtins import get_function


def _fn(name):
    fn = get_function(name)
    assert fn is not None, f"内置函数 {name} 未注册"
    return fn


class MockEngine:
    """模拟引擎，提供 context 字典"""
    def __init__(self):
        self.context = {}


@pytest.fixture
def engine():
    return MockEngine()


class TestNotifyScroll:
    def test_col_1_records_row_fingerprint(self, engine):
        """col=1 时记录行指纹"""
        _fn("notify_scroll")(engine, 1, 1, "fp_row1")
        manager = engine.context["_scroll_manager"]
        assert "fp_row1" in manager["row_fps"]
        assert "fp_row1" in manager["fingerprints"]

    def test_col_not_1_only_records_fingerprint(self, engine):
        """col!=1 时只记指纹不记行"""
        _fn("notify_scroll")(engine, 2, 1, "fp_cell")
        manager = engine.context["_scroll_manager"]
        assert "fp_cell" not in manager["row_fps"]
        assert "fp_cell" in manager["fingerprints"]

    def test_multiple_rows_accumulate(self, engine):
        """多行指纹累积"""
        _fn("notify_scroll")(engine, 1, 1, "fp1")
        _fn("notify_scroll")(engine, 1, 2, "fp2")
        _fn("notify_scroll")(engine, 1, 3, "fp3")
        manager = engine.context["_scroll_manager"]
        assert manager["row_fps"] == ["fp1", "fp2", "fp3"]

    def test_col_as_string(self, engine):
        """col 为字符串 "1" 时也视为第一列"""
        _fn("notify_scroll")(engine, "1", 1, "fp_str")
        manager = engine.context["_scroll_manager"]
        assert "fp_str" in manager["row_fps"]


class TestCheckScroll:
    def test_no_snapshot_returns_zero(self, engine):
        """无快照数据时返回 "0"（正常）"""
        result = _fn("check_scroll")(engine, "any_fp")
        assert result == "0"

    def test_unknown_fingerprint_returns_zero(self, engine):
        """指纹不在已知集合中返回 "0"（视为正常）"""
        engine.context["_scroll_manager"] = {
            "row_fps": ["fp1", "fp2"],
            "fingerprints": {"fp1": True, "fp2": True},
        }
        result = _fn("check_scroll")(engine, "unknown_fp")
        assert result == "0"

    def test_normal_offset_returns_zero(self, engine):
        """正常偏移（行 2 位置）返回 "0" """
        engine.context["_scroll_manager"] = {
            "row_fps": ["fp1", "fp2", "fp3"],
            "fingerprints": {"fp1": True, "fp2": True, "fp3": True},
        }
        # fp2 在 row_fps[1]，offset = 1 - 1 = 0
        result = _fn("check_scroll")(engine, "fp2")
        assert result == "0"

    def test_not_scrolled_returns_positive(self, engine):
        """没滚动（仍在行 1 位置）返回 "+1" """
        engine.context["_scroll_manager"] = {
            "row_fps": ["fp1", "fp2", "fp3"],
            "fingerprints": {"fp1": True, "fp2": True, "fp3": True},
        }
        # fp1 在 row_fps[0]，offset = 0 - 1 = -1... 等等，这是过头
        # 让我重新理解：滚动 1 步后，行 1 应该是原行 2（i=1）
        # 如果看到的是原行 1（i=0），说明没滚动，offset = 0 - 1 = -1
        # 但注释说 offset = i - 1: 0=正常, -1=过头, +1=没滚
        # 所以没滚应该是 i=2（看到的是原行 3），offset = 2 - 1 = +1
        result = _fn("check_scroll")(engine, "fp3")
        assert result == "1"  # 没滚

    def test_scrolled_too_far_returns_negative(self, engine):
        """滚动过头（行 3 位置）返回 "-1" """
        engine.context["_scroll_manager"] = {
            "row_fps": ["fp1", "fp2", "fp3"],
            "fingerprints": {"fp1": True, "fp2": True, "fp3": True},
        }
        # fp1 在 row_fps[0]，offset = 0 - 1 = -1（过头）
        result = _fn("check_scroll")(engine, "fp1")
        assert result == "-1"


class TestScrollAdvance:
    def test_removes_first_row_fingerprint(self, engine):
        """推进后移除首行指纹"""
        engine.context["_scroll_manager"] = {
            "row_fps": ["fp1", "fp2", "fp3"],
            "fingerprints": {},
            "scroll_count": 0,
        }
        _fn("scroll_advance")(engine)
        assert engine.context["_scroll_manager"]["row_fps"] == ["fp2", "fp3"]

    def test_increments_scroll_count(self, engine):
        """推进后 scroll_count 递增"""
        engine.context["_scroll_manager"] = {
            "row_fps": ["fp1"],
            "fingerprints": {},
            "scroll_count": 5,
        }
        _fn("scroll_advance")(engine)
        assert engine.context["_scroll_manager"]["scroll_count"] == 6

    def test_empty_row_fps_no_error(self, engine):
        """row_fps 为空时不报错"""
        engine.context["_scroll_manager"] = {
            "row_fps": [],
            "fingerprints": {},
            "scroll_count": 0,
        }
        _fn("scroll_advance")(engine)
        assert engine.context["_scroll_manager"]["scroll_count"] == 1

    def test_no_manager_context_no_error(self, engine):
        """无 _scroll_manager 上下文时不报错"""
        _fn("scroll_advance")(engine)
        # 不应抛异常
