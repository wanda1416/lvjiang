"""背包物品识别回归测试

使用真实截图验证 MaterialRecognizer 对背包格子的识别准确率。
验证内容：材料名称 + 等级（忽略数量）。

截图来源：config/session/screenshots/默认布局/bag_item_detail__bag_detail.png
标准结果：人工标注的 30 个格子（5 行 × 6 列）

注意：依赖 RapidOCR（ONNX Runtime），CI 环境可能不支持，仅本地运行。
"""

import os
from pathlib import Path

import cv2
import numpy as np
import pytest

from lvjiang.apps.yysls.core.material_recognizer import MaterialRecognizer
from lvjiang.core.layout_models import Region
from lvjiang.core.ocr import OCREngine

# CI 环境（GitHub Actions 等）可能不支持 ONNX Runtime，跳过整组测试
pytestmark = pytest.mark.skipif(
    os.environ.get("CI") == "true",
    reason="背包识别测试依赖 RapidOCR，CI 环境不支持 ONNX Runtime",
)

# ─── 测试数据 ──────────────────────────────────────────────

# 截图路径
SCREENSHOT_PATH = Path(__file__).parent / "data" / "bag_item_detail__bag_detail.png"

# 标准结果：{(row, col): (material_name, level)}
# row: 1-5, col: 1-6
# level: None 表示无等级标识
EXPECTED_RESULTS = {
    # 第 1 行
    (1, 1): ("大律准石", 86),
    (1, 2): ("大律准石", 81),
    (1, 3): ("大律准石", 71),
    (1, 4): ("大律准石", 61),
    (1, 5): ("大律准石", 56),
    (1, 6): ("大律准石", 51),
    # 第 2 行
    (2, 1): ("大律准石", None),
    (2, 2): ("溯玉", None),
    (2, 3): ("律准石", None),
    (2, 4): ("振玉", None),
    (2, 5): ("小律准石", 96),
    (2, 6): ("小律准石", 71),
    # 第 3 行
    (3, 1): ("小律准石", None),
    (3, 2): ("小律准石", 56),
    (3, 3): ("小律准石", 51),
    (3, 4): ("彩狗粮", 110),
    (3, 5): ("彩狗粮", 105),
    (3, 6): ("金狗粮", 105),
    # 第 4 行
    (4, 1): ("彩狗粮", 100),
    (4, 2): ("金狗粮", 100),
    (4, 3): ("彩狗粮", 96),
    (4, 4): ("金狗粮", 96),
    (4, 5): ("彩狗粮", 91),
    (4, 6): ("金狗粮", 91),
    # 第 5 行
    (5, 1): ("彩狗粮", 86),
    (5, 2): ("彩狗粮", 81),
    (5, 3): ("金狗粮", 81),
    (5, 4): ("彩狗粮", 71),
    (5, 5): ("金狗粮", 71),
    (5, 6): ("金狗粮", 61),
}

# Panel key
BAG_PANEL_KEY = "bag_grid"

# 每个格子的匹配分组（默认"调律材料"，个别格子属于其他分组）
SLOT_GROUPS = {
    (2, 2): "装备培养",  # 溯玉
    (2, 4): "装备培养",  # 振玉
}
DEFAULT_GROUP = "调律材料"


# ─── Fixture ──────────────────────────────────────────────

@pytest.fixture(scope="module")
def screenshot():
    """加载测试截图"""
    if not SCREENSHOT_PATH.exists():
        pytest.skip(f"截图不存在: {SCREENSHOT_PATH}")
    # cv2.imread 不支持中文路径，用 np.fromfile + imdecode
    data = SCREENSHOT_PATH.read_bytes()
    buf = np.frombuffer(data, dtype=np.uint8)
    img = cv2.imdecode(buf, cv2.IMREAD_COLOR)
    if img is None:
        pytest.skip(f"截图加载失败: {SCREENSHOT_PATH}")
    return img


@pytest.fixture(scope="module")
def layout():
    """加载默认布局的 bag_item_detail 场景区域"""
    from lvjiang.core.layout_manager import load_layout_by_name
    layout = load_layout_by_name("默认布局")
    if layout is None:
        pytest.skip("默认布局不存在")
    return layout


@pytest.fixture(scope="module")
def recognizer():
    """创建材料识别器"""
    ocr = OCREngine()
    return MaterialRecognizer(ocr)


def crop_bag_slot(img: np.ndarray, region: Region) -> np.ndarray:
    """从截图中裁剪单个背包格子

    区域坐标是相对于整张截图的归一化坐标（canvas 默认为全图）。
    """
    h, w = img.shape[:2]
    x1 = int(region.x_ratio * w)
    y1 = int(region.y_ratio * h)
    x2 = int((region.x_ratio + region.w_ratio) * w)
    y2 = int((region.y_ratio + region.h_ratio) * h)
    crop = img[y1:y2, x1:x2]
    if crop.size == 0:
        raise ValueError(f"裁剪为空: region={region.key}")
    return crop


def crop_bag_cell(img: np.ndarray, cell_bounds: tuple[int, int, int, int]) -> np.ndarray:
    """从截图中裁剪 panel grid 的单个 cell

    cell_bounds: (x1, y1, x2, y2) 像素坐标
    """
    x1, y1, x2, y2 = cell_bounds
    crop = img[y1:y2, x1:x2]
    if crop.size == 0:
        raise ValueError(f"裁剪为空: bounds={cell_bounds}")
    return crop


# ─── 测试用例 ──────────────────────────────────────────────

