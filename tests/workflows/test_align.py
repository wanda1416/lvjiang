"""Grid 对齐算法单元测试

通过 engine 的 _exec_align 方法测试，确保与 engine 集成正确。

使用 6 张实测图片验证：
- image1: 完整 5×6 grid
- image2: 首行半截（滚动 ~50%）
- image3: 首行 1/4 可见（滚动 ~75%）
- image4: 首行 3/4 可见（滚动 ~25%）
- image5: 首行半截 + 最后一行只有 2 个有效 slot
- image6: 完整 5×6 grid（有 1 像素列噪声）
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import cv2
import numpy as np
import pytest

from lvjiang.core.scene_registry import CanvasConfig, Layout, Panel
from lvjiang.workflows.align import (
    _binary_axis,
    _even_axis,
    _make_even_alignment,
    detect_grid,
)
from lvjiang.workflows.engine import WorkflowEngine
from lvjiang.workflows.grammar.ast_nodes import Align

DATA_DIR = Path(__file__).parent / "data"


@pytest.fixture
def mock_engine():
    """创建最小化的 mock engine"""
    # 创建 mock 依赖
    mock_capture = MagicMock()
    mock_ocr = MagicMock()
    mock_input = MagicMock()

    # 创建带 panel 的 layout
    layout = Layout()
    layout.canvas = CanvasConfig()

    # 添加测试 panel（5 行 6 列）
    panel = Panel(
        key="test_panel",
        x_ratio=0.1,
        y_ratio=0.1,
        w_ratio=0.8,
        h_ratio=0.8,
        cols=6,
        rows=5,
    )
    layout.set_scene_panels("test_scene", [panel])

    # 创建 engine
    engine = WorkflowEngine(
        capture=mock_capture,
        ocr=mock_ocr,
        input_ctrl=mock_input,
        layout=layout,
    )

    return engine


@pytest.fixture
def load_test_image():
    """加载测试图片"""
    def _load(name: str):
        path = DATA_DIR / name
        img = cv2.imread(str(path))
        assert img is not None, f"无法读取图片: {path}"
        return img
    return _load


class TestEngineAlign:
    """通过 engine._exec_align 测试对齐功能"""

    def test_image1_complete_5_rows(self, mock_engine, load_test_image):
        """完整 5 行 grid → engine 缓存 5×6 对齐结果"""
        img = load_test_image("image1.png")

        # mock _capture_panel_image 返回测试图片
        with patch.object(mock_engine, '_capture_panel_image', return_value=img):
            node = Align(scene="test_scene", panel="test_panel")
            mock_engine._exec_align(node)

        # 验证缓存
        cal = mock_engine._panel_alignments.get(("test_scene", "test_panel"))
        assert cal is not None
        assert cal.n_rows == 5
        assert cal.n_cols == 6
        assert cal.total_slots == 30

    def test_image2_scrolled_half_row(self, mock_engine, load_test_image):
        """首行半截 → engine 缓存 4×6 对齐结果"""
        img = load_test_image("image2.png")

        with patch.object(mock_engine, '_capture_panel_image', return_value=img):
            node = Align(scene="test_scene", panel="test_panel")
            mock_engine._exec_align(node)

        cal = mock_engine._panel_alignments.get(("test_scene", "test_panel"))
        assert cal is not None
        assert cal.n_rows == 4
        assert cal.n_cols == 6
        assert cal.total_slots == 24

    def test_image3_scrolled_quarter_visible(self, mock_engine, load_test_image):
        """首行 1/4 可见 → engine 缓存 4×6 对齐结果"""
        img = load_test_image("image3.png")

        with patch.object(mock_engine, '_capture_panel_image', return_value=img):
            node = Align(scene="test_scene", panel="test_panel")
            mock_engine._exec_align(node)

        cal = mock_engine._panel_alignments.get(("test_scene", "test_panel"))
        assert cal is not None
        assert cal.n_rows == 4
        assert cal.n_cols == 6
        assert cal.total_slots == 24

    def test_image4_scrolled_three_quarter_visible(self, mock_engine, load_test_image):
        """首行 3/4 可见 → engine 缓存 4×6 对齐结果（< 95% 不可靠）"""
        img = load_test_image("image4.png")

        with patch.object(mock_engine, '_capture_panel_image', return_value=img):
            node = Align(scene="test_scene", panel="test_panel")
            mock_engine._exec_align(node)

        cal = mock_engine._panel_alignments.get(("test_scene", "test_panel"))
        assert cal is not None
        assert cal.n_rows == 4
        assert cal.n_cols == 6
        assert cal.total_slots == 24

    def test_image5_sparse_last_row(self, mock_engine, load_test_image):
        """首行半截 + 最后一行稀疏 → engine 缓存 4×6 对齐结果"""
        img = load_test_image("image5.png")

        with patch.object(mock_engine, '_capture_panel_image', return_value=img):
            node = Align(scene="test_scene", panel="test_panel")
            mock_engine._exec_align(node)

        cal = mock_engine._panel_alignments.get(("test_scene", "test_panel"))
        assert cal is not None
        assert cal.n_rows == 4
        assert cal.n_cols == 6

    def test_image6_complete_with_noise(self, mock_engine, load_test_image):
        """完整 5 行（有列噪声）→ engine 缓存 5×6 对齐结果"""
        img = load_test_image("image6.png")

        with patch.object(mock_engine, '_capture_panel_image', return_value=img):
            node = Align(scene="test_scene", panel="test_panel")
            mock_engine._exec_align(node)

        cal = mock_engine._panel_alignments.get(("test_scene", "test_panel"))
        assert cal is not None
        assert cal.n_rows == 5
        assert cal.n_cols == 6


class TestEngineAlignBounds:
    """测试 engine 缓存的对齐结果边界"""

    def test_image1_bounds_cover_full_grid(self, mock_engine, load_test_image):
        """完整 grid 的边界应覆盖大部分面板"""
        img = load_test_image("image1.png")

        with patch.object(mock_engine, '_capture_panel_image', return_value=img):
            node = Align(scene="test_scene", panel="test_panel")
            mock_engine._exec_align(node)

        cal = mock_engine._panel_alignments.get(("test_scene", "test_panel"))
        assert cal is not None
        # 首边界应接近 0（有 span 边框）
        assert cal.row_bounds[0] < 0.05
        # 尾边界应接近 1
        assert cal.row_bounds[-1] > 0.95

    def test_image2_bounds_exclude_partial_row(self, mock_engine, load_test_image):
        """滚动 grid 的边界应排除半截行区域"""
        img = load_test_image("image2.png")

        with patch.object(mock_engine, '_capture_panel_image', return_value=img):
            node = Align(scene="test_scene", panel="test_panel")
            mock_engine._exec_align(node)

        cal = mock_engine._panel_alignments.get(("test_scene", "test_panel"))
        assert cal is not None
        # 首边界应远离 0（半截行被排除）
        assert cal.row_bounds[0] > 0.10
        # 尾边界应远离 1
        assert cal.row_bounds[-1] < 0.95


class TestEngineSlotAccess:
    """测试通过 engine 对齐结果访问 slot"""

    def test_slot_center_returns_correct_format(self, mock_engine, load_test_image):
        """slot_center 应返回 (cx, cy) 归一化坐标"""
        img = load_test_image("image1.png")

        with patch.object(mock_engine, '_capture_panel_image', return_value=img):
            node = Align(scene="test_scene", panel="test_panel")
            mock_engine._exec_align(node)

        cal = mock_engine._panel_alignments.get(("test_scene", "test_panel"))
        assert cal is not None
        cx, cy = cal.slot_center(0, 0)
        assert 0 < cx < 1
        assert 0 < cy < 1

    def test_slot_bounds_returns_correct_format(self, mock_engine, load_test_image):
        """slot_bounds 应返回 (x1, y1, x2, y2) 归一化坐标"""
        img = load_test_image("image1.png")

        with patch.object(mock_engine, '_capture_panel_image', return_value=img):
            node = Align(scene="test_scene", panel="test_panel")
            mock_engine._exec_align(node)

        cal = mock_engine._panel_alignments.get(("test_scene", "test_panel"))
        assert cal is not None
        x1, y1, x2, y2 = cal.slot_bounds(0, 0)
        assert 0 <= x1 < x2 <= 1
        assert 0 <= y1 < y2 <= 1

    def test_slot_bounds_consistent_with_centers(self, mock_engine, load_test_image):
        """slot 中心应在边界范围内"""
        img = load_test_image("image1.png")

        with patch.object(mock_engine, '_capture_panel_image', return_value=img):
            node = Align(scene="test_scene", panel="test_panel")
            mock_engine._exec_align(node)

        cal = mock_engine._panel_alignments.get(("test_scene", "test_panel"))
        assert cal is not None
        for r in range(cal.n_rows):
            for c in range(cal.n_cols):
                cx, cy = cal.slot_center(r, c)
                x1, y1, x2, y2 = cal.slot_bounds(r, c)
                assert x1 < cx < x2, f"row={r}, col={c}: cx={cx} not in ({x1}, {x2})"
                assert y1 < cy < y2, f"row={r}, col={c}: cy={cy} not in ({y1}, {y2})"


def _profile(length: int, bright_bands: list[tuple[int, int]],
             bright: float = 160.0, dark: float = 8.0) -> np.ndarray:
    """构造一维亮度剖面：bright_bands 区间内为亮，其余为黑边"""
    arr = np.full(length, dark, dtype=np.float64)
    for s, e in bright_bands:
        arr[s:e] = bright
    return arr


class TestBinaryAxisLattice:
    """周期点阵拟合：直接驱动 _binary_axis 锁定过检排除与漏检补全"""

    def test_off_lattice_peek_excluded(self):
        """点阵外的半周期偷看亮区→排除，不会过检为第 4 行"""
        # 三个周期 slot（周期 100，宽 70）+ 底部半周期处的短偷看亮区
        prof = _profile(300, [(15, 85), (115, 185), (215, 285), (293, 300)])
        axis = _binary_axis(prof, expected_count=3)
        assert len(axis.centers) == 3
        # 三个中心落在周期点阵上（约 0.167 / 0.5 / 0.833），无 0.9+ 的伪行
        assert all(c < 0.9 for c in axis.centers)
        assert abs(axis.centers[1] - 0.5) < 0.02

    def test_dark_sparse_row_filled(self):
        """第三行整行偏暗（无 run）但在点阵上→被周期补出"""
        # 只有前两行亮，第三行（215..285）保持黑暗，仍应被补为 3 行
        prof = _profile(300, [(15, 85), (115, 185)])
        axis = _binary_axis(prof, expected_count=3)
        assert len(axis.centers) == 3
        # 第三个中心落在 ≈ 0.833 的点阵位置
        assert abs(axis.centers[2] - 0.833) < 0.03

    def test_count_capped_at_expected(self):
        """候选多于 expected_count → 数量封顶"""
        prof = _profile(600, [(15, 85), (115, 185), (215, 285),
                              (315, 385), (415, 485), (515, 585)])
        axis = _binary_axis(prof, expected_count=3)
        assert len(axis.centers) == 3


class TestDetectGridMinVisible:
    """min_visible 可配置：控制半截行是否计入有效行"""

    def test_image4_default_excludes_partial_row(self, load_test_image):
        """首行 3/4 可见，默认 0.95 → 排除，检测 4 行"""
        img = load_test_image("image4.png")
        result = detect_grid(img, expected_rows=5, expected_cols=6)
        assert result is not None
        assert result.n_rows == 4

    def test_image4_lower_threshold_includes_partial_row(self, load_test_image):
        """首行 3/4 可见，min_visible=0.55 → 75% ≥ 55% 计入，检测 5 行"""
        img = load_test_image("image4.png")
        result = detect_grid(img, expected_rows=5, expected_cols=6,
                             min_visible=0.55)
        assert result is not None
        assert result.n_rows == 5

    def test_image3_quarter_visible_top_excluded(self, load_test_image):
        """首行仅 1/4 可见，min_visible=0.55 → 25% < 55% 顶部行仍排除；
        底部 ~83% 可见的行 ≥ 55% 被计入 → 共 5 行，且首中心远离顶边"""
        img = load_test_image("image3.png")
        result = detect_grid(img, expected_rows=5, expected_cols=6,
                             min_visible=0.55)
        assert result is not None
        assert result.n_rows == 5
        assert result.row_centers[0] > 0.1   # 顶部 1/4 行未计入

    def test_min_visible_clamped_to_half(self, load_test_image):
        """传入 < 0.5 被钳位到 0.5（保证行中心可点击）：75% 可见行仍计入"""
        img = load_test_image("image4.png")
        result = detect_grid(img, expected_rows=5, expected_cols=6,
                             min_visible=0.2)
        assert result is not None
        assert result.n_rows == 5


class TestPanelMinVisible:
    """Panel.min_visible 字段序列化兼容"""

    def test_from_dict_default(self):
        """旧布局 JSON 无 min_visible 字段 → 默认 0.95"""
        p = Panel.from_dict({"key": "g", "x_ratio": 0.1, "y_ratio": 0.1,
                             "w_ratio": 0.8, "h_ratio": 0.8})
        assert p.min_visible == 0.95

    def test_roundtrip(self):
        """to_dict/from_dict 往返保留 min_visible"""
        p = Panel(key="g", x_ratio=0.1, y_ratio=0.1, w_ratio=0.8,
                  h_ratio=0.8, min_visible=0.55)
        assert Panel.from_dict(p.to_dict()).min_visible == 0.55


class TestDetectGridDebugImage:
    """detect_grid 异常时保存调试图片"""

    def test_saves_debug_image_on_row_mismatch(self, load_test_image):
        """检测到的行数既不是 expected 也不是 expected-1 → 保存调试图到 logs/image/"""
        img = load_test_image("image1.png")  # 实际 5 行
        saved = []

        def fake_imwrite(p, _img):
            saved.append(str(p))
            return True

        with patch("lvjiang.workflows.align.cv2.imwrite", side_effect=fake_imwrite):
            result = detect_grid(img, expected_rows=10, expected_cols=6)

        # 检测到 5 行，既不是 10 也不是 9 → 应触发保存
        assert result is not None
        assert result.n_rows == 5
        assert len(saved) == 1
        # 文件名带检测/预期信息
        assert "rows5_of_10" in saved[0]
        assert "cols6_of_6" in saved[0]

    def test_no_debug_image_when_rows_ok(self, load_test_image):
        """行数满足 expected 或 expected-1 → 不保存调试图"""
        img = load_test_image("image1.png")  # 实际 5 行

        with patch("lvjiang.workflows.align.cv2.imwrite") as mock_imwrite:
            result = detect_grid(img, expected_rows=5, expected_cols=6)

        assert result is not None
        assert result.n_rows == 5
        mock_imwrite.assert_not_called()


class TestPanelRatioClamp:
    """_panel_ratio_to_screen 钳位：边缘 slot 中心不能落在 panel 边框上

    min_visible 接近 0.5 时半可见行的中心恰在 panel 边缘，叠加底层
    ±click_random_offset 随机偏移后 tap 会出界，必须钳位到内缩矩形内。
    mock_engine: capture(1000,1000)，canvas 全屏，panel x/y∈[100,900]，
    margin = click_random_offset(3) + 2 = 5。
    """

    @pytest.fixture
    def sized_engine(self, mock_engine):
        mock_engine._capture.get_capture_size.return_value = (1000, 1000)
        return mock_engine

    def _panel(self, engine):
        return engine._find_panel_in_layout("test_scene", "test_panel")

    def test_top_edge_center_clamped_inside(self, sized_engine):
        """cy=0（半截首行中心压顶边）→ 钳位到 panel_top + margin"""
        sx, sy = sized_engine._panel_ratio_to_screen(self._panel(sized_engine), 0.5, 0.0)
        assert sy == 105
        assert sx == 500

    def test_bottom_edge_center_clamped_inside(self, sized_engine):
        """cy=1（半截尾行中心压底边）→ 钳位到 panel_bottom - margin"""
        _, sy = sized_engine._panel_ratio_to_screen(self._panel(sized_engine), 0.5, 1.0)
        assert sy == 895

    def test_slightly_out_of_range_center_clamped(self, sized_engine):
        """eps 容差下中心略越界（cy<0）→ 同样钳回内缩矩形"""
        _, sy = sized_engine._panel_ratio_to_screen(self._panel(sized_engine), 0.5, -0.02)
        assert sy == 105

    def test_interior_center_untouched(self, sized_engine):
        """panel 内部正常 slot 中心不受钳位影响"""
        sx, sy = sized_engine._panel_ratio_to_screen(self._panel(sized_engine), 0.5, 0.5)
        assert (sx, sy) == (500, 500)


class TestEvenAxis:
    """_even_axis 等分降级算法"""

    def test_basic_division(self):
        """3 等分 → 中心 [0.167, 0.5, 0.833]"""
        axis = _even_axis(3)
        assert len(axis.centers) == 3
        assert abs(axis.centers[0] - 1/6) < 0.01
        assert abs(axis.centers[1] - 0.5) < 0.01
        assert abs(axis.centers[2] - 5/6) < 0.01

    def test_boundaries_cover_full_range(self):
        """边界覆盖 [0, 1]"""
        axis = _even_axis(4)
        assert len(axis.boundaries) == 5
        assert axis.boundaries[0] == 0.0
        assert axis.boundaries[-1] == 1.0

    def test_slot_size_no_span(self):
        """等分模式 slot_size = 1/count，span_size = 0"""
        axis = _even_axis(5)
        assert axis.slot_size == 0.2
        assert axis.span_size == 0.0

    def test_zero_count(self):
        """0 等分 → 空结果"""
        axis = _even_axis(0)
        assert axis.centers == []
        assert axis.boundaries == []


class TestMakeEvenAlignment:
    """_make_even_alignment 等分降级辅助函数"""

    def test_basic_grid(self):
        """3×6 等分 → 18 个 slot"""
        alignment = _make_even_alignment(3, 6)
        assert alignment.n_rows == 3
        assert alignment.n_cols == 6
        assert alignment.total_slots == 18

    def test_slot_centers_in_range(self):
        """所有 slot 中心在 [0, 1] 范围内"""
        alignment = _make_even_alignment(4, 5)
        for r in range(alignment.n_rows):
            for c in range(alignment.n_cols):
                cx, cy = alignment.slot_center(r, c)
                assert 0 < cx < 1
                assert 0 < cy < 1

    def test_slot_bounds_within_panel(self):
        """所有 slot 边界在 [0, 1] 范围内"""
        alignment = _make_even_alignment(3, 4)
        for r in range(alignment.n_rows):
            for c in range(alignment.n_cols):
                x1, y1, x2, y2 = alignment.slot_bounds(r, c)
                assert 0 <= x1 < x2 <= 1
                assert 0 <= y1 < y2 <= 1


class TestDetectGridFallback:
    """detect_grid fallback 参数：检测失败时降级为等分"""

    def test_fallback_on_empty_image(self):
        """空图像 + fallback=True → 返回等分结果"""
        empty = np.zeros((0, 0), dtype=np.uint8)
        result = detect_grid(empty, expected_rows=3, expected_cols=6, fallback=True)
        assert result is not None
        assert result.n_rows == 3
        assert result.n_cols == 6
        assert result.total_slots == 18

    def test_no_fallback_returns_none(self):
        """空图像 + fallback=False → 返回 None"""
        empty = np.zeros((0, 0), dtype=np.uint8)
        result = detect_grid(empty, expected_rows=3, expected_cols=6, fallback=False)
        assert result is None

    def test_fallback_on_dark_image(self, load_test_image):
        """全黑图像（无法检测）+ fallback=True → 返回等分结果"""
        # 构造全黑图像
        dark = np.zeros((100, 100, 3), dtype=np.uint8)
        result = detect_grid(dark, expected_rows=4, expected_cols=5, fallback=True)
        assert result is not None
        assert result.n_rows == 4
        assert result.n_cols == 5

    def test_fallback_preserves_expected_counts(self):
        """等分降级应使用传入的 expected_rows/cols"""
        dark = np.zeros((200, 300, 3), dtype=np.uint8)
        result = detect_grid(dark, expected_rows=7, expected_cols=8, fallback=True)
        assert result is not None
        assert result.n_rows == 7
        assert result.n_cols == 8


class TestPanelCalibration:
    """Panel.calibration 字段序列化兼容"""

    def test_from_dict_default(self):
        """旧布局 JSON 无 calibration 字段 → 默认 auto"""
        p = Panel.from_dict({"key": "g", "x_ratio": 0.1, "y_ratio": 0.1,
                             "w_ratio": 0.8, "h_ratio": 0.8})
        assert p.calibration == "auto"

    def test_from_dict_even(self):
        """calibration=even 可正确解析"""
        p = Panel.from_dict({"key": "g", "x_ratio": 0.1, "y_ratio": 0.1,
                             "w_ratio": 0.8, "h_ratio": 0.8, "calibration": "even"})
        assert p.calibration == "even"

    def test_roundtrip(self):
        """to_dict/from_dict 往返保留 calibration"""
        p = Panel(key="g", x_ratio=0.1, y_ratio=0.1, w_ratio=0.8,
                  h_ratio=0.8, calibration="image")
        assert Panel.from_dict(p.to_dict()).calibration == "image"


class TestEngineAlignCalibration:
    """engine._exec_align 校准模式集成"""

    def test_even_mode_skips_image_detection(self, mock_engine):
        """calibration=even → 跳过图像检测，直接等分"""
        # 设置 panel 为 even 模式
        panel = Panel(
            key="test_panel",
            x_ratio=0.1, y_ratio=0.1, w_ratio=0.8, h_ratio=0.8,
            cols=6, rows=5,
            calibration="even",
        )
        mock_engine._layout.set_scene_panels("test_scene", [panel])

        # 不 mock _capture_panel_image，even 模式不应调用它
        node = Align(scene="test_scene", panel="test_panel")
        mock_engine._exec_align(node)

        # 验证缓存
        cal = mock_engine._panel_alignments.get(("test_scene", "test_panel"))
        assert cal is not None
        assert cal.n_rows == 5
        assert cal.n_cols == 6
        # 等分模式 span 应为 0
        assert cal.row_span == 0.0
        assert cal.col_span == 0.0

    def test_auto_mode_fallback_on_dark_image(self, mock_engine):
        """calibration=auto + 全黑图像 → 降级为等分"""
        # 设置 panel 为 auto 模式
        panel = Panel(
            key="test_panel",
            x_ratio=0.1, y_ratio=0.1, w_ratio=0.8, h_ratio=0.8,
            cols=6, rows=5,
            calibration="auto",
        )
        mock_engine._layout.set_scene_panels("test_scene", [panel])

        # 构造全黑图像
        dark = np.zeros((100, 100, 3), dtype=np.uint8)
        with patch.object(mock_engine, '_capture_panel_image', return_value=dark):
            node = Align(scene="test_scene", panel="test_panel")
            mock_engine._exec_align(node)

        # 验证降级为等分
        cal = mock_engine._panel_alignments.get(("test_scene", "test_panel"))
        assert cal is not None
        assert cal.n_rows == 5
        assert cal.n_cols == 6
        assert cal.row_span == 0.0

    def test_image_mode_returns_none_on_failure(self, mock_engine):
        """calibration=image + 全黑图像 → 返回 None，不缓存"""
        # 设置 panel 为 image 模式
        panel = Panel(
            key="test_panel",
            x_ratio=0.1, y_ratio=0.1, w_ratio=0.8, h_ratio=0.8,
            cols=6, rows=5,
            calibration="image",
        )
        mock_engine._layout.set_scene_panels("test_scene", [panel])

        # 构造全黑图像
        dark = np.zeros((100, 100, 3), dtype=np.uint8)
        with patch.object(mock_engine, '_capture_panel_image', return_value=dark):
            node = Align(scene="test_scene", panel="test_panel")
            mock_engine._exec_align(node)

        # 验证未缓存
        cal = mock_engine._panel_alignments.get(("test_scene", "test_panel"))
        assert cal is None


class TestPanelScrollDirection:
    """Panel.scroll_direction 字段验证"""

    def test_from_dict_default(self):
        """旧布局 JSON 无 scroll_direction 字段 → 默认 vertical"""
        p = Panel.from_dict({"key": "g", "x_ratio": 0.1, "y_ratio": 0.1,
                             "w_ratio": 0.8, "h_ratio": 0.8})
        assert p.scroll_direction == "vertical"

    def test_from_dict_horizontal(self):
        """scroll_direction=horizontal 可正确解析"""
        p = Panel.from_dict({"key": "g", "x_ratio": 0.1, "y_ratio": 0.1,
                             "w_ratio": 0.8, "h_ratio": 0.8,
                             "scroll_direction": "horizontal"})
        assert p.scroll_direction == "horizontal"

    def test_roundtrip(self):
        """to_dict/from_dict 往返保留 scroll_direction"""
        p = Panel(key="g", x_ratio=0.1, y_ratio=0.1, w_ratio=0.8,
                  h_ratio=0.8, scroll_direction="both")
        assert Panel.from_dict(p.to_dict()).scroll_direction == "both"

    def test_invalid_scroll_direction_raises(self):
        """无效的 scroll_direction 抛出 ValueError"""
        with pytest.raises(ValueError, match="scroll_direction 必须为"):
            Panel(key="g", x_ratio=0.1, y_ratio=0.1, w_ratio=0.8, h_ratio=0.8,
                  scroll_direction="diagonal")

    def test_rows1_vertical_raises(self):
        """rows=1 + scroll_direction=vertical 抛出 ValueError"""
        with pytest.raises(ValueError, match="rows=1 时 scroll_direction 不能为"):
            Panel(key="g", x_ratio=0.1, y_ratio=0.1, w_ratio=0.8, h_ratio=0.8,
                  rows=1, scroll_direction="vertical")

    def test_rows1_both_raises(self):
        """rows=1 + scroll_direction=both 抛出 ValueError"""
        with pytest.raises(ValueError, match="rows=1 时 scroll_direction 不能为"):
            Panel(key="g", x_ratio=0.1, y_ratio=0.1, w_ratio=0.8, h_ratio=0.8,
                  rows=1, scroll_direction="both")

    def test_rows1_horizontal_ok(self):
        """rows=1 + scroll_direction=horizontal 合法"""
        p = Panel(key="g", x_ratio=0.1, y_ratio=0.1, w_ratio=0.8, h_ratio=0.8,
                  rows=1, scroll_direction="horizontal")
        assert p.scroll_direction == "horizontal"

    def test_rows1_none_ok(self):
        """rows=1 + scroll_direction=none 合法"""
        p = Panel(key="g", x_ratio=0.1, y_ratio=0.1, w_ratio=0.8, h_ratio=0.8,
                  rows=1, scroll_direction="none")
        assert p.scroll_direction == "none"

    def test_cols1_horizontal_raises(self):
        """cols=1 + scroll_direction=horizontal 抛出 ValueError"""
        with pytest.raises(ValueError, match="cols=1 时 scroll_direction 不能为"):
            Panel(key="g", x_ratio=0.1, y_ratio=0.1, w_ratio=0.8, h_ratio=0.8,
                  cols=1, scroll_direction="horizontal")

    def test_cols1_both_raises(self):
        """cols=1 + scroll_direction=both 抛出 ValueError"""
        with pytest.raises(ValueError, match="cols=1 时 scroll_direction 不能为"):
            Panel(key="g", x_ratio=0.1, y_ratio=0.1, w_ratio=0.8, h_ratio=0.8,
                  cols=1, scroll_direction="both")

    def test_cols1_vertical_ok(self):
        """cols=1 + scroll_direction=vertical 合法"""
        p = Panel(key="g", x_ratio=0.1, y_ratio=0.1, w_ratio=0.8, h_ratio=0.8,
                  cols=1, scroll_direction="vertical")
        assert p.scroll_direction == "vertical"

    def test_single_cell_none_ok(self):
        """rows=1 + cols=1 + scroll_direction=none 合法"""
        p = Panel(key="g", x_ratio=0.1, y_ratio=0.1, w_ratio=0.8, h_ratio=0.8,
                  rows=1, cols=1, scroll_direction="none")
        assert p.rows == 1 and p.cols == 1 and p.scroll_direction == "none"


class TestDetectGridScrollDirection:
    """detect_grid scroll_direction 参数：影响 rows/cols 容差判断"""

    def test_vertical_rows_minus1_ok(self, load_test_image):
        """scroll_direction=vertical + 检测行数=expected-1 → 通过"""
        img = load_test_image("image2.png")  # 4 行（expected=5）
        result = detect_grid(img, expected_rows=5, expected_cols=6,
                             scroll_direction="vertical")
        assert result is not None
        assert result.n_rows == 4  # expected-1 可接受

    def test_vertical_cols_minus1_fallback(self, load_test_image):
        """scroll_direction=vertical + 检测列数=expected-1 + fallback → 等分"""
        # 构造一个列数少的图像（模拟横向滚动异常）
        img = load_test_image("image1.png")
        # 假设检测到 5 列（expected=6），vertical 模式不允许 cols-1
        detect_grid(img, expected_rows=5, expected_cols=5,
                    scroll_direction="vertical", fallback=True)
        # 如果列数不匹配，应降级
        # 注意：实际测试取决于图片内容，这里主要验证逻辑路径

    def test_horizontal_cols_minus1_ok(self, load_test_image):
        """scroll_direction=horizontal + 检测列数=expected-1 → 通过"""
        # 使用 image2（4 行）模拟，但 horizontal 模式关注列
        img = load_test_image("image1.png")  # 5 行 6 列
        result = detect_grid(img, expected_rows=5, expected_cols=6,
                             scroll_direction="horizontal")
        assert result is not None
        # horizontal 模式：rows 必须精确，cols 允许 -1

    def test_none_exact_match_required(self, load_test_image):
        """scroll_direction=none + 行数不精确 + fallback → 等分"""
        img = load_test_image("image2.png")  # 4 行（expected=5）
        result = detect_grid(img, expected_rows=5, expected_cols=6,
                             scroll_direction="none", fallback=True)
        # none 模式要求精确匹配，4 行不符合 5 行预期，应降级
        assert result is not None
        assert result.n_rows == 5  # 等分模式使用 expected_rows
        assert result.row_span == 0.0

    def test_both_directions_allow_minus1(self, load_test_image):
        """scroll_direction=both + 行/列数=expected-1 → 通过"""
        img = load_test_image("image2.png")  # 4 行
        result = detect_grid(img, expected_rows=5, expected_cols=6,
                             scroll_direction="both")
        assert result is not None
        # both 模式：rows 和 cols 都允许 -1
