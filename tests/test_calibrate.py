"""Grid 校准算法单元测试

通过 engine 的 _exec_calibrate 方法测试，确保与 engine 集成正确。

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
import pytest

from lvjiang.workflows.engine import WorkflowEngine
from lvjiang.workflows.grammar.ast_nodes import Calibrate
from lvjiang.core.scene_registry import Layout, Panel, CanvasConfig

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


class TestEngineCalibrate:
    """通过 engine._exec_calibrate 测试校准功能"""

    def test_image1_complete_5_rows(self, mock_engine, load_test_image):
        """完整 5 行 grid → engine 缓存 5×6 校准结果"""
        img = load_test_image("image1.png")

        # mock _capture_panel_image 返回测试图片
        with patch.object(mock_engine, '_capture_panel_image', return_value=img):
            node = Calibrate(scene="test_scene", panel="test_panel")
            mock_engine._exec_calibrate(node)

        # 验证缓存
        cal = mock_engine._panel_calibrations.get(("test_scene", "test_panel"))
        assert cal is not None
        assert cal.n_rows == 5
        assert cal.n_cols == 6
        assert cal.total_slots == 30

    def test_image2_scrolled_half_row(self, mock_engine, load_test_image):
        """首行半截 → engine 缓存 4×6 校准结果"""
        img = load_test_image("image2.png")

        with patch.object(mock_engine, '_capture_panel_image', return_value=img):
            node = Calibrate(scene="test_scene", panel="test_panel")
            mock_engine._exec_calibrate(node)

        cal = mock_engine._panel_calibrations.get(("test_scene", "test_panel"))
        assert cal is not None
        assert cal.n_rows == 4
        assert cal.n_cols == 6
        assert cal.total_slots == 24

    def test_image3_scrolled_quarter_visible(self, mock_engine, load_test_image):
        """首行 1/4 可见 → engine 缓存 4×6 校准结果"""
        img = load_test_image("image3.png")

        with patch.object(mock_engine, '_capture_panel_image', return_value=img):
            node = Calibrate(scene="test_scene", panel="test_panel")
            mock_engine._exec_calibrate(node)

        cal = mock_engine._panel_calibrations.get(("test_scene", "test_panel"))
        assert cal is not None
        assert cal.n_rows == 4
        assert cal.n_cols == 6
        assert cal.total_slots == 24

    def test_image4_scrolled_three_quarter_visible(self, mock_engine, load_test_image):
        """首行 3/4 可见 → engine 缓存 4×6 校准结果（< 95% 不可靠）"""
        img = load_test_image("image4.png")

        with patch.object(mock_engine, '_capture_panel_image', return_value=img):
            node = Calibrate(scene="test_scene", panel="test_panel")
            mock_engine._exec_calibrate(node)

        cal = mock_engine._panel_calibrations.get(("test_scene", "test_panel"))
        assert cal is not None
        assert cal.n_rows == 4
        assert cal.n_cols == 6
        assert cal.total_slots == 24

    def test_image5_sparse_last_row(self, mock_engine, load_test_image):
        """首行半截 + 最后一行稀疏 → engine 缓存 4×6 校准结果"""
        img = load_test_image("image5.png")

        with patch.object(mock_engine, '_capture_panel_image', return_value=img):
            node = Calibrate(scene="test_scene", panel="test_panel")
            mock_engine._exec_calibrate(node)

        cal = mock_engine._panel_calibrations.get(("test_scene", "test_panel"))
        assert cal is not None
        assert cal.n_rows == 4
        assert cal.n_cols == 6

    def test_image6_complete_with_noise(self, mock_engine, load_test_image):
        """完整 5 行（有列噪声）→ engine 缓存 5×6 校准结果"""
        img = load_test_image("image6.png")

        with patch.object(mock_engine, '_capture_panel_image', return_value=img):
            node = Calibrate(scene="test_scene", panel="test_panel")
            mock_engine._exec_calibrate(node)

        cal = mock_engine._panel_calibrations.get(("test_scene", "test_panel"))
        assert cal is not None
        assert cal.n_rows == 5
        assert cal.n_cols == 6


class TestEngineCalibrateBounds:
    """测试 engine 缓存的校准结果边界"""

    def test_image1_bounds_cover_full_grid(self, mock_engine, load_test_image):
        """完整 grid 的边界应覆盖大部分面板"""
        img = load_test_image("image1.png")

        with patch.object(mock_engine, '_capture_panel_image', return_value=img):
            node = Calibrate(scene="test_scene", panel="test_panel")
            mock_engine._exec_calibrate(node)

        cal = mock_engine._panel_calibrations.get(("test_scene", "test_panel"))
        assert cal is not None
        # 首边界应接近 0（有 span 边框）
        assert cal.row_bounds[0] < 0.05
        # 尾边界应接近 1
        assert cal.row_bounds[-1] > 0.95

    def test_image2_bounds_exclude_partial_row(self, mock_engine, load_test_image):
        """滚动 grid 的边界应排除半截行区域"""
        img = load_test_image("image2.png")

        with patch.object(mock_engine, '_capture_panel_image', return_value=img):
            node = Calibrate(scene="test_scene", panel="test_panel")
            mock_engine._exec_calibrate(node)

        cal = mock_engine._panel_calibrations.get(("test_scene", "test_panel"))
        assert cal is not None
        # 首边界应远离 0（半截行被排除）
        assert cal.row_bounds[0] > 0.10
        # 尾边界应远离 1
        assert cal.row_bounds[-1] < 0.95


class TestEngineSlotAccess:
    """测试通过 engine 校准结果访问 slot"""

    def test_slot_center_returns_correct_format(self, mock_engine, load_test_image):
        """slot_center 应返回 (cx, cy) 归一化坐标"""
        img = load_test_image("image1.png")

        with patch.object(mock_engine, '_capture_panel_image', return_value=img):
            node = Calibrate(scene="test_scene", panel="test_panel")
            mock_engine._exec_calibrate(node)

        cal = mock_engine._panel_calibrations.get(("test_scene", "test_panel"))
        assert cal is not None
        cx, cy = cal.slot_center(0, 0)
        assert 0 < cx < 1
        assert 0 < cy < 1

    def test_slot_bounds_returns_correct_format(self, mock_engine, load_test_image):
        """slot_bounds 应返回 (x1, y1, x2, y2) 归一化坐标"""
        img = load_test_image("image1.png")

        with patch.object(mock_engine, '_capture_panel_image', return_value=img):
            node = Calibrate(scene="test_scene", panel="test_panel")
            mock_engine._exec_calibrate(node)

        cal = mock_engine._panel_calibrations.get(("test_scene", "test_panel"))
        assert cal is not None
        x1, y1, x2, y2 = cal.slot_bounds(0, 0)
        assert 0 <= x1 < x2 <= 1
        assert 0 <= y1 < y2 <= 1

    def test_slot_bounds_consistent_with_centers(self, mock_engine, load_test_image):
        """slot 中心应在边界范围内"""
        img = load_test_image("image1.png")

        with patch.object(mock_engine, '_capture_panel_image', return_value=img):
            node = Calibrate(scene="test_scene", panel="test_panel")
            mock_engine._exec_calibrate(node)

        cal = mock_engine._panel_calibrations.get(("test_scene", "test_panel"))
        assert cal is not None
        for r in range(cal.n_rows):
            for c in range(cal.n_cols):
                cx, cy = cal.slot_center(r, c)
                x1, y1, x2, y2 = cal.slot_bounds(r, c)
                assert x1 < cx < x2, f"row={r}, col={c}: cx={cx} not in ({x1}, {x2})"
                assert y1 < cy < y2, f"row={r}, col={c}: cy={cy} not in ({y1}, {y2})"