class TestBagItemRecognition:
    """背包物品识别测试"""

    def test_screenshot_exists(self, screenshot):
        """截图加载成功"""
        assert screenshot is not None
        assert screenshot.shape[0] > 0 and screenshot.shape[1] > 0

    def test_layout_has_bag_panel(self, layout):
        """布局包含背包网格 panel"""
        panels = layout.get_scene_panels("bag_item_detail")
        assert len(panels) >= 1, f"Panel 数量不足: {len(panels)}"
        # 检查 bag_grid panel 是否存在
        panel_keys = {p.key for p in panels}
        assert BAG_PANEL_KEY in panel_keys, f"缺少 panel: {BAG_PANEL_KEY}"

    @pytest.mark.parametrize("row,col", [
        (1, 1), (1, 2), (1, 3), (1, 4), (1, 5), (1, 6),
        (2, 1), (2, 2), (2, 3), (2, 4), (2, 5), (2, 6),
        (3, 1), (3, 2), (3, 3), (3, 4), (3, 5), (3, 6),
        (4, 1), (4, 2), (4, 3), (4, 4), (4, 5), (4, 6),
        (5, 1), (5, 2), (5, 3), (5, 4), (5, 5), (5, 6),
    ])
    def test_bag_slot_recognition(self, screenshot, layout, recognizer, row, col):
        """单个背包格子识别：验证材料名称和等级"""
        from lvjiang.core.ocr import OCREngine

        # 获取 panel
        panels = layout.get_scene_panels("bag_item_detail")
        panel = next((p for p in panels if p.key == BAG_PANEL_KEY), None)
        assert panel is not None, f"Panel 不存在: {BAG_PANEL_KEY}"

        # 使用 OCREngine 校准 panel
        ocr = OCREngine()
        canvas = layout.get_canvas()
        cells = ocr.calibrate_panel_cells(screenshot, canvas, panel)
        assert cells, "Panel 校准失败，无有效 cell"

        # 获取 cell 像素坐标 (row, col 是 1-based，cells 按 row-major 顺序)
        cell_idx = (row - 1) * panel.cols + (col - 1)
        assert cell_idx < len(cells), f"Cell 索引越界: {cell_idx} >= {len(cells)}"
        cell_bounds = cells[cell_idx]

        # 裁剪格子
        slot_img = crop_bag_cell(screenshot, cell_bounds)

        # 识别（按格子限定匹配分组，避免跨组误匹配）
        group = SLOT_GROUPS.get((row, col), DEFAULT_GROUP)
        result = recognizer.recognize(slot_img, group=group)

        # 获取期望结果
        expected_name, expected_level = EXPECTED_RESULTS[(row, col)]

        # 验证材料名称
        assert result.type == expected_name, (
            f"背包格{row}_{col}: 材料名称不匹配 "
            f"(期望={expected_name!r}, 实际={result.type!r}, "
            f"置信度={result.confidence:.2f})"
        )

        # 验证等级（None 表示无等级标识，允许识别为 None）
        if expected_level is not None:
            assert result.real_level == expected_level, (
                f"背包格{row}_{col}: 等级不匹配 "
                f"(期望={expected_level}, 实际={result.real_level})"
            )
        # 如果期望为 None，实际可以是 None 或任何值（不强制验证）


class TestBagItemRecognitionSummary:
    """汇总统计测试（可选运行）"""

    def test_recognition_accuracy_summary(self, screenshot, layout, recognizer):
        """输出识别准确率汇总（用于调试和回归分析）"""
        from lvjiang.core.ocr import OCREngine

        # 获取 panel
        panels = layout.get_scene_panels("bag_item_detail")
        panel = next((p for p in panels if p.key == BAG_PANEL_KEY), None)
        assert panel is not None, f"Panel 不存在: {BAG_PANEL_KEY}"

        # 使用 OCREngine 校准 panel
        ocr = OCREngine()
        canvas = layout.get_canvas()
        cells = ocr.calibrate_panel_cells(screenshot, canvas, panel)
        assert cells, "Panel 校准失败，无有效 cell"

        correct_name = 0
        correct_level = 0
        total = len(EXPECTED_RESULTS)
        errors = []

        for (row, col), (expected_name, expected_level) in EXPECTED_RESULTS.items():
            # 获取 cell 像素坐标 (row, col 是 1-based，cells 按 row-major 顺序)
            cell_idx = (row - 1) * panel.cols + (col - 1)
            if cell_idx >= len(cells):
                errors.append(f"背包格{row}_{col}: Cell 索引越界")
                continue
            cell_bounds = cells[cell_idx]
            slot_img = crop_bag_cell(screenshot, cell_bounds)
            group = SLOT_GROUPS.get((row, col), DEFAULT_GROUP)
            result = recognizer.recognize(slot_img, group=group)

            name_ok = result.type == expected_name
            level_ok = (expected_level is None) or (result.real_level == expected_level)

            if name_ok:
                correct_name += 1
            if level_ok:
                correct_level += 1
            if not (name_ok and level_ok):
                errors.append(
                    f"背包格{row}_{col}: 期望={expected_name} {expected_level}级, "
                    f"实际={result.type} {result.real_level}级 (conf={result.confidence:.2f})"
                )

        name_accuracy = correct_name / total * 100
        level_accuracy = correct_level / total * 100

        print("\n识别准确率汇总:")
        print(f"  材料名称: {correct_name}/{total} ({name_accuracy:.1f}%)")
        print(f"  等级: {correct_level}/{total} ({level_accuracy:.1f}%)")
        if errors:
            print("  错误详情:")
            for e in errors:
                print(f"    {e}")

        # 设置阈值（允许一定误差）
        assert name_accuracy >= 90, f"材料名称识别率过低: {name_accuracy:.1f}%"
        assert level_accuracy >= 85, f"等级识别率过低: {level_accuracy:.1f}%"
