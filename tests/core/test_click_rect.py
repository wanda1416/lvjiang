"""区域点击落点框（click_rect）测试

覆盖：
- effective_click_rect 的两条路径统一（显式标定 / 由 region_jitter_ratio 派生）
- 子集约束：越界的 click_rect 直接拒绝，不静默钳回去
- 序列化：未标定不写盘，标定后可 roundtrip
- 引擎换算：落点落在标定框内，且未标定时与旧实现的分布一致
"""

import random
from unittest.mock import MagicMock

import pytest

from lvjiang.core.layout_models import Region, effective_click_rect
from lvjiang.workflows.base.coords import _CoordMixin


def _region(**kw) -> Region:
    base = dict(key="btn", x_ratio=0.2, y_ratio=0.3, w_ratio=0.4, h_ratio=0.2)
    base.update(kw)
    return Region(**base)


class TestEffectiveClickRect:
    def test_derived_from_jitter_ratio(self):
        """未标定：region_jitter_ratio 派生一个居中框"""
        assert effective_click_rect(_region(), 0.25) == (0.25, 0.25, 0.5, 0.5)
        assert effective_click_rect(_region(), 0.1) == (0.4, 0.4, 0.2, 0.2)

    def test_zero_ratio_collapses_to_center(self):
        """比例 0 = 恒定点区域中心"""
        assert effective_click_rect(_region(), 0.0) == (0.5, 0.5, 0.0, 0.0)

    def test_explicit_rect_is_not_shrunk_again(self):
        """已精确标定的框原样使用，不再叠加抖动收缩"""
        r = _region(click_rect=(0.1, 0.0, 0.8, 0.45))
        assert effective_click_rect(r, 0.25) == (0.1, 0.0, 0.8, 0.45)


class TestSubsetConstraint:
    @pytest.mark.parametrize("bad", [
        (-0.1, 0.0, 0.5, 0.5),    # 左越界
        (0.0, -0.1, 0.5, 0.5),    # 上越界
        (0.6, 0.0, 0.5, 0.5),     # 右越界
        (0.0, 0.6, 0.5, 0.5),     # 下越界
        (0.0, 0.0, 0.0, 0.5),     # 空框
    ])
    def test_out_of_bounds_rejected(self, bad):
        """区域的范围就是 OCR 范围，落点框越界直接报错而不是静默钳制"""
        with pytest.raises(ValueError, match="click_rect"):
            _region(click_rect=bad)

    def test_full_region_is_legal(self):
        assert _region(click_rect=(0, 0, 1, 1)).click_rect == (0.0, 0.0, 1.0, 1.0)

    @pytest.mark.parametrize("bad", [
        (float("nan"), 0.0, 0.5, 0.5),
        (0.0, 0.0, float("inf"), 0.5),
        (0.0, 0.0, 0.5),
        ("x", 0.0, 0.5, 0.5),
    ])
    def test_malformed_rect_rejected(self, bad):
        with pytest.raises(ValueError, match="click_rect"):
            _region(click_rect=bad)


class TestSerialization:
    def test_absent_rect_not_persisted(self):
        """未标定的区域不该在布局 JSON 里多出一个字段"""
        assert "click_rect" not in _region().to_dict()

    def test_roundtrip(self):
        r = _region(click_rect=(0.1, 0.0, 0.8, 0.45))
        d = r.to_dict()
        assert d["click_rect"] == [0.1, 0.0, 0.8, 0.45]
        assert Region.from_dict(d).click_rect == (0.1, 0.0, 0.8, 0.45)

    def test_clone_keeps_rect(self):
        r = _region(click_rect=(0.1, 0.0, 0.8, 0.45))
        assert r.clone().click_rect == r.click_rect


class _Coords(_CoordMixin):
    """把坐标换算 mixin 拼成可实例化的最小宿主"""

    def __init__(self, jitter_ratio: float):
        self._capture = MagicMock()
        self._capture.get_capture_size.return_value = (1000, 1000)
        self._layout = MagicMock()
        self._layout.get_canvas.return_value = MagicMock(
            x_ratio=0.0, y_ratio=0.0, w_ratio=1.0, h_ratio=1.0)
        self._input_sim = MagicMock()
        self._input_sim.region_jitter_ratio = jitter_ratio
        self._window_left = 0
        self._window_top = 0


class TestRegionToScreen:
    def test_derived_matches_legacy_distribution(self):
        """未标定时的落点区间必须与旧实现（中心 ± ratio×边长）逐点一致"""
        r = _region()
        coords = _Coords(0.25)
        xs = [coords._region_to_screen(r)[0] for _ in range(5000)]
        # 区域 x∈[200,600]，中间一半 = [300,500]
        assert 300 <= min(xs) and max(xs) <= 500
        assert min(xs) < 305 and max(xs) > 495

    def test_explicit_rect_confines_clicks(self):
        """标定成上半部分后，落点全部落在上半部分"""
        r = _region(click_rect=(0.0, 0.0, 1.0, 0.5))
        coords = _Coords(0.25)
        ys = [coords._region_to_screen(r)[1] for _ in range(5000)]
        # 区域 y∈[300,500]，上半 = [300,400]
        assert 300 <= min(ys) and max(ys) <= 400
        assert max(ys) > 395

    def test_no_jitter_takes_click_rect_center(self):
        """jitter=False 取的是落点框中心，不是区域中心

        未标定时派生框的中心在数学上就是区域中心（0.25 + 0.5/2 = 0.5），
        但换算多走一步乘加会引入末位浮点误差，配合 int() 截断可能差 1px。
        这点偏差在后端 ±click_random_offset 面前无意义，允许 ±1。
        """
        r = _region(click_rect=(0.0, 0.0, 1.0, 0.5))
        coords = _Coords(0.25)
        assert coords._region_to_screen(r, jitter=False) == (400, 350)
        x, y = coords._region_to_screen(_region(), jitter=False)
        assert abs(x - 400) <= 1 and abs(y - 400) <= 1

    def test_click_rect_never_escapes_region(self):
        """随便标一个贴边的框，落点也不会跑出区域"""
        r = _region(click_rect=(0.9, 0.9, 0.1, 0.1))
        coords = _Coords(0.25)
        random.seed(7)
        for _ in range(2000):
            x, y = coords._region_to_screen(r)
            assert 200 <= x <= 600 and 300 <= y <= 500
